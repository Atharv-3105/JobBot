import httpx 
from typing import List, Optional
import asyncio 
import random 
import logging 
from portals.base import BaseCrawler, JobListing 

logger = logging.getLogger(__name__)

class HackerNewsCrawler(BaseCrawler):
    """ 
        This CRAWLS HackerNews jobs via their public JSON API
        ENDPOINT: GET https://hn.algolia.com/api/v1/search_by_data
    """
    
    async def search(self, keyword: str, location: Optional[str] = None) -> List[JobListing]:
        results: List[JobListing] = [] 
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            try:
                
                url = "https://hn.algolia.com/api/v1/search_by_date"
                
                #We Filter for Stories with > 5 points to avoid spam
                params = {
                    "query": keyword,
                    "tags": "story",
                    "numericFilters": "points>5",
                    "hitsPerPage": self.max_results
                } 
                
                logger.info(f"HackerNews: searching '{keyword}'")
                
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                
                hits = data.get("hits", [])
                if not hits:
                    logger.info(f"HackerNews: No open jobs for '{keyword}'")
                    return results 
                
                for hit in hits:
                    title = hit.get("title", "")
                    
                    #Check if "Show HN" and "Ask HN" posts then they are not real jobs, drop them
                    if title.lower().startswith(("show hn:", "ask hn:")):
                        logger.info(f"Hackernews: Dropping Post: '{title}' as it's not a job")
                        continue
                    
                    job_url = hit.get("url", "")
                    job_id = str(hit.get("objectID", ""))
                    
                    #HackerNews posts often have the JD in the story_text field
                    jd_text = hit.get("story_text", "") or hit.get("comment_text", "")
                    
                    #If No external URL is mentioned, we use the post url
                    if not job_url:
                        job_url = f"https://news.ycombinator.com/item?={job_id}"
                        
                    searchable_text = f"{title}"
                    
                    if not self._keyword_match(searchable_text, keyword):
                        continue 
                    
                    listing = JobListing(
                        title = title, 
                        company = 'Hacker News',
                        url = job_url,
                        portal = "hackernews",
                        jd_text = jd_text,
                        location = "Remote/Various",
                        portal_job_id = job_id
                    )
                    
                    results.append(listing)
                    
                    if len(results) >= self.max_results:
                        break 
                    
            except Exception as e:
                logger.warning(f"HackerNews: search failed: {e}")
                
        await asyncio.sleep(random.uniform(1.0, 2.0))
        return results[:self.max_results]
    
    