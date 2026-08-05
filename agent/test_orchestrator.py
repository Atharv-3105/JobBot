import asyncio 
import logging 
from dotenv import load_dotenv
from agent.orchestrator import pipeline


load_dotenv()
logging.basicConfig(level = logging.INFO, format = "%(name)s | %(message)s")

async def test_pipeline():
    print("="*60)
    print("TEST: LangGraph Orchestrator (End-To-End)")
    print("="*60)
    
    initial_state = {
        "user_id": 12345,
        "keyword": "Python, ML Engineer",
        "location": "Remote",
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
    

if __name__ == "__main__":
    asyncio.run(test_pipeline())