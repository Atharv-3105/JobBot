import os 
import logging 
import re 
from typing import Tuple, Optional
import difflib
from router.llm_router import llm_router
from db import crud
from db.crud import get_db
from db.models import JobStatus
from resume.parser import LatexParser
from resume.compiler import compile_pdf


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

5. MODIFYING SKILLS: 
   - Inject technical skills from the JD to beat ATS filters. 
   - Do NOT delete original skills. Do NOT invent soft skills. 
   - Preserve LaTeX formatting (like \textbf{Category:}, '\newline', etc.).

6. MODIFYING EXPERIENCE (CRITICAL): 
   - You MUST preserve the original projects, companies, and core technologies mentioned in the bullet points.
   - You may ONLY rephrase the bullet points to emphasize aspects that align with the JD.
   - DO NOT invent new projects, DO NOT replace the original technologies with JD keywords if they were not in the original bullet, and DO NOT fabricate experience.
   - Keep the exact same number of bullet points (\item).
"""


def _get_deterministic_diff(original_text: str, tailored_text: str) -> dict:
    """ 
        Generates a deterministic, word/phrase-level diff between original and tailored tex
        Strips LaTeX commands to focus on actual content changes, prevents syntax tags
    """
    def strip_latex(text: str) -> str:
        """ 
            Removes the LaTeX commands like \textbf{}, \section*{}, etc. 
        """
        
        text = re.sub(r'\\[a-zA-Z*]+\{([^}]*)\}', r'\1', text)
        text = re.sub(r'\\[a-zA-Z*]+', '', text)
        
        return text.strip()
    
    origi_clean = strip_latex(original_text)
    tail_clean = strip_latex(tailored_text)
    
    #We use SequenceMatcher for Block-Level phrase diff
    matcher = difflib.SequenceMatcher(None, origi_clean.split(), tail_clean.split())
    
    added_phrases = []
    removed_phrases = []
    
    #get_opcodes function returns a tuple which tells how to convert string a into string b
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ('replace', 'delete'):
            removed_phrases.append(" ".join(origi_clean.split()[i1:i2]))
        if tag in ('replace', 'insert'):
            added_phrases.append(" ".join(tail_clean.split()[j1:j2]))
            
    
    #Clean the punctuation and deduplicate
    added = list(set([p.strip('.,;:(){}[]\\') for p in added_phrases if re.search(r'[a-zA-Z0-9]', p)]))
    removed = list(set([p.strip('.,;:(){}[]\\')for p in removed_phrases if re.search(r'[a-zA-Z0-9]', p)]))
    
    if not added or not removed:
        logger.info(f"[TAILOR]: Diff Generation failed")
    
    logger.info(f"[DEBUG] Diff added: {added} | removed: {removed}")
    return {
        "added": added, 
        "removed": removed
    }
    
async def _summarize_diff_with_llm(diff_data: dict, job_title: str, company: str) -> str:
    """ 
        Uses LLM to translate the deterministic diff into a 1-2 sentence natural language summary.
    """
    
    added_str = ", ".join(diff_data["added"]) if diff_data["added"] else "None"
    removed_str = ", ".join(diff_data["removed"]) if diff_data["removed"] else "None"
    
    logger.info(f"[DEBUG] Diff added_str: {added_str} | remove_strd: {removed_str}")
    
    prompt = f"""
                You are a helpful assistant. A user's resume was updated for a {job_title} role at {company}. 
                Here is a deterministic, algorithmic list of content changes made to the resume:
                - Added keywords/skills: {added_str}
                - Removed keywords/skills: {removed_str}

                Summarize these exact changes in 1-2 concise, professional sentences explaining how the resume was improved for this specific role. 
                STRICT RULE: Do NOT hallucinate or mention any changes, skills, or projects that are not explicitly listed in the 'Added' or 'Removed' lists above.
            """
    
    try:
        summary = await llm_router.complete(system_prompt="You are a concise, factual resume assistant",
                                            user_message = prompt,
                                            temperature = 0.1,
                                            max_tokens = 150,
                                            task_type = "tailoring")
        
        return summary.strip()
    
    except Exception as e:
        logger.warning(f"[TAILOR] LLM Diff summarization failed, falling back to raw diff")
        #WE Fallback to raw deterministic string if LLM fails
        return f"Added: {added_str}. Removed: {removed_str}"

async def _call_llm_for_tailoring(job_title: str, company: str, jd_text: str, sections: list) -> Optional[str]:
    """ 
        LLM Tailoring with XML structural enforcement and fallback
    """ 
    
    #Fix: Wrap extracted sections in XML tags
    wrapped_sections = ""
    for i, section in enumerate(sections):
        wrapped_sections += f"<section_{i}>\n{section}\n</section_{i}>\n\n"
        
    
    
    USER_MESSAGE = f""" 
    JOB: {job_title} at {company}
    
    JOB DESCRIPTION (TRUNCATED): {jd_text[:1200]}
    
    CURRENT RESUME SECTIONS: 
    {wrapped_sections}    
    
    TAILORED RESUME SECTIONS: 
    """
    
    try:
        #Call LLMROuter for JSON output, Note: Use high tokens limit
        raw_content = await llm_router.complete(
            system_prompt=SYSTEM_PROMPT, user_message = USER_MESSAGE,
            temperature = 0.2, max_tokens = 3000, task_type = "tailoring"
        )
        
        #Strip the markdown symbols if LLM ignored the instructions
        raw_content = raw_content.strip().removeprefix("```latex").removesuffix("```").strip()
        #Fix: Basic validation; Ensure at least the first XML tag exists
        if "<section_0>" not in raw_content:
            raise ValueError("LLM did not return the required XML structure")
        
        return raw_content 
    
    except Exception as e:
        logger.error(f"[TAILOR] LLM Tailoring failed or validation failed: {e}. Falling back to original resume.")
        return None
    
    
def _inject_tailored_content(original_tex: str, original_blocks: list, llm_response: str) -> Tuple[Optional[str], dict]:
    """ 
        Function to inject the tailored LLM response back into the tex resume PDF
        We simply just match XML tags and replace string of original content with tailored content
    """
    
    try:
        modified_tex = original_tex 
        tailored_blocks_for_validation = []
        diff_data = {"added": [], "removed": []}
        for i, original_block in enumerate(original_blocks):
            #Extract the tailored block from the LLM's XML response
            tag_regex = re.compile(rf"<section_{i}>\s*(.*?)\s*</section_{i}>", re.DOTALL)
            tag_match = tag_regex.search(llm_response)
            
            if tag_match:
                tailored_block = tag_match.group(1).strip()
                tailored_blocks_for_validation.append(tailored_block)
    
                #Replace the original block with the tailored block in the full LaTeX string
                #We will use .replace(original, new, 1) to only replace the first occurence safely
                modified_tex = modified_tex.replace(original_block, tailored_block, 1)
                
                #Generate the Deterministic diff for this block
                block_diff = _get_deterministic_diff(original_block, tailored_block)
                diff_data["added"].extend(block_diff["added"])
                diff_data["removed"].extend(block_diff["removed"])
            else:
                logger.warning(f"[TAILOR] XML tag <section_{i}> missing from LLM response: Skippin injection for this block.")
                return None, {}
        
        # #Verification: Post-Validation Step
        if not _validate_tailored_blocks(original_blocks, tailored_blocks_for_validation):
            logger.error("[TAILOR] LLM Hallucinated or Delted too much changes, Rejecting Changes")
            return None, {}
        
        #Verification: Ensure LaTeX structure is Intact
        if "\\begin{document}" not in modified_tex or "\\end{document}" not in modified_tex:
            raise ValueError("LaTeX structure corrupted during injection")
        
        #Deduplicate the Final Diff Lists
        diff_data["added"] = list(set(diff_data["added"]))
        diff_data["removed"] = list(set(diff_data["removed"]))
        
        return modified_tex, diff_data
    except Exception as e:
        logger.error(f"[TAILOR] Surgical injection failed: {e}. Falling back to original resume.")
        return None, {}
    

async def tailor(base_tex_path: str,
                 jd_text:        str,
                 job_title:     str,
                 company:       str,
                 user_id:       int,
                 job_id:        int) -> Tuple[Optional[str], Optional[str], str]:
    
    """ 
        Main entry point for Resume Tailoring
        Returns: {Tex_path, Pdf_path, diff_summary} on Success, (None, None, "") on failure.
    """
    
    logger.info(f"[TAILOR] Starting resume tailoring for Job {job_id} ({company}) for User {user_id}")
    
    try:
        #Read the Base-Resume
        with open(base_tex_path, "r", encoding = "utf-8") as f:
            original_tex = f.read()
            
        #--------Phase 1:- Parse The Resume
        parser = LatexParser()
        original_tex, tailorable_blocks = parser.parse(original_tex)
        
        #Fix: If tailorable_blocks missing, Return None,None
        if not tailorable_blocks:
            logger.warning("[TAILOR] Could not extract tailorable sections. Returning Original")
            return None, None, ""
        
        
        #---------Phase 2:-  LLM Tailoring
        llm_response = await _call_llm_for_tailoring(job_title, company, jd_text, tailorable_blocks)
        if not llm_response:
            logger.warning("[TAILOR] LLM response missing.")
            return None, None, ""    #Fallback to original resume is handled inside the function
        
        
        #Phase 3:- Injection of tailored sections according to JD
        modified_tex, diff_data  = _inject_tailored_content(original_tex, tailorable_blocks, llm_response)
        if not modified_tex:
            return None, None, ""      #Fallback to original is handled inside the function
        
        #Generate Natural Language Summary of the Deterministic Diff
        diff_summary = await _summarize_diff_with_llm(diff_data, job_title, company)
        
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
            
                
def _validate_tailored_blocks(original_blocks: list, tailored_blocks: list) -> bool:
    """ 
        Post-validation: Strips the LaTeX commands and checks if original core skills were deleted by the LLM
        for preventing Hallucination in the response
    """
    
    #Simple regex to strip LaTeX commands, keeping just the text content
    strip_latex = lambda x: re.sub(r'\\[a-zA-Z*]+\{([^}]*)\}', r'\1',x)
    
    for original, tailored in zip(original_blocks, tailored_blocks):
        orig_text = strip_latex(original)
        tail_text = strip_latex(tailored)
        
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


