from typing import List, Optional 
import asyncio 
import logging 

from portals.base import BaseCrawler, JobListing 
from browser.session import create_stealth_context
from browser.human_sim import random_delay, random_scroll, human_warmup

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
        
        async with create_stealth_context(headless = False) as context:
            page = await context.new_page()
            
            try:
                #==================DEBUG=======================
                #Let's ADD request delay warm-up
                #Visit HomePage->Wait 5-8 Seconds->Visit one Job Listing Manually-->Then Search
                logger.info("Wellfound: Warming up session on homepage")
                await page.goto("https://wellfound.com", wait_until="domcontentloaded", timeout = 30000)
                
                #FIX: ADD HUMAN WARM-UP
                await human_warmup(page)
                await random_delay(3000, 6000)
    
                #Build the search URL
                role_slug = keyword.lower().strip().replace(" ", "-").replace(",", "")
                                
                if location:
                    location_slug = location.lower().strip().replace(" ", "-")
                    search_url = f"https://wellfound.com/role/l/{role_slug}/{location_slug}"
                else:
                    search_url = f"https://wellfound.com/role/r/{role_slug}"
                    
                logger.info(f"Wellfound searching '{keyword}' at {search_url}")
                    
                await page.goto(search_url, wait_until = "domcontentloaded", timeout = 60000)
                await random_delay(3000, 5000)
                
                #CHECK IF WE GOT BLOCKED
                page_title = await page.title()
                page_content = await page.content()
                
                if "temporarily restricted" in page_content.lower() or "unusual activity" in page_content.lower():
                    logger.error("Wellfound: Bot detection triggered - taking screenshot")
                    await page.screenshot(path = "wellfound_blocked.png")
                    return results
                
                logger.info(f"Wellfound: Page Loaded - title: '{page_title}'")
                await random_delay(2000, 4000)
                
                #Scroll a few time to trigger lazy loading
                for _ in range(3):
                    await random_scroll(page)
                    await random_delay(1000, 2000)
                    
                #Extract job cards from the search results page
                job_cards = await page.query_selector_all('[data-test="job-card"], .styles_jobCard_1y1s, a[href*="/job/"]')
                logger.info(f"Wellfound: Found {len(job_cards)} job cards")
                
                if not job_cards:
                    #Save Artifacts for DEBUGGING
                    # page_title = await page.title()
                    logger.warning(f"Wellfound: no job cards found for '{keyword}'.")
                    
                    ss_path = "wellfound_debug.png"
                    await page.screenshot(path = ss_path, full_page = True)
                    
                    with open("wellfound_page.html", "w", encoding = "utf-8") as f:
                        f.write(await page.content())
                        
                    logger.warning(f"Wellfound: No Job cards found - DEBUG FILES Saved.")
                    
                    #FallBack to try to get all links that look like job Posting
                    job_cards = await page.query_selector_all('a[href*="/job/"]')
                    if not job_cards:
                        logger.warning("Wellfound: Fallback selector found 0 results. Exiting")
                        return results 
                    
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
                import traceback 
                logger.error(traceback.format_exc())
        
        logger.info(f"Wellfound: found {len(results)} matching for '{keyword}'")
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
        useful_lines = [l for l in lines if len(l) > 3]
        title = useful_lines[0] if len(useful_lines) > 0 else "Unknown Title"
        company = useful_lines[1] if len(useful_lines) > 1 else "Unknown Company"
        location = useful_lines[2] if len(useful_lines) > 2 else ""
        
        return JobListing(title = title, company = company, url = url, portal = "wellfound",
                          jd_text = text_content, location = location, portal_job_id = href.split("/")[-1] if "/" in href else "")
        
        