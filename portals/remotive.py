import httpx 
from typing import List, Optional 
import asyncio 
import random 
import logging 
from bs4 import BeautifulSoup
from portals.base import BaseCrawler, JobListing

logger = logging.getLogger(__name__)

class RemotiveCrawler(BaseCrawler):
    """ 
        This CRAWLS Remotive via JSON API
        ENDPOINT: GET https://remotive.com/api/remote-jobs
    """
    
    async def search(self, keyword: str, location: Optional[str] = None) -> List[JobListing]:
        results : List[JobListing] = []
        
        async with httpx.AsyncClient(timeout = 30.0) as client:
            
            try:
                #Remotive allows searching by keyword
                url = "https://remotive.com/api/remote-jobs"
                params = {"search": keyword, "limit": self.max_results}
                
                logger.info(f"REMOTIVE: searching '{keyword}'")
                response = await client.get(url, params = params)
                response.raise_for_status()
                data = response.json()
                
                jobs = data.get("jobs", [])
                if not jobs:
                    logger.info(f"REMOTIVE: No open jobs for '{keyword}'")
                    return results 
                
                for job in jobs:
                    title = job.get("title", "")
                    company = job.get("company_name", "")
                    job_url = job.get("url", "")
                    job_id = str(job.get("id", ""))
                    
                    #Remotive returns HTML description, clean it
                    description_html = job.get("description", "")
                    jd_text = self._html_to_text(description_html)
                    
                    #Category will act as a location/dept tag
                    category = job.get("category", "")
                    
                    #We won't match against JD-Text
                    searchable_text = f"{title}"
                    if not self._keyword_match(searchable_text, keyword):
                        continue 
                    
                    #Remotive is Remote-Only, but we can filter by category if location is passed
                    if location and not self._keyword_match(category, location):
                        continue 
                    
                    listing = JobListing(
                        title = title, 
                        company = company,
                        url = job_url, 
                        portal = "remotive",
                        jd_text = jd_text,
                        location = "Remote",
                        portal_job_id = job_id
                    )
                    
                    results.append(listing)
                    
                    if len(results) >= self.max_results:
                        break 
                    
            except Exception as e:
                logger.warning(f"REMOTIVE: search failed: {e}")
                
        #Add slepp inorder to not hit rate-limit
        await asyncio.sleep(random.uniform(1.0, 2.0))
        return results[:self.max_results]
    
    def _html_to_text(self, html: str) -> str:
        
        if not html:
            return ""
        
        soup = BeautifulSoup(html, 'html.parser')
        for element in soup(["script", "style"]):
            element.decompose()
        
        lines = [line.strip() for line in soup.get_text(separator="\n").splitlines() if line.strip()]
        return "\n".join(lines)