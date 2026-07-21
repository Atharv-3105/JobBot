import httpx 
from typing import List, Optional
import asyncio
import random 
import logging 
from bs4 import BeautifulSoup

from portals.base import BaseCrawler, JobListing 

logger = logging.getLogger(__name__)

BASE_URL = "https://api.ashbyhq.com/posting-api/job-board"

class AshbyCrawler(BaseCrawler):
    """ 
        Crawls Ashby ATS via their public Job Board API.
        No authentication needed, No stealth needed.
        
        Endpoint: GET /api/non-user-portal/job-board/{company}
        RETURNS a JSON object with a 'JobPostings' array
    """
    
    async def search(self, keyword: str, location: Optional[str] = None) -> List[JobListing]:
        
        results: List[JobListing] = []
        
        async with httpx.AsyncClient(timeout = 30.0) as client: 
            for company_slug in self.companies:
                if len(results) >= self.max_results:
                    break 
                
                try:
                    listings = await self._search_company(client, company_slug, keyword, location)
                    results.extend(listings)
                except Exception as e:
                    logger.warning(f"Ashby: failed to crawl '{company_slug}' : {e}")
                    continue 
                
                
                await asyncio.sleep(random.uniform(2.0, 5.0))
                
            return results[:self.max_results]
        
        
    async def _search_company(self, client: httpx.AsyncClient, slug: str, keyword: str, location: Optional[str]) -> List[JobListing]:
        
        results = []
        
        url = f"{BASE_URL}/{slug}"
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()
        
        jobs = data.get("jobs", [])
        if not jobs:
            logger.info(f"Ashby: No open JOBS for '{slug}'")
            return results 
        
        for job in jobs:
            title = job.get("title", "")
            job_location = job.get("location", "")
            
            #Ashby has description under descriptionHTML
            description = job.get("descriptionPlain", "")
            if not description:
                description_html = job.get("descriptionHtml", "") or job.get("description", "")
                description = self._html_to_text(description_html)
                
            job_id = job.get("id", "")
            
            #Build the canonical URL for this Job
            job_url = job.get("jobUrl", f"https://jobs.ashbyhq.com/{slug}/{job_id}")
            
            searchable_text = f"{title} {description}"
            if not self._keyword_match(searchable_text, keyword):
                continue 
            
            if location and not self._keyword_match(job_location, location):
                continue 
            
            listing = JobListing(title = title, company = slug.replace("-", " ").title(),
                                 url = job_url, portal = "ashby", jd_text = description,
                                 location = job_location, portal_job_id = str(job_id))
            
            results.append(listing)
            
        return results 
    
    def _html_to_text(self, html: str) -> str:
        if not html:
            return ""
        
        soup = BeautifulSoup(html, "html.parser")
        for element in soup(["script", "style"]):
            element.decompose()
            
        lines = [line.strip() for line in soup.get_text(separator = "\n").splitlines() if line.strip()]
        return "\n".join(lines)