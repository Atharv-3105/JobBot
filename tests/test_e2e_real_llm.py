"""
Budgeted real-LLM end-to-end matrix: hits actual providers (Groq/Gemini/Cerebras/
OpenRouter) through the real resume/tailor.py pipeline. Kept intentionally small
(3 profiles x 4 JDs = 12 calls) to respect the shared free-tier rate-limit pool.
Run standalone: pytest tests/test_e2e_real_llm.py -v -s
"""
import time
import pytest
from unittest.mock import patch

from resume.tailor import tailor
from fixtures.profiles import get_allowed_skills
from fixtures.jd_samples import JD_SAMPLES
from fixtures import metrics

BASE_TEX_PATH = "config/base_resume.tex"

MATRIX_PROFILES = ["narrow_junior", "broad_senior", "ml_focused"]
MATRIX_JDS = ["clean_match", "skills_gap", "quantifier_bait", "prompt_injection"]

# Skills that must NEVER appear for a given (profile, jd) pair - used to verify
# the whitelist guard holds against real LLM output, not just mocked output.
FABRICATION_PROBES = {
    "skills_gap": ["Terraform", "Rust", "GraphQL"],
    "prompt_injection": ["AWS Certified Solutions Architect", "Certified Kubernetes Administrator"],
}


@pytest.mark.asyncio
async def test_real_llm_matrix():
    for profile_key in MATRIX_PROFILES:
        allowed_skills = get_allowed_skills(profile_key)
        for jd_label in MATRIX_JDS:
            jd_text = JD_SAMPLES[jd_label]

            with patch("resume.tailor.get_db"), patch("resume.tailor.crud.update_job_status"):
                start = time.time()
                try:
                    tex_path, pdf_path, diff_summary = await tailor(
                        base_tex_path=BASE_TEX_PATH,
                        jd_text=jd_text,
                        job_title="Test Role",
                        company="Test Co",
                        user_id=1,
                        job_id=1,
                        allowed_skills=allowed_skills,
                    )
                    latency_ms = (time.time() - start) * 1000
                    outcome = "pdf_generated" if pdf_path else "dropped"
                    error = None
                except Exception as e:
                    latency_ms = (time.time() - start) * 1000
                    outcome = "error"
                    error = str(e)
                    tex_path = None

            fabrication_leak = None
            tex_content = ""
            if tex_path:
                with open(tex_path, "r", encoding="utf-8") as f:
                    tex_content = f.read()
                probes = FABRICATION_PROBES.get(jd_label, [])
                leaked = [p for p in probes if p.lower() in tex_content.lower()]
                if leaked:
                    fabrication_leak = leaked

            print(f"[{profile_key} x {jd_label}] outcome={outcome} latency={latency_ms:.0f}ms "
                  f"fabrication_leak={fabrication_leak} error={error}")

            metrics.record(
                test_name="real_llm_matrix", mode="real", profile_name=profile_key,
                jd_label=jd_label, provider="real (router-selected)",
                latency_ms=round(latency_ms, 1), outcome=outcome,
                fabrication_leak=fabrication_leak, error=error,
            )

            # The one assertion that actually matters for the audit's ethics claim:
            # regardless of outcome, no unverified skill may ever leak through.
            assert not fabrication_leak, f"FABRICATION LEAK: {fabrication_leak} in {profile_key} x {jd_label}"
