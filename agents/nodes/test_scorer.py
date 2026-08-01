import os
import asyncio
import logging
from dotenv import load_dotenv
from portals.base import JobListing
from agents.nodes.scorer import score_listing

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(name)s | %(message)s")

MOCK_PROFILE = {
    "name": "Atharva",
    "target_roles": ["Backend Engineer", "ML Engineer"],
    "experience_years": 1,
    "skills": {
        "core": ["Python", "Go", "FastAPI", "Docker"],
        "ai_ml": ["LangChain", "LangGraph", "RAG", "ChromaDB"],
        "databases": ["MongoDB", "Redis", "PostgreSQL"],
    }
}

MOCK_JOBS = [
    JobListing(title="Senior Java Dev", company="TechCorp", url="http://1", portal="test", portal_job_id="1",
               jd_text="We need a Senior Java Developer. Requirements: 5+ years Java, Spring Boot."),
    JobListing(title="Python ML Engineer", company="AI Startup", url="http://2", portal="test", portal_job_id="2",
               jd_text="About the role: ML Engineer. What you'll do: Build RAG pipelines using LangChain and FastAPI. Requirements: Python, Redis."),
    JobListing(title="React Frontend", company="WebAgency", url="http://3", portal="test", portal_job_id="3",
               jd_text="Looking for React developer. Must know TypeScript, Next.js.")
]

async def test_scorer():
    print("=" * 60)
    print("TEST: Job Scorer Agent (with LLM Router)")
    print("=" * 60)
    
    scored_jobs = await score_listing(MOCK_JOBS, MOCK_PROFILE, user_id=None)
    
    print(f"\nResults ({len(scored_jobs)} jobs passed A/B threshold):\n")
    for sj in scored_jobs:
        print(f"  🏢 {sj.job.title} at {sj.job.company} | Score: {sj.score} ({sj.match_percentage}%)")
        print(f"     Strengths: {', '.join(sj.strengths)}")
        print("-" * 40)

if __name__ == "__main__":
    asyncio.run(test_scorer())