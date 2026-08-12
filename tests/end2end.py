import pytest
import asyncio
import os
import json
import subprocess
import shutil
from unittest.mock import patch, AsyncMock
from pathlib import Path

# Check if pdflatex is available
try:
    subprocess.run(["pdflatex", "--version"], capture_output=True, check=True)
    PDFLATEX_AVAILABLE = True
except (FileNotFoundError, subprocess.CalledProcessError):
    PDFLATEX_AVAILABLE = False

# Sample LaTeX resume for testing
SAMPLE_RESUME = r"""\documentclass{article}
\begin{document}

\section*{Summary}
AI Engineer with Python experience building ML pipelines.

\section*{Skills}
\begin{description}
    \item[\textbf{Languages:}]
    Python, Go, SQL
\end{description}

\section*{Experience}
\begin{itemize}
    \item Built ML pipelines using Python and TensorFlow.
\end{itemize}

\end{document}
"""

# Mock LLM responses
MOCK_SCORER_RESPONSE = json.dumps([
    {
        "job_id": "test_1",
        "score": "A",
        "match_percentage": 95,
        "strengths": ["Python matches", "ML experience"],
        "gaps": [],
        "recommendation": "Apply immediately"
    }
])

# FIX 1: Must return XML-wrapped LaTeX, not JSON!
MOCK_TAILOR_RESPONSE = """
<section_0>
\\section*{Summary}
Tailored AI Engineer with deep expertise in PyTorch and Python for ML pipelines.
</section_0>

<section_1>
\\section*{Skills}
\\begin{description}
    \\item[\\textbf{Languages:}]
    Python, Go, SQL, PyTorch
\\end{description}
</section_1>

<section_2>
\\section*{Experience}
\\begin{itemize}
    \\item Built scalable ML pipelines using Python and PyTorch.
\\end{itemize}
</section_2>
"""


@pytest.fixture(scope="session")
def test_env(tmp_path_factory):
    """Setup temporary environment and database for the test session"""
    from db import init_db, save_user, get_db
    
    # Init the project DB
    init_db()
    
    # Create a temporary directory for test user files
    test_dir = tmp_path_factory.mktemp("data")
    user_dir = test_dir / "12345"
    os.makedirs(user_dir, exist_ok=True)
    
    # Create a minimal, valid base_resume.tex that pdflatex can compile
    resume_path = user_dir / "base_resume.tex"
    resume_path.write_text(SAMPLE_RESUME)
    
    # Create a mock user in the DB using the new JSON column schema
    mock_skills = {
        "core": ["Python", "Go", "FastAPI", "Docker"],
        "primary": ["LangChain", "RAG"],
        "secondary": [],
        "basic": []
    }
    with get_db() as db:
        save_user(db, 12345, "TestUser", ["ML Engineer"], mock_skills, str(resume_path))
        
    yield {
        "user_id": 12345,
        "resume_path": str(resume_path),
        "profile": {
            "name": "TestUser",
            "target_roles": ["ML Engineer"],
            "skills": mock_skills,
            "experience_years": 1,
            "location": "Remote"
        }
    }


@pytest.mark.asyncio
async def test_full_pipeline_smoke(test_env):
    """
    Smoke test: Run the full pipeline with mocked LLM and crawlers.
    Validates: crawl -> score -> tailor -> log
    """
    if not PDFLATEX_AVAILABLE:
        pytest.skip("pdflatex not installed")
    
    from agent.orchestrator import pipeline
    from portals.base import JobListing
    
    # Mock crawler results
    mock_jobs = [
        JobListing(
            title="ML Engineer",
            company="TestCorp",
            url="https://example.com/job1",
            portal="test",
            jd_text="We need an ML engineer with Python and PyTorch experience. Requirements: Python, ML, 2+ years experience.",
            portal_job_id="test_1"
        )
    ]
    
    # Mock LLM responses based on prompt content
    async def mock_llm_complete(system_prompt, user_message, **kwargs):
        # Route to correct mock based on the system prompt content
        if "XML" in system_prompt or "tailor" in kwargs.get("task_type", "").lower():
            return MOCK_TAILOR_RESPONSE
        else:
            return MOCK_SCORER_RESPONSE
    
    with patch("agent.orchestrator.search_all", new_callable=AsyncMock, return_value=mock_jobs), \
         patch("router.llm_router.llm_router.complete", new=mock_llm_complete):
        
        initial_state = {
            "user_id": test_env["user_id"],
            "keyword": "Python ML Engineer",
            "location": "Remote",
            "portals": "config/portals.yml",
            "profile": test_env["profile"],
            "base_tex_path": test_env["resume_path"],
            "mode": "full",
            "raw_jobs": [],
            "scored_jobs": [],
            "tailored_jobs": [],
            "final_report": "",
            "error": None
        }
        
        final_state = await pipeline.ainvoke(initial_state)
        
        # Assertions
        assert final_state.get("error") is None, f"Pipeline error: {final_state.get('error')}"
        assert len(final_state["raw_jobs"]) > 0, "No raw jobs found"
        assert len(final_state["scored_jobs"]) > 0, "No jobs scored"
        assert len(final_state["tailored_jobs"]) > 0, "No jobs tailored"
        
        # Verify PDFs were created
        for job in final_state["tailored_jobs"]:
            assert os.path.exists(job["pdf_path"]), f"PDF not created: {job['pdf_path']}"
            assert job["score"] in ["A", "B"], f"Unexpected score: {job['score']}"


@pytest.mark.asyncio
async def test_router_routing():
    """Test that the router correctly routes based on mode."""
    from agent.orchestrator import route_after_router
    
    assert route_after_router({"mode": "full"}) == "crawl_node"
    assert route_after_router({"mode": "score"}) == "score_node"
    assert route_after_router({"mode": "tailor"}) == "tailor_node"
    assert route_after_router({}) == "crawl_node"  # Default mode


@pytest.mark.asyncio
async def test_tailor_mode_pipeline(test_env):
    """Test the pipeline starting from tailor mode (no scoring)."""
    if not PDFLATEX_AVAILABLE:
        pytest.skip("pdflatex not installed")
    
    from agent.orchestrator import pipeline
    from portals.base import JobListing
    from agent.nodes.scorer import ScoredJob
    from db.crud import save_job, get_db
    
    # Save a job to DB first so we get a valid integer ID
    with get_db() as db:
        db_job = save_job(
            db,
            user_id=test_env["user_id"],
            title="Data Engineer",
            company="TestCorp",
            url="https://example.com/job3",
            portal="manual",
            jd_text="Looking for a data engineer with Python and SQL."
        )
        job_id = db_job.id
    
    raw_job = JobListing(
        title="Data Engineer",
        company="TestCorp",
        url="https://example.com/job3",
        portal="manual",
        jd_text="Looking for a data engineer with Python and SQL.",
        portal_job_id=str(job_id)
    )
    
    scored_job = ScoredJob(
        job=raw_job,
        db_job_id=job_id,
        score="A",
        match_percentage=90,
        strengths=["Python matches"],
        gaps=[],
        recommendation="Apply"
    )
    
    async def mock_llm_complete(system_prompt, user_message, **kwargs):
        return MOCK_TAILOR_RESPONSE
    
    with patch("router.llm_router.llm_router.complete", new=mock_llm_complete):
        initial_state = {
            "user_id": test_env["user_id"],
            "keyword": "Data Engineer",
            "location": "Remote",
            "portals": "config/portals.yml",
            "profile": test_env["profile"],
            "base_tex_path": test_env["resume_path"],
            "mode": "tailor",
            "raw_jobs": [raw_job],
            "scored_jobs": [scored_job],
            "tailored_jobs": [],
            "final_report": "",
            "error": None
        }
        
        final_state = await pipeline.ainvoke(initial_state)
        
        assert final_state.get("error") is None
        assert len(final_state["tailored_jobs"]) > 0


@pytest.mark.asyncio
async def test_llm_router_failover():
    """Test that the LLM router fails over to next provider on rate limit."""
    from router.llm_router import LLMRouter
    
    router = LLMRouter()
    
    # Mock the first provider to fail with rate limit
    call_count = {"groq": 0, "gemini": 0}
    
    async def mock_groq(*args, **kwargs):
        call_count["groq"] += 1
        raise Exception("429 rate limit exceeded")
    
    async def mock_gemini(*args, **kwargs):
        call_count["gemini"] += 1
        return "Success from Gemini"
    
    with patch.object(router, "_call_groq", new=mock_groq), \
         patch.object(router, "_call_gemini", new=mock_gemini):
        
        # FIX 2: Pass the task_type parameter we added in the Worker brick
        result = await router.complete("test prompt", "test message", max_retries=3, task_type="scoring")
        
        assert result == "Success from Gemini"
        assert call_count["groq"] == 1
        assert call_count["gemini"] == 1


@pytest.mark.asyncio
async def test_latex_parser():
    """Test that the LaTeX parser correctly extracts sections."""
    from resume.parser import LatexParser
    
    parser = LatexParser()
    
    # FIX 3: Match the actual return signature of parse()
    original_tex, tailorable_blocks = parser.parse(SAMPLE_RESUME)
    
    assert original_tex is not None, "Original TEX not returned"
    assert len(tailorable_blocks) >= 2, "Should find at least Summary and Skills blocks"


def test_database_models():
    """Test that database models can be created and queried with new JSON schema."""
    from db import models
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    
    # Use in-memory database
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    
    with Session() as db:
        # Create a user using the new JSON columns
        user = models.User(
            user_id=99999,
            username="DBTest",
            target_roles=["Test Role"], # JSON column
            skills={"core": ["Test"]},   # JSON column
            resume_path="/tmp/test.tex"
        )
        db.add(user)
        db.commit()
        
        # Query the user
        queried_user = db.query(models.User).filter(models.User.user_id == 99999).first()
        assert queried_user is not None
        assert queried_user.username == "DBTest"
        assert isinstance(queried_user.skills, dict), "Skills should be a dict"
        assert "core" in queried_user.skills, "Skills dict missing 'core' key"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])