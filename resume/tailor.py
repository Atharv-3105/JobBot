import os 
import json
import logging 
import re 
from typing import Tuple, Optional, Dict, List 
from json_repair import repair_json

from router.llm_router import llm_router
from db import crud
from db.crud import get_db
from db.models import JobStatus
from resume.parser import LatexParser
from resume.compiler import compile_pdf


logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
You are an expert technical resume writer and ATS optimization specialist. 
Your task is to tailor a candidate's resume for a specific job.

CANDIDATE SKILL HIERARCHY (CRITICAL):
- "core": Expert level. Daily drivers.
- "primary": Strong proficiency. Used frequently.
- "secondary": Familiar. Can work independently.
- "basic": Conceptual knowledge. Can read/understand.

STRICT RULES:
1. OUTPUT FORMAT: You MUST respond ONLY with a valid JSON object. No markdown, no prose.
2. SUMMARY: Rewrite the summary to mirror the job title and core requirements. Keep it under 3 sentences.
3. SKILL INJECTION & REORDERING:
   - You ARE ALLOWED to add technical skills from the JD to the candidate's skills section to beat ATS filters.
   - If you add a skill, place it in the category that matches the candidate's actual proficiency (e.g., do not put a 'basic' skill in 'core').
   - You MUST NOT delete any of the candidate's original skills.
   - You MUST NOT invent soft skills, certifications, or seniority levels.
4. STRUCTURE: The JSON must have exactly two keys: "summary" (string) and "skills" (dict of lists).

JSON FORMAT:
{
  "summary": "Tailored summary text...",
  "skills":{
      "Original Category 1": ["Original Skill A", "Added JD skill B"],
      "Original Category 2": ["Original Skill C"] 
    }
}
"""

async def _call_llm_for_tailoring(job_title: str, company: str, jd_text: str, current_data: dict) -> Optional[dict]:
    """ 
        LLM Tailoring with strict JSON enforcement and fallback
    """ 
    
    USER_MESSAGE = f""" 
    JOB: {job_title} at {company}
    
    JOB DESCRIPTION (TRUNCATED): {jd_text[:1200]}
    
    CURRENT RESUME DATA: 
    {json.dumps(current_data, indent = 2)}    
    """
    
    try:
        #Call LLMROuter for JSON output, Note: Use high tokens limit
        raw_content = await llm_router.complete(
            system_prompt=SYSTEM_PROMPT, user_message = USER_MESSAGE,
            temperature = 0.2, max_tokens = 1500
        )
        
        
        #Repair and Parse the JSON Response
        fixed_content = repair_json(raw_content, return_objects = True)
        
        if not isinstance(fixed_content, dict) or "summary" not in fixed_content or "skills" not in fixed_content:
            raise ValueError("LLM Response missing 'summary' or 'skills' keys")
        
        #Validate that the original categories still exist
        original_cats = {"core", "primary", "secondary", "basic"}
        new_cats = set(fixed_content.get("skills", {}).keys())
        if not original_cats.issubset(new_cats):
            raise ValueError(f"LLM altered skill categories. Missing {original_cats - new_cats}")
        
        #Validate no original skills were deleted(Additions of skills are allowed).
        for category in original_cats:
            original_skills = set(s.lower().strip() for s in current_data["skills"].get(category, []))
            new_skills = set(s.lower().strip() for s in fixed_content["skills"].get(category, []))
            
            #If the LLM deleted a skill, original_skills will not be a subset of new_skills
            if not original_skills.issubset(new_skills):
                dropped_skills = original_skills - new_skills
                raise ValueError(f"LLM deleted original skills in '{category}': {dropped_skills}")
        
        #Log what skills were added for observability
        for category in original_cats:
            original_skills = set(s.lower().strip() for s in current_data["skills"][category])
            new_skills = set(s.lower().strip() for s in fixed_content["skills"].get(category, []))
            added_skills = new_skills - original_skills
            if added_skills:
                logger.info(f"LLM added skills to '{category}' : {added_skills}")
        
        return fixed_content 
    
    except Exception as e:
        logger.error(f"LLM Tailoring failed or validation failed: {e}. Falling back to original resume.")
        return None
    
    
def _inject_tailored_content(original_tex: str, raw_summary: str, raw_skills: str, tailored_data: dict) -> Optional[str]:
    """ 
        Function to inject the tailored content back into the tex resume PDF
        We simply just replace string of original content with tailored content
    """
    
    try:
        modified_tex = original_tex 
        
        #Inject Summary (Simple string replace)
        if raw_summary and "summary" in tailored_data:
            #Clean-Up the Raw Summary to Remove the section header for replacement
            #We just replace the content inside the section
            tailored_summary = tailored_data["summary"]
            
            #Find the actual text block inside the section
            modified_tex = modified_tex.replace(raw_summary, f"\n{tailored_summary}\n")
        
        #Inject Skills (Regex replacement inside categories)
        if raw_skills and "skills" in tailored_data: 
            for category, new_skills in tailored_data["skills"].items():
                skills_string = ", ".join(new_skills)
                
                #Regex to find the category and replace the skills string after it
                pattern = re.compile(rf'(\\textbf\{{[^}}]*?{re.escape(category)}[^}}]*?\}}\s*)([^\n\\]+)', re.IGNORECASE)
                modified_tex = pattern.sub(rf'\1{skills_string}', modified_tex)
                
        
        #verification: Ensure LaTeX structure is intact
        if "\\begin{document}" not in modified_tex or "\\end{document}" not in modified_tex:
            raise ValueError("LaTeX structure corrupted during injection......")
        
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
            
        #Phase 1:- Parse The Resume
        parser = LatexParser()
        original_tex, skills_dict, raw_summary, raw_skills = parser.parse(original_tex)
        
        #If Raw-Summary or Raw-Skills missing, Return None,None
        if not raw_summary or not raw_skills:
            logger.warning("Could not extract Summary or Skills. Returning Original")
            return None, None 
        
        current_data = {
            "summary": raw_summary,
            "skills":  skills_dict,
        }
        
        #Phase 2:-  LLM Tailoring
        tailored_data = await _call_llm_for_tailoring(job_title, company, jd_text, current_data)
        if not tailored_data:
            return None, None    #Fallback to original resume is handled inside the function
        
        
        #Phase 3:- Injection of Skills and Summary according to JD
        modified_tex = _inject_tailored_content(original_tex, raw_summary, raw_skills, tailored_data)
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
            
                