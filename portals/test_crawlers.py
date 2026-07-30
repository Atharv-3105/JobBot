import asyncio 
import logging 
from portals import load_crawlers, search_all
from portals.greenhouse import GreenhouseCrawler
from portals.ashby import AshbyCrawler
from portals.lever import LeverCrawler
from portals.wellfound import WellfoundCrawler
from portals.remotive import RemotiveCrawler
from portals.himalayas import HimalayasCrawler
from portals.hackernews import HackerNewsCrawler
from portals.remoteok import RemoteOkCrawler


logging.basicConfig(level = logging.INFO, format = "%(name)s | %(message)s")

async def test_greenhouse_only():
    """ 
        Focused test: Search Greenhouse for 'engineer' at Stripe
        Simple test, pure HTTP 
    """
    
    print("="*75)
    print("TEST: Greenhouse Crawler (single company)")
    print("="*75)
    
    crawler = GreenhouseCrawler(companies = ["stripe"], max_results=5)
    results = await crawler.search(keyword="engineer")
    
    print(f"\nFOUND {len(results)} matchings jobs:\n")
    for i, job in enumerate(results, 1):
        print(f"    {i}. {job.title}")
        print(f"    Company: {job.company}")
        print(f"    Location: {job.location}")
        print(f"    URL:   {job.url}")
        print(f"    JD Length: {len(job.jd_text)} chars")
        
    return results 

async def test_ashby():
    print("="*75)
    print("TEST:Ashby Crawler")
    print("="*75)
    
    crawler = AshbyCrawler(companies = ["ramp"], max_results=10)
    results = await crawler.search(keyword="engineer")
    
    print(f"\nFOUND {len(results)} matchings jobs:\n")
    for i, job in enumerate(results, 1):
        print(f"    {i}. {job.title}")
        print(f"    Company: {job.company}")
        print(f"    Location: {job.location}")
        print(f"    URL:   {job.url}")
        print(f"    JD Length: {len(job.jd_text)} chars")
        
async def test_lever():
    print("="*75)
    print("TEST:Lever Crawler")
    print("="*75)
    
    crawler = LeverCrawler(companies = ["paytm"], max_results=10)
    results = await crawler.search(keyword="engineer")
    
    print(f"\nFOUND {len(results)} matchings jobs:\n")
    for i, job in enumerate(results, 1):
        print(f"    {i}. {job.title}")
        print(f"    Company: {job.company}")
        print(f"    Location: {job.location}")
        print(f"    URL:   {job.url}")
        print(f"    JD Length: {len(job.jd_text)} chars")
        

async def test_wellfound():
    print("="*75)
    print("TEST:WellFound Crawler")
    print("="*75)
    
    crawler = WellfoundCrawler(companies = ["paytm"], max_results=10)
    results = await crawler.search(keyword="engineer")
    
    print(f"\nFOUND {len(results)} matchings jobs:\n")
    for i, job in enumerate(results, 1):
        print(f"    {i}. {job.title}")
        print(f"    Company: {job.company}")
        print(f"    Location: {job.location}")
        print(f"    URL:   {job.url}")
        print(f"    JD Length: {len(job.jd_text)} chars")
        
        
async def test_remotive():
    print("="*75)
    print("TEST: Remotive Crawler")
    print("="*75)
    
    crawler = RemotiveCrawler(companies=[], max_results=5)
    results = await crawler.search('engineer')
    
    print(f"\n FOUND: {len(results)} matching jobs:\n")
    for i, job in enumerate(results, 1):
        print(f"    {i}. {job.title}")
        print(f"    Company: {job.company}")
        print(f"    Location: {job.location}")
        print(f"    URL:    {job.url}")
        print(f"    JD-Length: {len(job.jd_text)} chars")
        
async def test_himalayas():
    print("="*75)
    print("TEST: Himalayas Crawler")
    print("="*75)
    
    crawler = HimalayasCrawler(companies=[], max_results=5)
    results = await crawler.search('engineer', 'India')
    
    print(f"\n Found: {len(results)} matching jobs")
    for i, job in enumerate(results, 1):
        print(f"    {i}. {job.title}")
        print(f"    Company: {job.company}")
        print(f"    Location: {job.location}")
        print(f"    URL:    {job.url}")
        print(f"    JD-Length:  {len(job.jd_text)} chars")
        
        
async def test_hackernews():
    print("="*75)
    print("TEST: HackerNews Crawler")
    print("="*75)
    
    crawler = HackerNewsCrawler(companies = [], max_results=5)
    results = await crawler.search('machine learning')
    
    print(f"\n Found: {len(results)} matching jobs")
    for i, job in enumerate(results, 1):
        print(f"    {i}. {job.title}")
        print(f"    Company: {job.company}")
        print(f"    Location: {job.location}")
        print(f"    URL:    {job.url}")
        print(f"    JD-Length:  {len(job.jd_text)} chars")
        
    
async def test_remoteok():
    print("="*75)
    print("TEST: RemoteOk Crawler")
    print("="*75)
    
    crawler = RemoteOkCrawler(companies = [], max_results = 5)
    results = await crawler.search('engineer')
    
    print(f"\n Found: {len(results)} matching jobs")
    for i, job in enumerate(results, 1):
        print(f"    {i}. {job.title}")
        print(f"    Company: {job.company}")
        print(f"    Location: {job.location}")
        print(f"    URL:    {job.url}")
        print(f"    JD-Length: {len(job.jd_text)} chars")
        
        
            
    

async def test_all_crawlers():
    """ 
        Integration test: load all crawlers from portals.yml and search
    """
    
    print("="*75)
    print("TEST: All Crawlers")
    print("="*75)
    
    crawlers = load_crawlers("config/portals.yml", max_results = 20)
    results = await search_all(crawlers, keyword="engineer")
    
    print(f"\n Total results across all portals: {len(results)}\n")
    for i, job in enumerate(results, 1):
        print(f" {i}. [{job.portal.upper()}] {job.title}")
        print(f"    {job.company} - {job.location}")
        print(f"    {job.url}")
        print()
        


if __name__ == "__main__":
    # asyncio.run(test_greenhouse_only())
    
    # asyncio.run(test_ashby())
    
    # asyncio.run(test_lever())
    
    # asyncio.run(test_wellfound())
    
    # asyncio.run(test_remotive())
    
    # asyncio.run(test_himalayas())
    
    # asyncio.run(test_hackernews())
    
    # asyncio.run(test_remoteok())
    
    asyncio.run(test_all_crawlers())