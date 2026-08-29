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
    
    
def _parse_score_array(content: str):
    """
        Shared JSON-repair + array-unwrap logic, used by both the validate_fn
        (pre-acceptance check) and the real parse after a response is accepted.
    """
    parsed = repair_json(content, return_objects = True)

    if not isinstance(parsed, list):
        #Sometimes LLM wraps the list in a dict like {"jobs": [...]}
        if isinstance(parsed, dict):
            for key in parsed:
                if isinstance(parsed[key], list):
                    parsed = parsed[key]
                    break
            else:
                raise ValueError("LLM Did Not return a JSON array")
        else:
            raise ValueError("LLM Did Not return a JSON array")

    return parsed


def _make_score_validator(expected_job_ids: set):
    """
        Builds a validate_fn for llm_router.complete(): rejects a response BEFORE
        it's accepted if it's structurally incomplete, schema-valid-but-empty, or
        matches a known LLM-flakiness signature (e.g. claiming no JD was provided
        when one clearly was sent) - so the router fails over to a different
        provider automatically instead of us silently accepting a degenerate reply.
    """
    def _validate(content: str) -> bool:
        try:
            parsed = _parse_score_array(content)
        except Exception:
            return False

        score_map = {item.get("job_id"): item for item in parsed if isinstance(item, dict)}

        for job_id in expected_job_ids:
            entry = score_map.get(job_id)
            if not entry or "score" not in entry or "match_percentage" not in entry:
                return False

            match_pct = entry.get("match_percentage", 0)
            strengths = entry.get("strengths", [])
            gaps = entry.get("gaps", [])

            #Schema-valid but empty - every genuine 0% match we've seen still lists gaps
            if match_pct == 0 and not strengths and not gaps:
                return False

            #Known hallucination signature: a jd_excerpt is always included when jd_text
            #is non-empty, so a claim that none was provided means the model is confused,
            #not that the input was actually missing.
            gaps_text = " ".join(str(g) for g in gaps).lower()
            if "no job description" in gaps_text or "job description" in gaps_text and "not provided" in gaps_text:
                return False

        return True

    return _validate


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
    
    
    expected_job_ids = {job.portal_job_id for job in jobs}

    try:
        #Call Router - validate_fn rejects a structurally-incomplete or known-flaky
        #response BEFORE we accept it, so the router fails over to a different
        #provider automatically instead of us silently accepting a degenerate reply.
        raw_content = await llm_router.complete(
            system_prompt= SYSTEM_PROMPT,
            user_message = user_message,
            temperature = 0.1,
            max_tokens=2000, task_type = "scoring",
            validate_fn = _make_score_validator(expected_job_ids),
        )

        fixed_content = _parse_score_array(raw_content)


        #Map scores back to JobListing objects
        scored_jobs = []
        score_map = {item.get("job_id"): item for item in fixed_content}
        
        for job in jobs:
            score_data = score_map.get(job.portal_job_id)
            #Require the load-bearing fields to actually be present, not just that the
            #job_id matched - a truncated/malformed per-job entry (e.g. only "job_id"
            #survived repair_json) must not silently masquerade as a real "C/0%" score.
            is_complete = score_data is not None and "score" in score_data and "match_percentage" in score_data

            if is_complete:
                match_pct = score_data.get("match_percentage", 0)
                strengths = score_data.get("strengths", [])
                gaps = score_data.get("gaps", [])
                #Content-quality check, not just structural: every genuine 0% match we've
                #observed still lists concrete gaps explaining why. A response claiming 0%
                #with NEITHER strengths NOR gaps is more likely a low-effort/degenerate
                #generation (schema-valid but empty) than a real, reasoned evaluation -
                #key-presence alone can't catch this, since the shape is technically correct.
                if match_pct == 0 and not strengths and not gaps:
                    is_complete = False

            if is_complete:
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
                #Fallback if the LLM missed this job in the batch, returned an incomplete/
                #malformed entry for it, or returned a suspect empty-content response -
                #flagged honestly, not disguised as a real score.
                logger.warning(f"[SCORER] Incomplete, malformed, or suspect score data for job_id={job.portal_job_id}: {score_data}")
                scored_jobs.append(ScoredJob(
                    job = job,
                    db_job_id= None,
                    score = "C",
                    match_percentage = 50,
                    strengths = [],
                    gaps = ["LLM response was incomplete, malformed, or empty for this job - please try again"],
                    recommendation = "Review manually",
                ))
                
        return scored_jobs
                
    except Exception as e:
        logger.error(f"[SCORER] LLM Scoring failed across all providers: {e}")
        #We return neutral scores so the Pipeline doesn't crash
        return [ScoredJob(job = j, score = "C", match_percentage=50, strengths = [], gaps = [str(e)], recommendation="Error") for j in jobs]
    

async def score_listing(jobs: List[JobListing], profile: Dict[str, Any], user_id: int = None, mode: str = "search") -> List[ScoredJob]:
    """ 
        This function is the main entry point for the Scorer Node
        Supports Caching By Job-URL, Tailor Only A/B Scores
    """
    
    logger.info(f"[SCORER]: Received {len(jobs)} jobs to evaluate")
    
    #Context-Aware Filtering
    if mode == "search":
        #We do aggressive filtering for high-volume crawl results
    
        #------------- Rule based Pre-Filter------------------
        filtered_jobs = [job for job in jobs if passess_rule_filter(job, profile) and passes_experience_filter(job, profile)]
        dropped_count = len(jobs) - len(filtered_jobs)
        logger.info(f"[SCORER]: Rule-Based Filter dropped {dropped_count} jobs. {len(filtered_jobs)} remaining")
        
    else:
        #NO filtering for explicit user requests(/score, /tailor)
        filtered_jobs = jobs 
        logger.info(f"[SCORER]: Mode is {mode} - Skipping rule-based filter")
        
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
    
    #Mode-Aware filtering
    if mode == "search":
        final_jobs = [sj for sj in all_scored_jobs if sj.score in ["A", "B"]]
        logger.info(f"[SCORER]: {len(final_jobs)} jobs scored A or B and will proceed to tailoring.")
    
    else:
        #For /score, /tailor commands, return ALL jobs so the user sees the result
        final_jobs = all_scored_jobs
        logger.info(f"[SCORER]: Mode is '{mode}' - Returning all {len(final_jobs)} scored jobs for user review.")
    
    return final_jobs
            
    