import yaml
import logging 
from typing import List
from pathlib import Path 

from portals.base import BaseCrawler, JobListing
from portals.greenhouse import GreenhouseCrawler
from portals.lever import LeverCrawler
from portals.ashby import AshbyCrawler
from portals.wellfound import WellfoundCrawler
from portals.remotive import RemotiveCrawler
from portals.himalayas import HimalayasCrawler

logger = logging.getLogger(__name__)

#mapping portal names to their crawler classes
CRAWLER_REGISTRY = {
    "greenhouse": GreenhouseCrawler,
    "lever": LeverCrawler,
    "ashby": AshbyCrawler,
    "wellfound": WellfoundCrawler,
    "remotive": RemotiveCrawler,
    "himalayas": HimalayasCrawler
}

def load_crawlers(config_path: str = "config/portals.yml", max_results: int = 20) -> List[BaseCrawler]:
    """ 
        Read portals.yml and instantiate all enabled crawlers
        
        ARGS: Path to portals.yml, Max jobs to return per crawler per run
        
        RETURNS: List of initialized crawler instances
    """
    
    
    path = Path(config_path)
    if not path.exists():
        logger.error(f"Portal config not found at {config_path}")
        return []
    
    with open(path, "r") as f:
        config = yaml.safe_load(f)
        
    crawlers = []
    portals_config = config.get("portals", {})
    
    for portal_name, portal_config in portals_config.items():
        if not portal_config.get("enabled", False):
            continue 
        
        crawler_class = CRAWLER_REGISTRY.get(portal_name)
        if not crawler_class:
            logger.warning(f"Unknown portal '{portal_name}' in config - skipping")
            continue 
        
        companies = portal_config.get("companies", [])
        
        #Wellfound does not use company slug, so we need to search globally
        if portal_name in ["remotive", "wellfound", "himalayas"]:
            crawlers.append(crawler_class(companies=[], max_results = max_results))
        else:
            if not companies:
                logger.warning(f"Portal '{portal_name}' is enabled but has no companies - skipping")
                continue 
            crawlers.append(crawler_class(companies = companies, max_results = max_results))
            
    logger.info(f"Loaded {len(crawlers)} crawlers: {[type(c).__name__ for c in crawlers]}")
    return crawlers 



async def search_all(crawlers: List[BaseCrawler], keyword: str, location: str = None) -> List[JobListing]:
    """ 
        This function RUNS all the crawlers sequentially
        We are not running parallely inorder to avoid RATE-LIMITING

        ARGS:
            crawlers: List of initialized crawler instances
            keyword: Search Keyword
            location: Optional location filter 
            
        RETURNS:
            Combined, deduplicated list of JobListing objects
    """
    
    all_results: List[JobListing] = []
    
    #We use set inorder to implement deduplication of URLS from different-different portals
    seen_urls = set()
    
    for crawler in  crawlers:
        try:
            results = await crawler.search(keyword, location)
            for listing in results:
                #Deduplicate By URL 
                if listing.url and listing.url not in seen_urls:
                    seen_urls.add(listing.url)
                    all_results.append(listing)
                    
        except Exception as e:
            logger.error(f"Crawler {type(crawler).__name__} failed: {e}")
            continue 
        
    return all_results



__all__ = ["BaseCrawler", "JobListing", "GreenhouseCrawler", "LeverCrawler", "AshbyCrawler", "WellfoundCrawler","RemotiveCrawler", "HimalayasCrawler", "load_crawlers", "search_all"]