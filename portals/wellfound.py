from typing import List, Optional 
import asyncio 
import logging 

from portals.base import BaseCrawler, JobListing 
from browser.session import create_stealth_context
from browser.human_sim import random_delay, random_scroll

logger = logging.getLogger(__name__)

class WellfoundCrawler(BaseCrawler):
    """ 
        Crawls WellFound using Playwright
        
        Since WellFound does not have public API, so we use the stealth browser session

        FLOW:
            1- Navigate to Wellfound.com/jobs with search query
            2- Scroll to load results(they are lazy-loaded)
            3- Extract job cards from the DOM
            4- For each card, extract title, company, location, url
    """
    
    async def search(self, keyword:str, location: Optional[str] = None) -> List[JobListing]:
        results: List[JobListing] = []
        
        async with create_stealth_context(headless = True) as context:
            page = await context.new_page()
            
            try:
                #Build the search URL
                search_url = f"https://wellfound.com/role/l/{keyword.lower().replace(' ', '-')}"
                
                if location:
                    search_url += f"/{location.lower().replace(' ', '-')}"
                    
                await page.goto(search_url, wait_until = "networkidle", timeout = 30000)
                await random_delay(2000, 4000)
                
                #Scroll a few time to trigger lazy loading
                for _ in range(3):
                    await random_scroll(page)
                    await random_delay(1000, 2000)
                    
                #Extract job cards from the search results page
                job_cards = await page.query_selector_all('[data-test="job-card"], .styles_jobCard_1y1s, a[href*="/job/"]')
                
                if not job_cards:
                    #FallBack to try to get all links that look like job Posting
                    job_cards = await page.query_selector_all('a[href*="/job/"]')
                    
                for card in job_cards[:self.max_results]:
                    try:
                        
                        listing = await self._extract_job_from_card(page, card)
                        if listing and self._keyword_match(listing.title, keyword):
                            results.append(listing)
                    
                    except Exception as e:
                        logger.debug(f"Wellfound: failed to extract a job-card: {e}")
                        continue 
                    
                    
                    if len(results) >= self.max_results:
                        break 
                    
                    
            except Exception as e:
                logger.error(f"Wellfound: search failed for keyword '{keyword}': {e}")
        
        return results[:self.max_results]
    
    
    async def _extract_job_from_card(self, page, card) -> Optional[JobListing]:
        """
            This function is used to extract Job Details from a single Job Card DOM element
        """
        
        href = await card.get_attribute("href")
        if not href:
            #If the card isn't a link, find the link inside it
            link = await card.query_selector("a[href*='/job/']")
            if link:
                href = await link.get_attribute("href")
                
        
        if not href:
            return None 
        
        #Build the absolute URL
        if href.startswith("/"):
            url = f"https://wellfound.com{href}"
        else:
            url = href 
            
            
        #Extract text-content from the Card
        text_content = await card.inner_text()
        lines = [line.strip() for line in text_content.split("\n") if line.strip()]
        
        #Heuristic parsing: first line is usually title, second is company 
        title = lines[0] if len(lines) > 0 else "Unknown Title"
        company = lines[1] if len(lines) > 1 else "Unknown Company"
        location = lines[2] if len(lines) > 2 else ""
        
        return JobListing(title = title, company = company, url = url, portal = "wellfound",
                          jd_text = text_content, location = location, portal_job_id = href.split("/")[-1] if "/" in href else "")