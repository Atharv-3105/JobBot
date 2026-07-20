import httpx 
from typing import List, Optional 
import asyncio
import logging 
import random 

from portals.base import BaseCrawler, JobListing

logger = logging.getLogger(__name__)

BASE_URL = "https://api.lever.co/v0/postings"

class LeverCrawler(BaseCrawler):
    """ 
        Crawls Lever ATS via it's publicly available JSON API
        No authentication required, No stealth needed.
        
        RETURNS all open postings.
    """
    
    async def search(self, keyword: str, location: Optional[str] = None) -> List[JobListing]:
        
        results: List[JobListing] = []
        
        async with httpx.AsyncClient(timeout = 30.0) as client:
            for company_slug in self.companies:
                #Condition to check if crawled jobs are withing max_results_per_run or not
                if len(results) >= self.max_results:
                    break 
                
                try:
                    listings = await self._search_company(client, company_slug, keyword, location)
                    results.extend(listings)
                except Exception as e:
                    logger.warning(f"Lever: failed to crawl '{company_slug}': {e}")
                    continue 
                
                await asyncio.sleep(random.uniform(2.0, 5.0))
                
        return results[:self.max_results]
    
    async def _search_company(self, client: httpx.AsyncClient, slug: str, keyword: str, location: Optional[str]) -> List[JobListing]:
        
        results = []
        
        url = f"{BASE_URL}/{slug}"
        response = await client.get(url, params = {"mode": "json"})
        response.raise_for_status()
        jobs = response.json()
        
        if not isinstance(jobs, list):
            logger.warning(f"Lever: unexpected response format for '{slug}'")
            return results 
        
        for job in jobs:
            title = job.get("text", "")
            categories = job.get("categories", {})
            job_location = categories.get("location", "")
            description = job.get("descriptionPlain", "")
            apply_url = job.get("applyUrl", "")
            
            #Keyword match against title + description
            searchable_text = f"{title} {description}"
            if not self._keyword_match(searchable_text, keyword):
                continue 
            
            #Search for location if mentioned 
            if location and not self._keyword_match(job_location, location):
                continue 
            
            listing = JobListing(title = title, company = slug.replace("-", " ").title(),
                                 url = apply_url, portal = "lever", jd_text = description, location = job_location,
                                 portal_job_id = str(job.get("id", "")))
            
            results.append(listing)
            
        return results
                
                