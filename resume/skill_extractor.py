import json 
import logging 
import re 
from typing import List

from router.llm_router import llm_router

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM_PROMPT = r"""
You are a precise information-extraction assistant.
Given the raw text of a candidate's resume, extract every specific,
named technology, programming language, framework, library, database,
cloud platform, or developer tool that is EXPLICITLY WRITTEN in the text.

STRICT RULES:
1. Only extract items that are literally present as words/phrases in the text.
2. Do NOT infer, generalize, or add anything not explicitly named
   (e.g. if the text says "deployed on the cloud", do NOT add "AWS" unless
   "AWS" is literally written somewhere).
3. Do NOT include soft skills, job titles, company names, or degree names.
4. Return ONLY a JSON array of strings, nothing else. No markdown, no prose.
   Example: ["Python", "FastAPI", "Docker", "PostgreSQL"]
"""

def _strip_latex(text: str) -> str:
    """ 
        This function removes LaTeX commands/escapes, returns plain readable text
    """
    if not text:
        return ""
    
    text = re.sub(r'\\([&%$#_{}~^])', r'\1', text)
    text = re.sub(r'\\[a-zA-Z*]+\{([^}]*)\}', r'\1', text)
    text = re.sub(r'\\[a-zA-Z*]+', '', text)
    text = re.sub(r'[{}]', '', text)
    return re.sub(r'\s+', ' ', text).strip()

def _parse_json_array(raw: str) -> List[str]:
    """ 
        Function to parse the JSON reponse from the LLM
    """
    stripped = raw.strip().removeprefix("```json").removesuffix("```").strip()
    try:
        data = json.loads(stripped)
    
    except (json.JSONDecodeError,TypeError):
        logger.warning(f"[SKILL-EXTRACTOR] LLM did not return valid JSON, got : {stripped[:200]}")
        return []
    
    if not isinstance(data, list):
        return []
    
    return [str(item).strip() for item in data if str(item).strip()]

async def extract_skills_from_resume(tex_content: str) -> List[str]:
    """ 
        This is a function which extracts a grounded list of named/mentioned skills/tools from the
        FULL resume text.
        Any skill the LLM returns that is not a literal(case-insensitive/ present) substring of the source
        text is dropped. Prohibits Hallucination
    """
    
    plain_text = _strip_latex(tex_content)
    
    if not plain_text:
        return []
    
    def _is_json_array(content: str) -> bool:
        stripped = content.strip().removeprefix("```json").removesuffix("```").strip()
        
        return stripped.startswith("[") and stripped.endswith("]")
    
    try:
        raw_response = await llm_router.complete(
            system_prompt = EXTRACTION_SYSTEM_PROMPT,
            user_message= f"RESUME TEXT:\n{plain_text}",
            temperature = 0.0,
            max_tokens = 2000,
            task_type = "default",
            validate_fn = _is_json_array,
        )
        
    except Exception as e:
        logger.error(f"[SKILL-EXTRACTOR] Extraction LLM call failed: {e}")
        return []
    
    candidates = _parse_json_array(raw_response)
    
    #Grounding Check: We reject anything added not literally present in the source text
    plain_lower = plain_text.lower()
    verified = []
    
    for c in candidates:
        if c.lower() in plain_lower:
            verified.append(c)
        else:
            logger.warning(f"[SKILL-EXTRACTOR] Dropped ungrounded candidate(not found in source text): '{c}'")
    
    #Deduplicate while preserving the order
    seen = set()
    deduped = []
    for skill in verified:
        key = skill.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(skill)
    
    return deduped