from dataclasses import dataclass, field 
from abc import ABC, abstractmethod
from typing import List, Optional
import logging 
import re

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
    def __init__(self, companies: List[str], max_results: int = 5):
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
            This function will check for case-insensitive keyword match against text
            Will support Comma-separated keywords (meaning it will match ANY keyword) OR LOGIC
            Will support Space-separated keywords (meaning it will match ALL keywords) AND LOGIC
            Also supports terms using ROLE_ALIASES to catch variations
        """
        if not text:
            return False 
        
        text_lower = text.lower()
        keyword_lower = keyword.lower()
        
        #1. Comma Separated Logic 
        if "," in keyword_lower:
            phrases = [phrase.strip() for phrase in keyword_lower.split(",") if phrase.strip()]
            for phrase in phrases:
                variations = self.get_variations(phrase)
                
                #If ANY of the expanded variations are in the text, it's A MATCH
                if any(var in text_lower for var in variations):
                    return True 
            return False 
        
        #2. Space-separated Keyword
        #First we check whether the entire phrase (or its aliases) exists contiguously
        variations = self.get_variations(keyword_lower)
        if any(var in text_lower for var in variations):
            return True 
        
        #Fallback AND Logic: Require all individual words to be present
        terms = [t.strip() for t in keyword_lower.split() if t.strip()]
        if not terms:
            return False 
        
        return all(term in text_lower for term in terms)
           
    
    def get_variations(self, phrase: str) -> set:
        """ 
            Helper function to get all variations of a phrase using ROLE_ALIASES dict
        """
        variations = {phrase}
        if phrase in ROLE_ALIASES:
            variations.update(ROLE_ALIASES[phrase])
        else:
            for key, aliases in ROLE_ALIASES.items():
                if phrase in aliases:
                    variations.update(aliases)
                    break 
                
        return variations