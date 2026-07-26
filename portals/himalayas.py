import httpx 
import asyncio
import random 
import logging 
from bs4 import BeautifulSoup 
from typing import Optional, List 
from portals.base import BaseCrawler, JobListing 

logger = logging.getLogger(__name__)

class HimalayasCrawler(BaseCrawler):
    """
        This CRAWLS Himalayas via JSON API 
        ENDPOINT: GET https://www.himalayas.app/jobs/api
    """
    
    #Himalayas allows filtering by q(keyword), country, seniority, employment_type, company, sort and page
    async def search(self, keyword: str, location: Optional[str] = None) -> List[JobListing]:
        results: List[JobListing] = []
        
        params = {
            "q": keyword,
            "limit": self.max_results
        }
        
        #Himalayas is remote-first so if location is "REMOTE" we won't filter but if a COUNTRY is requested, we filter it
        if location and location.lower() not in ("remote", "anywhere", "worldwide"):
            params["country"] = location
        
        try:
            async with httpx.AsyncClient(timeout = 30.0, follow_redirects=True) as client:
                
                url = "https://himalayas.app/jobs/api/search"
                
                logger.info(f"Himalayas: searching '{keyword}'")
                response = await client.get(url, params = params)
                response.raise_for_status()
                
                data = response.json()
                
                jobs = data.get("jobs", [])
                logger.info(f"Himalayas: Received {len(jobs)} jobs from API")
                
                if not jobs:
                    logger.info(f"HIMALAYAS: No open jobs for '{keyword}'")
                    return results 
                
                for job in jobs:
                    title = job.get("title", "")
                    
                    #Company is nested in Himalyas API
                    company_obj = job.get("company", {})
                    company = company_obj.get("name", "Unknown") if company_obj else "Unknown"
                    
                    job_url = job.get("url", "") or job.get("applyUrl", "")
                    job_id = str(job.get("id", ""))
                    
                    description_html = job.get("description", "")
                    jd_text = self._html_to_text(description_html)
                    
                    #We only search for title
                    searchable_text = f"{title}"
                    if not self._keyword_match(searchable_text, keyword):
                        continue 
                    
                    listing = JobListing(
                        title = title,
                        company = company,
                        url = job_url,
                        portal = "Himalayas",
                        jd_text = jd_text,
                        location = self._extract_location(job),
                        portal_job_id = job_id
                    )
                    
                    results.append(listing)
                    
                    if len(results) >= self.max_results:
                        break 
            
        except Exception as e:
            logger.warning(f"HIMALAYAS: Search Failed: {e}")
                
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
                    
    def _extract_location(self, job: dict) -> str:
        """ 
            Himalayas Job are remote-first 
        """                    
        worldwide = job.get("worldwideAvailability", False)
        if worldwide:
            return "Remote - Worldwide"
        
        restrictions = job.get("locationRestrictions", [])
        if restrictions:
            return ", ".join(restrictions[:3])
        
        
        return "Remote"
