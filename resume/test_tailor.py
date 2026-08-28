import os
import pytest
from dotenv import load_dotenv
from resume.tailor import tailor

load_dotenv()

USER_ID = 12345
JOB_ID = 99
BASE_TEX_PATH = "config/base_resume.tex"
JD_TEXT = """
We are looking for a Senior ML Engineer.
Requirements: Python, Triton Inference Server, ONNX Runtime, Ray Serve, Kubernetes, MLflow.
You will build scalable AI pipelines and optimize LLM inference.
"""
ALLOWED_SKILLS = ["Python", "PyTorch", "Docker", "FastAPI"]


@pytest.mark.asyncio
async def test_tailor_real_llm_no_fabrication():
    """
        Integration test (hits real LLM providers): tailoring a real resume against a
        JD that requires several skills NOT in allowed_skills. Whatever the outcome
        (PDF produced, or dropped by a validation guard), none of the JD's
        unverified skills may appear in the final tex output.
    """
    assert os.path.exists(BASE_TEX_PATH), f"{BASE_TEX_PATH} not found"

    tex_path, pdf_path, diff_summary = await tailor(
        base_tex_path=BASE_TEX_PATH,
        jd_text=JD_TEXT,
        job_title="Senior ML Engineer",
        company="AI Startup",
        user_id=USER_ID,
        job_id=JOB_ID,
        allowed_skills=ALLOWED_SKILLS,
    )

    if tex_path:
        with open(tex_path, "r", encoding="utf-8") as f:
            content = f.read().lower()
        for fabricated in ["triton inference server", "onnx runtime", "ray serve", "mlflow"]:
            assert fabricated not in content, f"Unverified skill '{fabricated}' leaked into tailored resume"
