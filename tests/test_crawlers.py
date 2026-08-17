import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from portals.base import BaseCrawler, JobListing
from portals import load_crawlers, search_all
from portals.greenhouse import GreenhouseCrawler
from portals.lever import LeverCrawler
from portals.ashby import AshbyCrawler
from portals.remotive import RemotiveCrawler
from portals.himalayas import HimalayasCrawler
from portals.hackernews import HackerNewsCrawler
from portals.remoteok import RemoteOkCrawler

# Create a concrete dummy crawler for testing BaseCrawler methods
class DummyCrawler(BaseCrawler):
    async def search(self, keyword, location=None):
        return []

def test_keyword_match():
    """Test BaseCrawler's keyword matching logic, aliases, and AND/OR matching."""
    crawler = DummyCrawler(companies=[], max_results=5)
    
    # 1. Simple match (case-insensitive)
    assert crawler._keyword_match("Senior Python Developer", "python") is True
    assert crawler._keyword_match("Senior Python Developer", "Java") is False
    
    # 2. Comma-separated OR match
    assert crawler._keyword_match("Golang Engineer", "python, go, java") is True
    assert crawler._keyword_match("Golang Engineer", "c++, rust") is False
    
    # 3. Space-separated AND match
    assert crawler._keyword_match("Full Stack Python Developer", "python developer") is True
    assert crawler._keyword_match("Full Stack Python Developer", "python rust") is False
    
    # 4. Role aliases matching (e.g., "ml engineer" matches "machine learning engineer")
    assert crawler._keyword_match("Senior Machine Learning Engineer", "ml engineer") is True
    assert crawler._keyword_match("Senior Applied Scientist", "ml engineer") is True
    assert crawler._keyword_match("Backend Software Engineer", "backend engineer") is True

@pytest.mark.asyncio
async def test_greenhouse_crawler():
    """Test Greenhouse API crawler parser logic with mocked HTTP responses."""
    crawler = GreenhouseCrawler(companies=["testcorp"], max_results=2)
    
    mock_jobs_response = {
        "jobs": [
            {
                "id": 123,
                "title": "Backend Software Engineer",
                "absolute_url": "https://greenhouse/testcorp/123",
                "location": {"name": "Remote, US"},
                "content": "<h1>Job Description</h1><p>We need Python and SQL.</p>"
            },
            {
                "id": 124,
                "title": "Product Designer",
                "absolute_url": "https://greenhouse/testcorp/124",
                "location": {"name": "New York"},
                "content": "Design role details"
            }
        ]
    }
    
    mock_response = MagicMock()
    mock_response.json.return_value = mock_jobs_response
    mock_response.raise_for_status = MagicMock()
    
    with patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_response)):
        results = await crawler.search("backend engineer")
        
        assert len(results) == 1
        job = results[0]
        assert job.title == "Backend Software Engineer"
        assert job.company == "Testcorp"
        assert job.url == "https://greenhouse/testcorp/123"
        assert job.portal == "greenhouse"
        assert "Python and SQL" in job.jd_text
        assert job.location == "Remote, US"
        assert job.portal_job_id == "123"

@pytest.mark.asyncio
async def test_lever_crawler():
    """Test Lever API crawler parser logic with mocked HTTP responses."""
    crawler = LeverCrawler(companies=["testcorp"], max_results=2)
    
    mock_lever_response = [
        {
            "id": "abc-123",
            "title": "ML Engineer",
            "hostedUrl": "https://lever/testcorp/abc-123",
            "categories": {"location": "Remote"},
            "descriptionPlain": "Build PyTorch and FastAPI applications."
        }
    ]
    
    mock_response = MagicMock()
    mock_response.json.return_value = mock_lever_response
    mock_response.raise_for_status = MagicMock()
    
    with patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_response)):
        results = await crawler.search("ml engineer")
        assert len(results) == 1
        job = results[0]
        assert job.title == "ML Engineer"
        assert job.portal == "lever"
        assert "PyTorch" in job.jd_text
        assert job.location == "Remote"
        assert job.portal_job_id == "abc-123"

@pytest.mark.asyncio
async def test_ashby_crawler():
    """Test Ashby crawler parser logic using GraphQL mock."""
    crawler = AshbyCrawler(companies=["testcorp"], max_results=2)
    
    mock_graphql_response = {
        "data": {
            "jobBoard": {
                "jobs": [
                    {
                        "id": "ashby-id-1",
                        "title": "Security Engineer",
                        "jobBoardUrl": "https://ashby/testcorp/ashby-id-1",
                        "locationName": "San Francisco",
                        "descriptionHtml": "Protect our cloud architecture."
                    }
                ]
            }
        }
    }
    
    mock_response = MagicMock()
    mock_response.json.return_value = mock_graphql_response
    mock_response.raise_for_status = MagicMock()
    
    with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_response)):
        results = await crawler.search("Security")
        assert len(results) == 1
        job = results[0]
        assert job.title == "Security Engineer"
        assert job.portal == "ashby"
        assert "cloud architecture" in job.jd_text
        assert job.location == "San Francisco"

@pytest.mark.asyncio
async def test_remotive_crawler():
    """Test Remotive API crawler with mock response."""
    crawler = RemotiveCrawler(companies=[], max_results=2)
    
    mock_api_response = {
        "jobs": [
            {
                "id": 999,
                "title": "Senior Python Developer",
                "url": "https://remotive.com/999",
                "company_name": "RemotiveCorp",
                "candidate_required_location": "Worldwide",
                "description": "Python, Django, AWS"
            }
        ]
    }
    mock_response = MagicMock()
    mock_response.json.return_value = mock_api_response
    mock_response.raise_for_status = MagicMock()
    
    with patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_response)):
        results = await crawler.search("Python")
        assert len(results) == 1
        job = results[0]
        assert job.title == "Senior Python Developer"
        assert job.company == "RemotiveCorp"
        assert job.portal == "remotive"

@pytest.mark.asyncio
async def test_search_all_dedup():
    """Test search_all correctly consolidates and deduplicates jobs by URL."""
    crawler1 = DummyCrawler(companies=[], max_results=5)
    crawler2 = DummyCrawler(companies=[], max_results=5)
    
    job1 = JobListing(title="Job A", company="Co A", url="https://dup-url", portal="crawler1")
    job2 = JobListing(title="Job A Dup", company="Co A", url="https://dup-url", portal="crawler2")
    job3 = JobListing(title="Job B", company="Co B", url="https://unique-url", portal="crawler1")
    
    crawler1.search = AsyncMock(return_value=[job1, job3])
    crawler2.search = AsyncMock(return_value=[job2])
    
    results = await search_all([crawler1, crawler2], keyword="test")
    
    # Check that job2 (duplicate URL) was filtered out
    assert len(results) == 2
    urls = [j.url for j in results]
    assert "https://dup-url" in urls
    assert "https://unique-url" in urls
