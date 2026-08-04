import os
import asyncio
import logging 
from dotenv import load_dotenv
from resume.tailor import tailor

load_dotenv()
logging.basicConfig(level = logging.DEBUG, format = "%(name)s | %(message)s")


# Mock Data
USER_ID = 12345
JOB_ID = 99
BASE_TEX_PATH = "config/base_resume.tex" # Ensure this file exists for the test!
JD_TEXT = """
We are looking for a Senior ML Engineer. 
Requirements: Python, Triton Inference Server, ONNX Runtime, Ray Serve, Kubernetes, MLflow.
You will build scalable AI pipelines and optimize LLM inference.
"""

async def test_tailor():
    print("="*60)
    print("TEST: Resume Tailor Agent(6-Phase Pipeline)")
    print("="*60)
    
    if not os.path.exists(BASE_TEX_PATH):
        print(f"Error: {BASE_TEX_PATH} not found. Please create a dummy LaTeX resume")
        return 
    
    tex_path, pdf_path = await tailor(base_tex_path=BASE_TEX_PATH,
                                      jd_text=JD_TEXT,
                                      job_title="Senior ML Engineer",
                                      company = "AI Startup",
                                      user_id=USER_ID,
                                      job_id=JOB_ID)
    
    
    if tex_path and pdf_path:
        print(f"\n SUCCESS")
        print(f"    Tailored TEX RESUME: {tex_path}")
        print(f"    Compiled PDF: {pdf_path}")
        
    else:
        print(f"\n Failed. Check logs for fallback trigger")
        
if __name__ == "__main__":
    asyncio.run(test_tailor())