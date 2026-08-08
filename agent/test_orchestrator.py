import asyncio 
import logging 
from dotenv import load_dotenv
from agent.orchestrator import pipeline
from portals.base import JobListing
from agent.nodes.scorer import ScoredJob
from db import save_job, get_db


load_dotenv()
logging.basicConfig(level = logging.INFO, format = "%(name)s | %(message)s")

MOCK_PROFILE = {
    "name": "Atharva",
    "target_roles": ["Backend Engineer", "ML Engineer"],
    "experience_years": 1,
    "skills": {
        # Align with the 4-tier structure expected by the Scorer
        "core": ["Python", "Go", "FastAPI", "Docker"],
        "primary": ["LangChain", "LangGraph", "RAG", "ChromaDB", "MongoDB", "Redis", "PostgreSQL"],
        "secondary": [],
        "basic": []
    }
}

MOCK_PORTALS = "config/portals.yml"
async def test_pipeline():
    print("="*60)
    print("TEST: LangGraph Orchestrator (End-To-End)")
    print("="*60)
    
    
    initial_state = {
        "user_id": 12345,
        "keyword": "Python, ML Engineer",
        "location": "Remote",
        "profile": MOCK_PROFILE,
        "portals": MOCK_PORTALS,
        "base_tex_path": "config/base_resume.tex",
        "mode": "score",
        "raw_jobs": [],
        "scored_jobs": [],
        "tailored_jobs": [],
        "final_report": "",
        "error": None 
    }
    
    
    print("\nStarting Pipeline.......")
    
    final_state = await pipeline.ainvoke(initial_state)
    
    print("\n" + "=" * 60)
    print("FINAL REPORT: ")
    print("=" * 60)
    print(final_state["final_report"])
  
async def test_score_only():
    print("="*60)
    print("TEST 2: Score only (Skip Crawl, Inject 1 JD)")
    print("="*60)
    
    #We must inject a raw-job because we are skipping the crawler
    mock_jd ="We are looking for a Senior ML Engineer. Requirements: Python, PyTorch, LangChain, RAG, FastAPI."
    dummy_job = JobListing(
        title = "ML Engineer",
        company = "AI startup",
        url = "Direct input test",
        portal = "manual",
        jd_text = mock_jd,
        portal_job_id = "test_01"
    )
    
    initial_state = {
        "user_id": 12345,
        "keyword": "ML Engineer",
        "location": "Remote",
        "profile": MOCK_PROFILE,
        "portals": "config/portals.yml",
        "base_tex_path": "config/base_resume.tex",
        "mode": "score",
        "raw_jobs": [dummy_job],
        "scored_jobs": [],
        "tailored_jobs": [],
        "final_report": "",
        "error": None,
    }
    
    final_state = await pipeline.ainvoke(initial_state)
    print(final_state['final_report'])
    
    
async def test_tailor_only():
    print("="*60)
    print("TEST 3: Tailor Only (Skip Crawl & Score, force tailor 1 JD)")
    print("="*60)
    
    mock_jd = "Looking for a Python backend developer with FastAPI and Docker experience."
    dummy_job = JobListing(
            title="Backend Developer", 
            company="Test Corp", 
            url="direct_input_tailor_test", 
            portal="manual", 
            jd_text=mock_jd, 
            portal_job_id="test_02"
    )
    
    with get_db() as db:
        db_job = save_job(db, 12345, dummy_job.title, dummy_job.company, dummy_job.url,dummy_job.portal, dummy_job.jd_text)
        mock_db_id = db_job.id 
        
    # We pretend the job scored an 'A' so it passes the conditional edge
    dummy_scored = ScoredJob(
        job=dummy_job, 
        db_job_id=mock_db_id, 
        score="A", 
        match_percentage=95, 
        strengths=["Python match"], 
        gaps=[], 
        recommendation="Apply"
    ) 
    
    initial_state = {
        "user_id": 12345,
        "keyword": "Backend Developer",
        "location": "Remote",
        "profile": MOCK_PROFILE,
        "portals": "config/portals.yml",
        "base_tex_path": "config/base_resume.tex",
        "mode": "tailor",            # ROUTE TO TAILOR_NODE
        "raw_jobs": [dummy_job], 
        "scored_jobs": [dummy_scored], # INJECTED DATA
        "tailored_jobs": [],
        "final_report": "",
        "error": None 
    }
    
    final_state = await pipeline.ainvoke(initial_state)
    print(final_state["final_report"])
        
    
      

if __name__ == "__main__":
    # asyncio.run(test_pipeline())
    # asyncio.run(test_score_only())
    asyncio.run(test_tailor_only())