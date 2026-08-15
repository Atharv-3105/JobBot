import os 
import re 
import logging 
import json 
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from json_repair import repair_json 

from portals.base import JobListing
from db import crud, get_db
from router.llm_router import llm_router

logger = logging.getLogger(__name__)

@dataclass 
class ScoredJob:
    job: JobListing
    db_job_id: Optional[int] 
    score: str  
    match_percentage: int 
    strengths: List[str]
    gaps: List[str]
    recommendation: str 
    

#======================Pre-Filtering & Extraction=====================
def extract_jd_sections(jd_text: str, max_chars: int = 1500) -> str:
    """ 
        Function to extract 'Requirements' from the JD instead of Blind Truncation
        So that we don't send the whole JD to the LLM and can save cost
    """
    
    #Fix: If no standard headers are used, then immediately return the truncated start of the JD
    
    if not jd_text:
        return ""
    headers = [r"(?i)(requirements|qualifications|what you'll do|responsibilities|about the role|skills|tech stack|what you bring)"]
    start_idx = -1
    
    for header in headers:
        match = re.search(header, jd_text)
        if match:      
            start_idx = match.start()
            
    #Fix: If we found a header, extract from there. Otherwise, take the top of the JD
    if start_idx != -1:
        extracted = jd_text[start_idx: start_idx + max_chars]
        #If the requirements section is very short, fallback to the top of the JD
        if len(extracted) < 300:
            return jd_text[:max_chars]
        
        return extracted
    else:
        return jd_text[:max_chars]

def passess_rule_filter(job: JobListing, profile: Dict[str, Any]) -> bool:
    """ 
        Function for a deterministic-rule based filter for dropping Jobs which are irrelevant according to the profile
        Checks for core-skills or primary skills from candidate profile
    """
    
    #Get the skills from the profile
    skills = profile.get("skills", {})
    #Combine core and primary skills for the hard filtering
    target_skills = skills.get("core", []) + skills.get("primary", [])
    
    if not target_skills:
        return True     #If no skills are define, passed everything
    
    jd_lower = job.jd_text.lower()
    
    #Build a Regex pattern: \b(python|fastapi|go)\b
    escaped_skills = [re.escape(skill.lower()) for skill in target_skills]
    pattern = r"\b(" +  "|".join(escaped_skills)+ r")\b"
    return bool(re.search(pattern, jd_lower))

def passes_experience_filter(job: JobListing, profile: Dict[str, Any]) -> bool:
    """ 
        Rule-Base filter to drop jobs with explicit seniority mismatches.
        Saves LLM Tokens by not scoring Senior/Principal jobs for juniors, and Vice-Versa
    """

    experience_years = profile.get("experience_years", 0)
    title_lower = job.title.lower()
    
    #---------Junior/Entry-Level Check-----------
    #If the User is Senior we drop explicit Junior/Intern roles
    if experience_years >= 5:
        junior_keywords = r"\b(junior|jr|intern|fresher|entry.level|associate)\b"
        if re.search(junior_keywords, title_lower):
            logger.info(f"FILTER: Dropped '{job.title}' - Senior User, Junior Role")
            return False
    
    #---------Senior/Experienced Check--------------
    #If the User is Junior drop explicit Senior/Principal/Manager Roles
    if experience_years < 3:
        senior_keyword = r"\b(senior|sr|staff|principal|lead|manager|director|head|vp|chief)\b"
        if re.search(senior_keyword, title_lower):
            logger.info(f"FILTER: Dropped '{job.title}' - Junior User, Senior/Leadership Role")
            return False 
        
    
    #If no explicit mismatch found, let it pass to the LLM for deeper scoring
    return True
        

#=========================BATCH SCORING & JSON PARSING==================================
SYSTEM_PROMPT = """
You are an expert technical recruiter and hiring manager. 
Your task is to score job listings against a candidate's profile.

Scoring Rubric:
- A: Perfect match. Core skills align, experience level matches, highly recommended.
- B: Strong match. Most core skills align, minor gaps that can be learned. Recommended.
- C: Moderate match. Some core skills align, but significant gaps or wrong seniority. 
- D: Weak match. Few skills align. Not recommended.
- F: No match. Completely different stack or role. Skip.

CRITICAL OUTPUT RULES:
1. You MUST respond ONLY with a valid JSON array. 
2. Do NOT wrap the JSON in ```json ... ``` markdown blocks.
3. Do NOT include any prose, explanations, or text outside the JSON array.
4. If you cannot score a job, assign it a 'C' and explain why in the gaps.

Format:
[
  {
    "job_id": "the portal_job_id",
    "score": "A",
    "match_percentage": 85,
    "strengths": ["Skill X matches", "Experience aligns"],
    "gaps": ["Missing Skill Y", "Location mismatch"],
    "recommendation": "Apply immediately"
  }
]
"""
    
    
async def score_batch_with_llm(jobs: List[JobListing], profile: Dict[str, Any]) -> List[ScoredJob]:
    """ 
        This function does Batch-Scoring(max-3 jobs per call)
    """
    if not jobs:
        return []
    
    profile_summary = f""" 
        Target Roles: {', '.join(profile.get('target_roles', []))}
        Experience: {profile.get('experience_years', 0)} years
        
        SKILL PROFICIENCY HEIRARCHY:
            - Core(Expert/Daily): {', '.join(profile.get('skills', {}).get('core', []))}
            - Primary(Strong/Frequent): {', '.join(profile.get('skills', {}).get('primary', []))}
            - Secondary(Familiar): {', '.join(profile.get('skills', {}).get('secondary', []))}
            - Basic(Conceptual): {', '.join(profile.get('skills', {}).get('basic', []))}
    """
    
    
    jobs_payload = []
    for job in jobs:
        #Apply Smart-Extraction + char limit
        truncated_jd =  extract_jd_sections(job.jd_text, max_chars=1500)
        jobs_payload.append({
            "job_id": job.portal_job_id,
            "title": job.title, 
            "company": job.company,
            "location": job.location,
            "jd_excerpt": truncated_jd
        })
        
    
    user_message = f""" 
    CANDIDATE PROFILE:
    {profile_summary}
    
    JOBS TO SCORE:
    {json.dumps(jobs_payload, indent = 2)}
    """
    
    
    try:
        #Call Router
        raw_content = await llm_router.complete(
            system_prompt= SYSTEM_PROMPT,
            user_message = user_message,
            temperature = 0.1,
            max_tokens=2000, task_type = "scoring"
        )
        
        #Fix-the response for accurate JSON
        fixed_content = repair_json(raw_content, return_objects = True)
        
        if not isinstance(fixed_content, list):
            #Sometimes LLM wraps the list in a dict like {"jobs": [...]}
            if isinstance(fixed_content, dict):
                for key in fixed_content:
                    if isinstance(fixed_content[key], list):
                        fixed_content = fixed_content[key]
                        break 
                    
            else:
                raise ValueError("LLM Did Not return a JSON array")
            
        
        #Map scores back to JobListing objects
        scored_jobs = []
        score_map = {item.get("job_id"): item for item in fixed_content}
        
        for job in jobs:
            score_data = score_map.get(job.portal_job_id)
            if score_data:
                scored_jobs.append(ScoredJob(
                    job = job,
                    db_job_id=None,
                    score = score_data.get("score", "C"),
                    match_percentage = score_data.get("match_percentage", 0),
                    strengths = score_data.get("strengths", []),
                    gaps = score_data.get("gaps", []),
                    recommendation = score_data.get("recommendation", "")
                ))
            else:
                #Fallback if LLM missed a job in the batch
                scored_jobs.append(ScoredJob(
                    job = job,
                    db_job_id= None,
                    score = "C",
                    match_percentage = 50,
                    strengths = [],
                    gaps = ["LLM Parsing Error"],
                    recommendation = "Review manually",
                ))
                
        return scored_jobs
                
    except Exception as e:
        logger.error(f"[SCORER] LLM Scoring failed across all providers: {e}")
        #We return neutral scores so the Pipeline doesn't crash
        return [ScoredJob(job = j, score = "C", match_percentage=50, strengths = [], gaps = [str(e)], recommendation="Error") for j in jobs]
    

async def score_listing(jobs: List[JobListing], profile: Dict[str, Any], user_id: int = None) -> List[ScoredJob]:
    """ 
        This function is the main entry point for the Scorer Node
        Supports Caching By Job-URL, Tailor Only A/B Scores
    """
    
    logger.info(f"[SCORER]: Received {len(jobs)} jobs to evaluate")
    
    #------------- Rule based Pre-Filter------------------
    filtered_jobs = [job for job in jobs if passess_rule_filter(job, profile) and passes_experience_filter(job, profile)]
    dropped_count = len(jobs) - len(filtered_jobs)
    logger.info(f"[SCORER]: Rule-Based Filter dropped {dropped_count} jobs. {len(filtered_jobs)} remaining")
    
    #check if filtered_jobs is None
    if not filtered_jobs:
        return []
    
    #-----------------Score Caching Check-----------------
    jobs_to_score = []
    cached_scored_jobs = []
    
    if user_id:
        with get_db() as db:
            for job in filtered_jobs:
                #Check the DB for job
                db_job = crud.get_job_by_url(db, user_id, job.url)
                
                #Cache-Hit, job is present in the DB
                if db_job and db_job.score:
                    logger.info(f"[SCORER]: Cache hit for {db_job.title}")
                    
                    #Parse the cached score_data JSON
                    score_data = {}
                    
                    if db_job.score_data:
                        try:
                            score_data = json.loads(db_job.score_data)
                        except:
                            score_data = {}
                    
                    #Critical FIX: Create ScoredJOB AND append to all_scored_jobs
                    cached_scored_jobs.append(ScoredJob(
                        job = job,
                        db_job_id = db_job.id,
                        score = db_job.score,
                        match_percentage = score_data.get("match_percentage", 0),
                        strengths = score_data.get("strengths", []),
                        gaps = score_data.get("gaps", []),
                        recommendation = score_data.get("recommendation", "")
                    ))
                    
                else:
                    jobs_to_score.append(job)
    
            
    else:
        jobs_to_score = filtered_jobs
        
    logger.info(f"[SCORER]: cache hit for {len(cached_scored_jobs)} jobs. {len(jobs_to_score)} need LLM Scoring")
    
    if not jobs_to_score and not cached_scored_jobs:
        return []
    
    #-------------Batch Scoring for efficient llm calling---------------
    BATCH_SIZE = 3
    newly_scored_jobs = []
    
    for i in range(0, len(jobs_to_score), BATCH_SIZE):
        batch = jobs_to_score[i : i + BATCH_SIZE]
        logger.info(f"Scorer: Processing batch {i // BATCH_SIZE + 1} ({len(batch)} jobs)...")
        
        scored_batch = await score_batch_with_llm(batch, profile)
        newly_scored_jobs.extend(scored_batch)
        
        #Save scores to DB for future caching
        if user_id:
            with get_db() as db:
                for sj in scored_batch:
                    score_dict = {
                        "match_percentage": sj.match_percentage,
                        "strengths": sj.strengths,
                        "gaps": sj.gaps,
                        "recommendation": sj.recommendation,
                    }
                    db_job = crud.save_job(
                        db, user_id, sj.job.title, sj.job.company,
                        sj.job.url, sj.job.portal, sj.job.jd_text,
                        sj.score, score_data = score_dict
                    )
                    
                    sj.db_job_id = db_job.id
                
    #Combine Cached and Newly Scored Jobs
    all_scored_jobs = cached_scored_jobs + newly_scored_jobs
    
    final_jobs = [sj for sj in all_scored_jobs if sj.score in ["A", "B"]]
    logger.info(f"[SCORER]: {len(final_jobs)} jobs scored A or B and will proceed to tailoring.")
    
    return final_jobs
            
    