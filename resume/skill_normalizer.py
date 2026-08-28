import os 
import re 
import logging 
import yaml 
from typing import Iterable

logger = logging.getLogger(__name__)

SYNONYMS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "skill_synonyms.yml")

#Alias(normalized) -> Canonical (normalized)

ALIAS_TO_CANONICAL: dict[str, str] = {}

def _normalize(text: str) -> str:
    """ 
        Normalizes the text by stripping punctuation/whitespace and lowercases so equivalent skill spellings
        compare equal (Eg: "React.js" = "react js")
    """
    
    text = text.lower().strip()
    text = re.sub(r"[.\-_]", " ", text)
    text = re.sub(r"[^a-z0-9+#\s]", "", text)
    
    return re.sub(r"\s+", " ", text).strip()

def _load_synonyms() -> None:
    if ALIAS_TO_CANONICAL:
        return 
    
    try:
        with open(SYNONYMS_PATH, "r", encoding = "utf-8") as f:
            raw = yaml.safe_load(f) or {}
            
    except FileNotFoundError:
        logger.warning(f"[SkillNormalizer] {SYNONYMS_PATH} not found, running with an empty alias table")
        raw = {}
        
    
    for canonical, aliases in raw.items():
        canonical_norm = _normalize(str(canonical))
        ALIAS_TO_CANONICAL[canonical_norm] = canonical_norm
        for alias in (aliases or []):
            #Map normalized alias to the normalized canonical form 
            ALIAS_TO_CANONICAL[_normalize(str(alias))] = canonical_norm
            

def canonicalize(skill : str) -> str:
    """ 
        Function which returns the canonical normalized form of a skill name,
        resolving known aliases(eg: k8s --> kubernetes)
    """
    _load_synonyms()
    norm = _normalize(skill)
    return ALIAS_TO_CANONICAL.get(norm, norm)

def is_allowed(added: str, allowed_skills: Iterable[str]) -> bool:
    """ 
        Function which checks whether 'added' (i.e a phrase pulled from the LLM's added-content diff) refers
        to the SAME SKILL as one already in 'allowed_skills' (giving us the candidate's verified skill list)
        
        This is only for already preseent skills(via the synonym table); it never infers a new, different skill.
    """
    
    if not added or not added.strip():
        return True 
    
    added_canonical = canonicalize(added)
    allowed_canonical = {canonicalize(s) for s in allowed_skills if s}
    
    return added_canonical in allowed_canonical