import pytest
import asyncio
import os
import tempfile
import shutil
from datetime import datetime

# DB Imports
from db import init_db, save_user, save_job, get_db
from db.models import JobStatus

# Agent Imports
from agent.orchestrator import pipeline
from agent.state import AgentState
from portals.base import JobListing
from agent.nodes.scorer import ScoredJob

# --- FIXTURES ---

@pytest.fixture(scope="session")
def test_env():
    """Setup temporary environment and database for the test session"""
    init_db()
    
    # Create a temporary directory for test user files
    test_dir = tempfile.mkdtemp()
    user_dir = os.path.join(test_dir, "12345")
    os.makedirs(user_dir, exist_ok=True)
    
    # Create a minimal, valid base_resume.tex that pdflatex can compile
    resume_content = r"""
\documentclass{article}
\begin{document}
\section*{Summary}
AI Engineer experienced in Python and FastAPI.
\section*{Skills}
Python, Go, Docker, LangChain
\section*{Experience}
Software Engineer at TestCorp.
\end{document}
"""
    resume_path = os.path.join(user_dir, "base_resume.tex")
    with open(resume_path, "w", encoding="utf-8") as f:
        f.write(resume_content)
        
    # Create a mock user in the DB
    mock_skills = {
        "core": ["Python", "Go", "FastAPI", "Docker"],
        "primary": ["LangChain", "RAG"],
        "secondary": [],
        "basic": []
    }
    with get_db() as db:
        save_user(db, 12345, "Test User", ["ML Engineer"], mock_skills, resume_path)
        
    yield {
        "user_id": 12345,
        "resume_path": resume_path,
        "profile": {
            "name": "Test User",
            "target_roles": ["ML Engineer"],
            "skills": mock_skills,
            "experience_years": 1,
            "location": "Remote"
        }
    }
    
    # Cleanup after tests
    shutil.rmtree(test_dir)


# --- TESTS ---

@pytest.mark.asyncio
async def test_tailor_mode_e2e(test_env):
    """
    FAST E2E TEST: Injects a JD directly into Tailor mode.
    Tests: DB Job Creation -> LLM Tailoring -> pdflatex Compilation -> PDF Existence
    """
    mock_jd = "Looking for a Python ML Engineer with FastAPI and PyTorch experience."
    unique_url = f"manual_test_{os.urandom(4).hex()}"
    
    # 1. Setup: Save mock job to DB to get a real integer ID
    with get_db() as db:
        db_job = save_job(db, test_env["user_id"], "ML Engineer", "Test AI Corp", unique_url, "manual", mock_jd)
        mock_db_id = db_job.id

    dummy_job = JobListing(
        title="ML Engineer", 
        company="Test AI Corp", 
        url=unique_url, 
        portal="manual", 
        jd_text=mock_jd, 
        portal_job_id=str(mock_db_id)
    )
    
    dummy_scored = ScoredJob(
        job=dummy_job, 
        db_job_id=mock_db_id, 
        score="A", 
        match_percentage=90, 
        strengths=["Python match"], 
        gaps=[], 
        recommendation="Apply"
    )

    initial_state = {
        "user_id": test_env["user_id"],
        "keyword": "ML Engineer",
        "location": "Remote",
        "profile": test_env["profile"],
        "portals": "config/portals.yml",
        "base_tex_path": test_env["resume_path"],
        "mode": "tailor", # Start directly at tailor
        "raw_jobs": [dummy_job],
        "scored_jobs": [dummy_scored],
        "tailored_jobs": [],
        "final_report": "",
        "error": None
    }

    # 2. Action: Invoke the pipeline
    final_state = await pipeline.ainvoke(initial_state)

    # 3. Assertions
    assert final_state["error"] is None, f"Pipeline failed with error: {final_state['error']}"
    assert len(final_state["tailored_jobs"]) == 1, "Tailor node did not produce 1 tailored job"
    
    tailored_job = final_state["tailored_jobs"][0]
    assert tailored_job["score"] == "A"
    assert tailored_job["company"] == "Test AI Corp"
    
    # Verify PDF was actually created on disk
    pdf_path = tailored_job["pdf_path"]
    assert os.path.exists(pdf_path), f"PDF file not found at {pdf_path}"
    assert pdf_path.endswith(".pdf"), "Generated file is not a PDF"
    
    # Verify DB status was updated
    with get_db() as db:
        job = db.get(db_job.__class__, mock_db_id)
        assert job.status == JobStatus.TAILORED, "DB Job status not updated to TAILORED"


@pytest.mark.asyncio
@pytest.mark.slow
async def test_full_pipeline_e2e(test_env):
    """
    SLOW E2E TEST: Runs the full Crawl -> Score -> Tailor pipeline.
    Hits real APIs (Groq, Job Boards). Run locally with: pytest -m slow
    """
    initial_state = {
        "user_id": test_env["user_id"],
        "keyword": "Python Backend Engineer",
        "location": "Remote",
        "profile": test_env["profile"],
        "portals": "config/portals.yml",
        "base_tex_path": test_env["resume_path"],
        "mode": "full",
        "raw_jobs": [],
        "scored_jobs": [],
        "tailored_jobs": [],
        "final_report": "",
        "error": None
    }

    # Action: Invoke the full pipeline
    final_state = await pipeline.ainvoke(initial_state)

    # Assertions
    assert final_state["error"] is None, f"Pipeline failed: {final_state['error']}"
    assert len(final_state["raw_jobs"]) > 0, "Crawler found 0 jobs"
    assert len(final_state["scored_jobs"]) > 0, "Scorer passed 0 A/B jobs"
    
    # Check if at least one PDF was generated
    if final_state["tailored_jobs"]:
        pdf_path = final_state["tailored_jobs"][0]["pdf_path"]
        assert os.path.exists(pdf_path), "Tailored PDF does not exist"