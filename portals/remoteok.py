import httpx 
from typing import List, Optional 
import asyncio
import random 
import logging 
from bs4 import BeautifulSoup
from portals.base import BaseCrawler, JobListing 

logger = logging.getLogger(__name__)

class RemoteOkCrawler(BaseCrawler):
    """ 
        This CRAWLER crawls RemoteOK via their public JSON APi
        ENDPOINT: GET https://remoteok.com/api
    """
    
    async def search(self, keyword: str, location: Optional[str] = None) -> List[JobListing]:
        results: List[JobListing] = []
        
        #RemoteOk requires a real User-Agent
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://remoteok.com/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        
        async with httpx.AsyncClient(timeout = 30.0, headers = headers, follow_redirects=True) as client:
            try:
                
                url = "https://remoteok.com/api"
                
                params = {"search": keyword}
                
                logger.info(f"RemoteOk: fetching Jobs for '{keyword}'")
                
                response = await client.get(url, params = params)
                response.raise_for_status()
                
                #RemoteOk returns a List where the first item is a header object
                data = response.json()
                if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict) and "header" in data[0]:
                    #Skip the header object
                    jobs = data[1:]
                else:
                    jobs = data 
                    
                if not jobs:
                    logger.info(f"RemoteOk: No open Jobs found")
                    return results 
                
                for job in jobs:
                    title = job.get("position", "")
                    company = job.get("company", "")
                    job_url = job.get("url", "")
                    job_id = str(job.get("id", ""))
                    
                    #RemoteOk also returns a tag object with technologies tag, extract that for better keyword mapping
                    tags = job.get("tags", [])
                    tags_string = " ".join(tags).lower() if tags else ""
                    
                    #RemoteOk returns HTML Description
                    description_html = job.get("description", "")
                    jd_text = self._html_to_text(description_html)
                    
                    #Location is usually a string like "WorldWide" or "North America"
                    job_location = job.get("location", "")
                    
                    
                    searchable_text = f"{title} {tags_string}".lower()
                    if not self._keyword_match(searchable_text, keyword):
                        continue
                    
                    #Remoteok is remote-only, but we can filter by region if location is passed
                    if location and not self._keyword_match(job_location, location):
                        continue
                    
                    listing = JobListing(
                        title = title,
                        company = company,
                        url = job_url,
                        portal = "remoteok",
                        jd_text = jd_text,
                        location = job_location,
                        portal_job_id = job_id
                    )
                    
                    results.append(listing)
                    
                    if len(results) >= self.max_results:
                        break 
            except httpx.TimeoutException:
                logger.error("RemoteOk: Request Timed out. The server is blocking the request")
            except httpx.HTTPStatusError as e:
                logger.error(f"RemoteOk: HTTP ERROR {e.response.status_code}")
            except Exception as e:
                logger.warning(f"RemoteOk: search failed: {e}")
                
        #Add sleep to prevent rate-limiting 
        await asyncio.sleep(random.uniform(1.0, 2.0))
        return results[:self.max_results]
    
    def _html_to_text(self, html:str) -> str:
        if not html:
            return ""
        
        soup = BeautifulSoup(html, "html.parser")
        for element in soup(["script", "style"]):
            element.decompose()
            
        lines = [line.strip() for line in soup.get_text(separator="]n").splitlines() if line.strip()]
        return "\n".join(lines)