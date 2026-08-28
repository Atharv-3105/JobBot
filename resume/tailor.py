import os 
import logging 
import re 
from typing import Tuple, Optional, List
import difflib
from router.llm_router import llm_router
from db import crud
from db.crud import get_db
from db.models import JobStatus
from resume.parser import LatexParser
from resume.compiler import compile_pdf
from resume import skill_normalizer


logger = logging.getLogger(__name__)

# FIX: Stricter prompt to prevent Summary and Experience hallucination
SYSTEM_PROMPT = r"""
You are an expert technical resume writer and ATS optimization specialist.
Your task is to tailor specific sections of a candidate's LaTeX resume for a specific job.

STRICT RULES:
1. You will receive LaTeX resume sections wrapped in XML tags (e.g., <section_0>...</section_0>).
2. You MUST return the EXACT same XML tags with the modified LaTeX inside.
3. Do NOT output markdown fences (```). Do NOT output prose or explanations. ONLY output the XML tags.

4. MODIFYING SUMMARY (CRITICAL):
   - You MUST preserve the original facts, years of experience, and core technologies mentioned in the original summary.
   - You may ONLY rephrase the summary to emphasize aspects that align with the JD's focus (e.g., if the JD wants "backend systems", emphasize the backend parts of the original summary).
   - DO NOT invent new years of experience, DO NOT add core technologies that were not in the original summary, and DO NOT fabricate metrics or achievements.
   - Keep it under 3 sentences. Preserve LaTeX formatting commands.

5. MODIFYING SKILLS (CRITICAL):
   - You will be given an ALLOWED_SKILLS list - the candidate's verified, real skills.
   - You may ONLY mention skills, tools, or technologies that appear in ALLOWED_SKILLS.
   - You may reorder/re-emphasize/rephrase existing skills to match the JD's terminology
     (e.g. if ALLOWED_SKILLS has "React" and the JD says "React.js", you may write "React.js").
   - You are STRICTLY FORBIDDEN from adding any skill, tool, framework, or technology that
     is not in ALLOWED_SKILLS, even if the JD explicitly requires it. Do NOT invent soft skills.
   - Do NOT delete original skills.
   - Preserve LaTeX formatting (like \textbf{Category:}, '\newline', etc.).

6. MODIFYING EXPERIENCE (CRITICAL): 
   - You MUST preserve the original projects, companies, and core technologies mentioned in the bullet points.
   - You may ONLY rephrase the bullet points to emphasize aspects that align with the JD.
   - DO NOT invent new projects, DO NOT replace the original technologies with JD keywords if they were not in the original bullet, and DO NOT fabricate experience.
   - Keep the exact same number of bullet points (\item).
"""

#----------------LaTeX stripping (shared logic - unescape + strip commands)---------------
def _strip_latex(text: str) -> str:
    """ 
        Function which removes LaTeX commands/escapes, returns plain readable text
    """
    if not text:
        return ""
    
    text = re.sub(r'\\([&%$#_{}~^])', r'\1', text)          # unescape \& \% etc.
    text = re.sub(r'\\[a-zA-Z*]+\{([^}]*)\}', r'\1', text)   # \textbf{X} -> X
    text = re.sub(r'\\[a-zA-Z*]+', '', text)                 # remaining bare commands
    text = re.sub(r'[{}]', '', text)                         # stray braces
    return re.sub(r'\s+', ' ', text).strip()

_NUMBER_WORD_RE = re.compile(
    r'\b(one|two|three|four|five|six|seven|eight|nine|ten|'
    r'eleven|twelve|thirteen|fourteen|fifteen|twenty|thirty|forty|fifty|'
    r'hundred|thousand|million|billion|dozen)\b',
    re.IGNORECASE
)

def _contains_quantifier(text: str) -> bool:
    """ 
        Function which returns True if text has a digit or a spelled-out number word
    """
    if not text:
        return False 
    
    return bool(re.search(r'\d', text) or _NUMBER_WORD_RE.search(text))


#----------------------Per-Section Deterministic Diff----------------------

def _get_deterministic_diff(original_text: str, tailored_text: str) -> dict:
    """ 
        Generates a deterministic, word/phrase-level diff between original and tailored tex
        Strips LaTeX commands to focus on actual content changes, prevents syntax tags
    """
    origi_clean = _strip_latex(original_text)
    tail_clean = _strip_latex(tailored_text)
    
    #We use SequenceMatcher for Block-Level phrase diff
    matcher = difflib.SequenceMatcher(None, origi_clean.split(), tail_clean.split())
    
    added_phrases,removed_phrases  = [], []
    
    
    #get_opcodes function returns a tuple which tells how to convert string a into string b
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ('replace', 'delete'):
            removed_phrases.append(" ".join(origi_clean.split()[i1:i2]))
        if tag in ('replace', 'insert'):
            added_phrases.append(" ".join(tail_clean.split()[j1:j2]))
            
    
    #Clean the punctuation and deduplicate
    added = list(set(p.strip('.,;:(){}[]\\') for p in added_phrases if re.search(r'[a-zA-Z0-9]', p)))
    removed = list(set(p.strip('.,;:(){}[]\\')for p in removed_phrases if re.search(r'[a-zA-Z0-9]', p)))
    
    if not added and not removed:
        logger.info(f"[TAILOR]: No content changes detected for this section")
    
    logger.info(f"[DEBUG] Diff added: {added} | removed: {removed}")
    return {
        "added": added, 
        "removed": removed
    }
    
def _section_label(section_type: str) -> str:
    return {
        "summary": "Summary",
        "skills": "Skills",
        "experience": "Experience"
    }.get(section_type, section_type.title())
    
def _deterministic_fallback_summary(per_section_diff: List[dict])-> str:
    """ 
        Summary built purely from diff data - used when the LLM summary call fails
        or returns empty, so the user never sees a blank section
    """
    lines = []
    for entry in per_section_diff:
        label = _section_label(entry["section_type"])
        
        if entry.get("blocked"):
            reason = entry.get("blocked_reason", "a numeric claim would have been altered")
            lines.append(f"**{label}:** No changes applied (reverted - {reason}")
            continue 
            
        added, removed = entry["added"], entry["removed"]
        if not added and not removed:
            lines.append(f"**{label}:** No changes")
            continue 
        
        parts = []
        if added:
            parts.append(f"added: {', '.join(added[:6])}")
        if removed:
            parts.append(f"removed: {', '.join(removed[:6])}")
        
        lines.append(f"**{label}:**" + "; ".join(parts))
    
    return "\n".join(lines)

    
async def _summarize_diff_with_llm(per_section_diff: List[dict], job_title: str, company: str) -> str:
    """ 
        Uses LLM to translate the deterministic diff into a short, section-labeled natural language summary,
        Falls back to a deterministic summary in case of erorr
    """
    
    section_blocks = []
    for entry in per_section_diff:
        label = _section_label(entry["section_type"])
        if entry.get("blocked"):
            reason = entry.get("blocked_reason", "this section is not to be changed")
            section_blocks.append(f"{label}: NO CHANGES ({reason})")
            continue 
    
        added_str = ", ".join(entry["added"]) if entry["added"] else "None"
        removed_str = ", ".join(entry["removed"]) if entry["removed"] else "None"
        
        section_blocks.append(f"{label}:\n Added: {added_str}\n Removed: {removed_str}")
        
    diff_text = "\n\n".join(section_blocks)
    
    prompt = f"""
                A user's resume was updated for a {job_title} role at {company}.
                Here is a deterministic, algorithmic, section-by-section list of content changes:
 
                {diff_text}
 
                Write a short summary (1 sentence per section that actually changed; skip sections
                with no changes) explaining how the resume was adapted for this role.
                STRICT RULE: Do NOT mention any change, skill, or project not explicitly listed above.
                Do NOT invent a reason for a section marked "NO CHANGES".
            """
 
    try:
        summary = await llm_router.complete(system_prompt="You are a concise, factual resume assistant",
                                            user_message = prompt,
                                            temperature = 0.1,
                                            max_tokens = 1500,
                                            task_type = "tailoring")
        
        summary = (summary or "").strip()
        
        if not summary:
            #No exception raise, but content was empty - this is a failure
            logger.warning("[TAILOR] LLM diff summary returned empty content, using deterministic fallback")
            return _deterministic_fallback_summary(per_section_diff)
        
        return summary
    
    except Exception as e:
        logger.warning(f"[TAILOR] LLM Diff summarization failed ({e}), falling back to raw diff")
        #WE Fallback to raw deterministic string if LLM fails
        return _deterministic_fallback_summary(per_section_diff)
    
    
#---------------------------LLM Tailoring Call----------------------------------

async def _call_llm_for_tailoring(job_title: str, company: str, jd_text: str, sections: List[Tuple[str, str]], allowed_skills: List[str]) -> Optional[str]:
    """ 
        LLM Tailoring with XML structural enforcement and fallback
        Args: sections is a list of (section_type, section_text).
        Function Args Update: added Candidate's SKILL WHITE_LIST
    """ 
    
    #Fix: Wrap extracted sections in XML tags
    wrapped_sections = ""
    for i, (section_type, section_text) in enumerate(sections):
        wrapped_sections += f'<section_{i} type="{section_type}">\n{section_text}\n</section_{i}>\n\n'    
    
    allowed_skills_str = ", ".join(sorted(set(allowed_skills))) if allowed_skills else "(none declared)"
    
    USER_MESSAGE = f""" 
    JOB: {job_title} at {company}
    
    JOB DESCRIPTION (TRUNCATED): {jd_text[:1200]}
    
    ALLOWED_SKILLS (the candidate's verified skills - you may ONLY use skills from this
    list in the Skills section, in any phrasing/order that matches the JD's terminology):
    {allowed_skills_str}
    
    CURRENT RESUME SECTIONS: 
    {wrapped_sections}    
    
    TAILORED RESUME SECTIONS: 
    """
    
    
    #Every input section must come back tagged, or injection can't proceed
    #Passed to the router as validate_fn so a provider returning a partial/malformed structure triggers failover to the next provider instead
    #of being slientyl accepted as "successful" call
    def _has_all_section_tags(content: str) -> bool:
        stripped = content.strip().removeprefix("```latex").removesuffix("```").strip()
        return all(re.search(rf'<section_{i}(?:\s[^>]*)?>', stripped) for i in range(len(sections)))
    
    
    
    try:
        #Call LLMROuter for JSON output, Note: Use high tokens limit
        raw_content = await llm_router.complete(
            system_prompt=SYSTEM_PROMPT, user_message = USER_MESSAGE,
            temperature = 0.2, max_tokens = 3000, task_type = "tailoring",
            validate_fn = _has_all_section_tags,
        )
        
        #Strip the markdown symbols if LLM ignored the instructions
        raw_content = raw_content.strip().removeprefix("```latex").removesuffix("```").strip()
        #Fix: Basic validation; Ensure at least the first XML tag exists
        if not re.search(r'<section_0(?:\s[^>]*)?>', raw_content):
            raise ValueError("LLM did not return the required XML structure")
        
        return raw_content 
    
    except Exception as e:
        logger.error(f"[TAILOR] LLM Tailoring failed or validation failed: {e}. Falling back to original resume.")
        return None
    
#------------------------Injection + Section-Aware Validation-----------------------    
def _inject_tailored_content(original_tex: str, original_blocks: List[Tuple[str, str]], llm_response: str, allowed_skills: List[str]) -> Tuple[Optional[str], List[dict]]:
    """ 
        Function to inject the tailored LLM response back into the tex resume PDF
        `original_blocks` is a list of (section_type, original_block_text).
 
        Returns (modified_tex, per_section_diff) where per_section_diff is a
        list of dicts: {section_type, added, removed, blocked}.
    
        Experience sections whose diff touches a number/quantifier are REJECTED:
        the original block is kept instead of the tailored one, and the section
        is marked blocked=True so the summary can note it honestly.
    """
    
    try:
        modified_tex = original_tex 
        tailored_blocks_for_validation = []
        per_section_diff: List[dict] = []
        for i, (section_type, original_block) in enumerate(original_blocks):
            #Extract the tailored block from the LLM's XML response
            tag_regex = re.compile(rf'<section_{i}[^>]*>\s*(.*?)\s*</section_{i}>', re.DOTALL)
            tag_match = tag_regex.search(llm_response)
            
            if not tag_match:
                logger.warning(f"[TAILOR] XML tag <section_{i}> missing from LLM response, Skipping injection for this block")
                return None, []
            
            tailored_block = tag_match.group(1).strip()
            tailored_blocks_for_validation.append(tailored_block)
            
            block_diff = _get_deterministic_diff(original_block, tailored_block)
            changed_phrases = block_diff["added"] + block_diff["removed"]
            
            #Check for quantifier change 
            if section_type == "experience" and any(_contains_quantifier(p) for p in changed_phrases):
                logger.warning(
                    f"[TAILOR] Rejected experience-section change for job section_{i}: "
                    f"quantifier change detected in {changed_phrases}. Reverting to original text."
                )
                
                #Revert back to use the ORIGINAL block, not the tailored one.
                modified_tex = modified_tex.replace(original_block, original_block, 1)
                per_section_diff.append({
                    "section_type": section_type,
                    "added": [], 
                    "removed": [],
                    "blocked": True, 
                    "blocked_reason": "a numeric claim would have been altered"
                })
                continue 
            
            #Candidate's WHITE-LIST SKILLS Check
            if section_type == "skills":
                disallowed = [p for p in block_diff["added"] if not skill_normalizer.is_allowed(p, allowed_skills)]
                
                #If any disallowed skill added, fallback to using original text
                if disallowed:
                    logger.warning(f"[TAILOR] Rejected skills-section change for job section_{i}: unverified skill(s) {disallowed} not in candidate's allowed_skills list. Reverting to original text")
                    
                    modified_tex = modified_tex.replace(original_block, original_block, 1)
                    
                    per_section_diff.append({
                        "section_type": section_type,
                        "added": [],
                        "removed": [],
                        "blocked": True,
                        "blocked_reason": f"unverified skill(s) requested by JD: {', '.join(disallowed)}",
                    })
                    continue
            
            #We can apply this section's tailored content
            modified_tex = modified_tex.replace(original_block, tailored_block, 1)
            per_section_diff.append({
                "section_type": section_type,
                "added": block_diff["added"],
                "removed": block_diff["removed"],
                "blocked": False, 
            })
            
        original_texts_only = [b[1] for b in original_blocks]
        if not _validate_tailored_blocks(original_texts_only, tailored_blocks_for_validation):
            logger.error("[TAILOR] LLM Hallucinated or deleted too much content, Rejecting changes.")
            return None, []
        

        if "\\begin{document}" not in modified_tex or "\\end{document}" not in modified_tex:
            raise ValueError("LaTeX structure corrupted during injection")
        
        return modified_tex, per_section_diff
    
    except Exception as e:
        logger.error(f"[TAILOR] Surgical injection failed: {e}. Falling back to original resume.")
        return None, []

#--------------------------------Main Entry Point-----------------------------
async def tailor(base_tex_path: str,jd_text:  str,
                 job_title:     str,company:  str,
                 user_id:       int,job_id:   int,
                 allowed_skills: Optional[List[str]] = None) -> Tuple[Optional[str], Optional[str], str]:
    
    """ 
        Main entry point for Resume Tailoring
        Returns: {Tex_path, Pdf_path, diff_summary} on Success, (None, None, "") on failure.
    """
    
    logger.info(f"[TAILOR] Starting resume tailoring for Job {job_id} ({company}) for User {user_id}")
    allowed_skills = allowed_skills or []
    
    try:
        #Read the Base-Resume
        with open(base_tex_path, "r", encoding = "utf-8") as f:
            original_tex = f.read()
            
        #--------Phase 1:- Parse The Resume----------------
        parser = LatexParser()
        original_tex, tailorable_blocks = parser.parse(original_tex)
        
        #Fix: If tailorable_blocks missing, Return None,None
        if not tailorable_blocks:
            logger.warning("[TAILOR] Could not extract tailorable sections. Returning Original")
            return None, None, ""
        
        
        #---------Phase 2:-  LLM Tailoring--------------------
        llm_response = await _call_llm_for_tailoring(job_title, company, jd_text, tailorable_blocks, allowed_skills)
        if not llm_response:
            logger.warning("[TAILOR] LLM response missing.")
            return None, None, ""    #Fallback to original resume is handled inside the function
        
        
        #Phase 3:- Injection of tailored sections according to JD
        modified_tex, per_section_diff  = _inject_tailored_content(original_tex, tailorable_blocks, llm_response, allowed_skills)
        if not modified_tex:
            return None, None, ""      #Fallback to original is handled inside the function
        
        #Generate Natural Language Summary of the Deterministic Diff
        diff_summary = await _summarize_diff_with_llm(per_section_diff, job_title, company)
        
        #Phase 4:- Compilation and Storage
        output_dir = f"data/users/{user_id}/outputs"
        filename_base = f"resume_{company.replace(' ', '_')}_{job_id}"
        tex_path, pdf_path = compile_pdf(modified_tex, output_dir, filename_base)
        
        if not tex_path or not pdf_path:
            return None, None, diff_summary
        
        #Phase 5:- Update the DB
        with get_db() as db:
            crud.update_job_status(db,user_id, job_id, JobStatus.TAILORED)
            logger.info(f"[TAILOR] DB Updated. Job {job_id} marked as TAILORED")
            
        
        return tex_path, pdf_path, diff_summary
    
    except Exception as e:
        logger.error(f"[TAILOR] Fatal error in tailor pipeline: {e}")
        return None, None, ""
            
                
def _validate_tailored_blocks(original_blocks: List[str], tailored_blocks: List[str]) -> bool:
    """ 
        Post-validation: Strips the LaTeX commands and checks if original core skills were deleted by the LLM
        for preventing Hallucination in the response
    """
    
    for original, tailored in zip(original_blocks, tailored_blocks):
        orig_text = _strip_latex(original)
        tail_text = _strip_latex(tailored)
        
        #Extract words for comparison
        orig_words = set(re.findall(r'[a-zA-Z0-9+#]+', orig_text.lower()))
        tail_words = set(re.findall(r'[a-zA-Z0-9+#]+', tail_text.lower()))
        
        #Condition: If the tailored block is missing >40% of the original unique words,
        #Then it means LLM probably deleted a chunk of skills or bullets
        if len(orig_words) > 0:
            retention_rate = len(orig_words.intersection(tail_words)) / len(orig_words)
            
            if retention_rate < 0.6:
                logger.warning(f"[TAILOR] Validation failed: Retention Rate {retention_rate:.2f} is too low")
                return False 
            
    return True 


