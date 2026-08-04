import os 
import logging 
import re 
from typing import Tuple, Optional


from router.llm_router import llm_router
from db import crud
from db.crud import get_db
from db.models import JobStatus
from resume.parser import LatexParser
from resume.compiler import compile_pdf


logger = logging.getLogger(__name__)

SYSTEM_PROMPT = r"""
You are an expert technical resume writer and ATS optimization specialist. 
Your task is to tailor specific sections of a candidate's LaTeX resume for a specific job.

STRICT RULES:
1. You will receive LaTeX resume sections wrapped in XML tags (e.g., <section_0>...</section_0>).
2. You MUST return the EXACT same XML tags with the modified LaTeX inside.
3. Do NOT output markdown fences (```). Do NOT output prose or explanations. ONLY output the XML tags.
4. MODIFYING SUMMARY: Rewrite to mirror the job title and core requirements. Keep it under 3 sentences. Preserve LaTeX formatting commands (like \textbf, \section).
5. MODIFYING SKILLS: Inject technical skills from the JD to beat ATS filters. Do NOT delete original skills. Do NOT invent soft skills. Preserve LaTeX formatting (like \textbf{Category:}, '\newline', etc.).
6. MODIFYING EXPERIENCE: Rewrite 2-3 bullet points (\item) to mirror the JD's language. Do NOT delete jobs or fabricate experience.
"""

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
            temperature = 0.2, max_tokens = 1500
        )
        
        #Strip the markdown symbols if LLM ignored the instructions
        raw_content = raw_content.strip().removeprefix("```latex").removesuffix("```").strip()
        
        #Fix: Basic validation; Ensure at least the first XML tag exists
        if f"<section_0>" not in raw_content:
            raise ValueError("LLM did not return the required XML structure")
        
        return raw_content 
    
    except Exception as e:
        logger.error(f"LLM Tailoring failed or validation failed: {e}. Falling back to original resume.")
        return None
    
    
def _inject_tailored_content(original_tex: str, original_blocks: list, llm_response: str) -> Optional[str]:
    """ 
        Function to inject the tailored LLM response back into the tex resume PDF
        We simply just match XML tags and replace string of original content with tailored content
    """
    
    try:
        modified_tex = original_tex 
        tailored_blocks_for_validation = []
        
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
            else:
                logger.warning(f"XML tag <section_{i}> missing from LLM response: Skippin injection for this block.")
                return None
        
        # #Verification: Post-Validation Step
        # if not _validate_tailored_blocks(original_blocks, tailored_blocks_for_validation):
        #     logger.error("LLM Hallucinated or Delted too much changes, Rejecting Changes")
        #     return None
        
        #Verification: Ensure LaTeX structure is Intact
        if "\\begin{document}" not in modified_tex or "\\end{document}" not in modified_tex:
            raise ValueError("LaTeX structure corrupted during injection")
        
        return modified_tex
    except Exception as e:
        logger.error(f"Surgical injection failed: {e}. Falling back to original resume.")
        return None 
    

async def tailor(base_tex_path: str,
                 jd_text:        str,
                 job_title:     str,
                 company:       str,
                 user_id:       int,
                 job_id:        int) -> Tuple[Optional[str], Optional[str]]:
    
    """ 
        Main entry point for Resume Tailoring
        Returns: {Tex_path, Pdf_path} on Success, (None, None) on failure.
    """
    
    logger.info(f"Starting resume tailoring for Job {job_id} ({company}) for User {user_id}")
    
    try:
        #Read the Base-Resume
        with open(base_tex_path, "r", encoding = "utf-8") as f:
            original_tex = f.read()
            
        #--------Phase 1:- Parse The Resume
        parser = LatexParser()
        original_tex, tailorable_blocks = parser.parse(original_tex)
        
        #Fix: If tailorable_blocks missing, Return None,None
        if not tailorable_blocks:
            logger.warning("Could not extract tailorable sections. Returning Original")
            return None, None 
        
        
        #---------Phase 2:-  LLM Tailoring
        llm_response = await _call_llm_for_tailoring(job_title, company, jd_text, tailorable_blocks)
        if not llm_response:
            logger.warning("LLM response missing.")
            return None, None    #Fallback to original resume is handled inside the function
        
        
        #Phase 3:- Injection of tailored sections according to JD
        modified_tex = _inject_tailored_content(original_tex, tailorable_blocks, llm_response)
        if not modified_tex:
            return None, None      #Fallback to original is handled inside the function
        
        #Phase 4:- Compilation and Storage
        output_dir = f"data/users/{user_id}/outputs"
        filename_base = f"resume_{company.replace(' ', '_')}_{job_id}"
        
        tex_path, pdf_path = compile_pdf(modified_tex, output_dir, filename_base)
        
        if not tex_path or not pdf_path:
            return None, None 
        
        #Phase 5:- Update the DB
        with get_db() as db:
            crud.update_job_status(db,user_id, job_id, JobStatus.TAILORED)
            logger.info(f"DB Updated. Job {job_id} marked as TAILORED")
            
        
        return tex_path, pdf_path 
    
    except Exception as e:
        logger.error(f"Fatal error in tailor pipeline: {e}")
        return None, None
            
                
        
        
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
                logger.warning(f"Validation failed: Retention Rate {retention_rate:.2f} is too low")
                return False 
            
    return True 