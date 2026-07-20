import httpx 
from bs4 import BeautifulSoup
from typing import List, Optional
import asyncio
import random 
import logging 

from portals.base import BaseCrawler, JobListing

logger = logging.getLogger(__name__)

BASE_URL = "https://boards-api.greenhouse.io/v1/boards"

class GreenhouseCrawler(BaseCrawler):
    """ 
        Crawls GreenHouse ATS via the publicly available JSON API.
        No authentication needed for this, No stealth needed.
        
        Crawler Flow Per Company:
            1. GET /boards/{slug}/jobs  => list of all open jobs(title + metadata)
            2. Filter by keyword in title 
            3. For matches, GET /board/{slug}/jobs/{job_id} => full JD(HTML)
            4. Convert HTML into clean TEXT
    """ 
    
    async def search(self, keyword: str, location: Optional[str] = None) -> List[JobListing]:
        
        results:  List[JobListing] = []
        
        async with httpx.AsyncClient(timeout = 30.0) as client:
            for company_slug in self.companies:
                
                #Condition to check if crawled jobs are withing max_results_per_run or not
                if len(results) >= self.max_results:
                    break
                
                try:
                    listings = await self._search_company(client, company_slug, keyword, location)
                    results.extend(listings)
                    
                except Exception as e:
                    #One company failing must not KILL the whole run
                    logger.warning(f"Greenhouse: failed to crawl '{company_slug}': {e}")
                    continue
                
                #Add Rate-Limit: 2-5 seconds delay between companies
                await asyncio.sleep(random.uniform(2.0, 5.0))
                
        return results[:self.max_results]
    
    
    async def _search_company(self, client: httpx.AsyncClient, slug: str, keyword: str, location: Optional[str]) -> List[JobListing]:
        """ 
            This function searches for open jobs given a company
        """
        results = []
        
        #Step1: GET all open Jobs for this company
        url = f"{BASE_URL}/{slug}/jobs"
        response = await client.get(url, params = {"content": "true"})
        response.raise_for_status()
        data = response.json()
        
        jobs = data.get("jobs", [])
        if not jobs:
            logger.info(f"Greenhouse: no open jobs for '{slug}'")
            return results 
        
        #Step2: Filter by keyword and location if metioned
        for job in jobs: 
            title = job.get("title", "")
            job_location = self._extract_location(job)
            
            #Check keyword match in title
            if not self._keyword_match(title, keyword):
                continue 
            
            #Check location match if specified
            if location and not self._keyword_match(job_location, location):
                continue 
            
            #Step3: Extract and clean the JD
            raw_content = job.get("content", "")
            jd_text = self._html_to_text(raw_content)
            
            listing = JobListing(title = title, company = slug.replace("-", " ").title(),
                                 url = job.get("absolute_url", ""), portal = "greenhouse",
                                 jd_text = jd_text, location = job_location, portal_job_id = str(job.get("id", "")))
            
            results.append(listing)
            
            
        return results 
    
    
    def _extract_location(self, job: dict) -> str: 
        """ 
            This function is used to extract location string from GreenHouse job object.
        """
        
        location_obj = job.get("locations", {})
        if isinstance(location_obj, dict):
            return location_obj.get("name", "")
        
        return str(location_obj) if location_obj  else ""
    
    def _html_to_text(self, html: str) -> str: 
        """ 
            Convert HTML job description to clean plain text.
        """
        
        if not html:
            return ""
        
        soup = BeautifulSoup(html, "html.parser")
        
        #Remove script and style elements
        for element in soup(["script", "style"]):
            element.decompose()
            
        #Get text with newlines preserved
        text = soup.get_text(separator = "\n", strip = True)
        
        #Collapse excessive blank lines
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)
    
            