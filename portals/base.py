from dataclasses import dataclass, field 
from abc import ABC, abstractmethod
from typing import List, Optional
import logging 

logger = logging.getLogger(__name__)

#Role-Aliases dictionaries to support similar roles
ROLE_ALIASES = {
    "ml engineer": [
        "ml engineer", "machine learning engineer", "ai engineer",
        "applied scientist", "research engineer", "applied ml", "machine learning"
    ],
    "backend engineer": [
        "backend engineer", "software engineer", "api engineer",
        "platform engineer", "infrastructure engineer", "sde",
        "software development engineer", "backend"
    ],
    "frontend engineer": [
        "frontend engineer", "front-end engineer", "ui engineer",
        "react engineer", "web engineer", "frontend"
    ],
    "full stack engineer":[
        "full stack", "fullstack", "full-stack"
    ],
    "data engineer": [
        "data engineer", "analytics engineer", "data platform engineer", "data scientist"
    ],
}

@dataclass 
class JobListing:
    """ 
        Default Job-Listing model
        Every crawler returns a list of these, regardles of source portal
    """
    
    title:      str 
    company:    str 
    url:        str 
    portal:     str 
    jd_text:    str = ""
    location:   str = ""
    portal_job_id:  str = ""        #Portal specific ID for deduplication
    

class BaseCrawler(ABC):
    """ 
        Abstract base class for all portal crawlers
        Every crawler will:- 
            - Read its company slugs from portal.yml
            - Implements search(keyword) -> list[JobListing]
            - Caps results at max_results_per_run
            - Handles its own errors gracefully (one company failing doesn't kill the run)
    """
    def __init__(self, companies: List[str], max_results: int = 20):
        self.companies = companies
        self.max_results = max_results
        
    @abstractmethod
    async def search(self, keyword: str, location: Optional[str] = None) -> List[JobListing]:
        """ 
            Search all configured companies for jobs matching the keyword.
            Returns a deduplicated capped list of JobListing objects
        """
        
        pass 
    
    def _keyword_match(self, text: str, keyword: str) -> bool:
        """ 
            This function will check for case-insensitive keyword match against title + description
            Will support Comma-separated keywords (meaning it will match ANY keyword)
        """
        if not text:
            return False 
        
        text_lower = text.lower()
        
        #Expand comma-separated input terms
        input_terms = [t.strip().lower() for t in keyword.split(",") if t.strip()]
        
        #For each input term, we collect it + it's aliases
        all_terms = set()
        for term in input_terms:
            all_terms.add(term)
            for key, aliases in ROLE_ALIASES.items():
                if term == key or term in aliases:
                    all_terms.update(aliases)
        
        return any(term in text_lower for term in all_terms)
        