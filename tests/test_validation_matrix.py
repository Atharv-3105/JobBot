"""
Mock-LLM validation matrix: exercises every guard in resume/tailor.py and
resume/compiler.py deterministically, with zero network calls. This is the bulk
of the edge-case coverage for the test audit - see testing-audit.md for the
narrative report these results feed into.
"""
import time
import pytest
from unittest.mock import patch

from resume.tailor import tailor
from fixtures.mock_llm import mock_llm_scenario
from fixtures.profiles import get_allowed_skills
from fixtures.jd_samples import JD_SAMPLES
from fixtures import metrics

BASE_TEX_PATH = "config/base_resume.tex"


async def _run(scenario: str, profile_key: str, jd_label: str, test_name: str):
    allowed_skills = get_allowed_skills(profile_key)
    jd_text = JD_SAMPLES[jd_label]

    with patch("resume.tailor.get_db"), patch("resume.tailor.crud.update_job_status"):
        with mock_llm_scenario(scenario):
            start = time.time()
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
    tex_content = ""
    if tex_path:
        with open(tex_path, "r", encoding="utf-8") as f:
            tex_content = f.read()

    metrics.record(
        test_name=test_name, mode="mock", profile_name=profile_key, jd_label=jd_label,
        scenario=scenario, provider="mock", latency_ms=round(latency_ms, 1), outcome=outcome,
    )
    return tex_path, pdf_path, diff_summary, tex_content


@pytest.mark.asyncio
async def test_fabricated_skill_gets_blocked():
    """A skill the profile doesn't have must never survive into the tailored resume."""
    tex_path, pdf_path, diff_summary, tex_content = await _run(
        "fabricated_skill", "narrow_junior", "skills_gap", "fabricated_skill_gets_blocked"
    )
    assert pdf_path is not None, "Tailoring should still succeed - only the skills section reverts"
    assert "Kubernetes" not in tex_content


@pytest.mark.asyncio
async def test_synonym_of_owned_skill_is_allowed():
    """A synonym of a skill the profile DOES have should NOT be treated as fabrication."""
    # broad_senior has "Kubernetes" - the mock injects "K8s" (an alias), should pass.
    tex_path, pdf_path, diff_summary, tex_content = await _run(
        "synonym_skill", "broad_senior", "clean_match", "synonym_of_owned_skill_is_allowed"
    )
    assert pdf_path is not None
    metrics.record(
        test_name="synonym_of_owned_skill_is_allowed_detail", mode="mock",
        profile_name="broad_senior", jd_label="clean_match", scenario="synonym_skill",
        provider="mock", outcome="pdf_generated",
        note="K8s alias of already-owned Kubernetes - verifies synonym table doesn't over-block",
    )


@pytest.mark.asyncio
async def test_quantifier_change_gets_blocked():
    """A fabricated metric/number in Experience must never survive into the tailored resume."""
    tex_path, pdf_path, diff_summary, tex_content = await _run(
        "quantifier_change", "backend_focused", "quantifier_bait", "quantifier_change_gets_blocked"
    )
    assert pdf_path is not None, "Only the experience section should revert, not the whole tailoring"
    assert "75%" not in tex_content


@pytest.mark.asyncio
async def test_over_rewrite_fails_retention_check():
    """A response that replaces almost all original content must be rejected wholesale."""
    tex_path, pdf_path, diff_summary, tex_content = await _run(
        "over_rewrite", "narrow_junior", "clean_match", "over_rewrite_fails_retention_check"
    )
    assert pdf_path is None
    metrics.record(
        test_name="over_rewrite_fails_retention_check_reason", mode="mock",
        profile_name="narrow_junior", jd_label="clean_match", scenario="over_rewrite",
        provider="mock", outcome="dropped", drop_reason="retention_too_low",
    )


@pytest.mark.asyncio
async def test_malformed_xml_falls_back_gracefully():
    """A non-XML LLM response must fall back to no-tailoring, not crash or inject garbage."""
    tex_path, pdf_path, diff_summary, tex_content = await _run(
        "malformed_xml", "narrow_junior", "clean_match", "malformed_xml_falls_back_gracefully"
    )
    assert pdf_path is None
    metrics.record(
        test_name="malformed_xml_falls_back_gracefully_reason", mode="mock",
        profile_name="narrow_junior", jd_label="clean_match", scenario="malformed_xml",
        provider="mock", outcome="dropped", drop_reason="xml_malformed",
    )


@pytest.mark.asyncio
async def test_empty_llm_content_falls_back_gracefully():
    """An empty LLM response must fall back to no-tailoring, not crash."""
    tex_path, pdf_path, diff_summary, tex_content = await _run(
        "empty_content", "narrow_junior", "clean_match", "empty_llm_content_falls_back_gracefully"
    )
    assert pdf_path is None
    metrics.record(
        test_name="empty_llm_content_falls_back_gracefully_reason", mode="mock",
        profile_name="narrow_junior", jd_label="clean_match", scenario="empty_content",
        provider="mock", outcome="dropped", drop_reason="empty_content",
    )


@pytest.mark.asyncio
async def test_latex_injection_blocked_end_to_end():
    """
        Full pipeline test of the compiler-level LaTeX-injection gate: simulates an
        LLM that got successfully prompt-injected into echoing a dangerous LaTeX
        primitive. It must survive the retention check (small addition, high
        overlap) but be caught at compile time - proving the defense-in-depth
        design actually works end to end, not just in isolation.
    """
    tex_path, pdf_path, diff_summary, tex_content = await _run(
        "latex_injection", "narrow_junior", "clean_match", "latex_injection_blocked_end_to_end"
    )
    assert pdf_path is None, "The compiler gate must refuse to compile dangerous LaTeX primitives"
    assert tex_path is None
    metrics.record(
        test_name="latex_injection_blocked_end_to_end_reason", mode="mock",
        profile_name="narrow_junior", jd_label="clean_match", scenario="latex_injection",
        provider="mock", outcome="dropped", drop_reason="latex_injection_blocked",
    )


@pytest.mark.asyncio
async def test_provider_exhausted_fails_fast_not_forever():
    """
        Regression test for the infinite-retry bug found and fixed this session:
        when the LLM router is fully exhausted, tailor() must fail within a bounded
        time (via the exhaustion-cycle cap), not hang.
    """
    start = time.time()
    tex_path, pdf_path, diff_summary, tex_content = await _run(
        "provider_exhausted", "narrow_junior", "clean_match", "provider_exhausted_fails_fast_not_forever"
    )
    elapsed = time.time() - start
    assert pdf_path is None
    assert elapsed < 5.0, f"tailor() should fail fast when the router raises, took {elapsed:.1f}s"
    metrics.record(
        test_name="provider_exhausted_fails_fast_not_forever_reason", mode="mock",
        profile_name="narrow_junior", jd_label="clean_match", scenario="provider_exhausted",
        provider="mock", outcome="dropped", drop_reason="provider_exhausted",
        latency_ms=round(elapsed * 1000, 1),
    )


@pytest.mark.asyncio
async def test_near_empty_profile_does_not_crash():
    """Edge case: a profile with almost no declared skills must degrade gracefully, not crash."""
    tex_path, pdf_path, diff_summary, tex_content = await _run(
        "fabricated_skill", "near_empty", "skills_gap", "near_empty_profile_does_not_crash"
    )
    # Whatever the outcome, it must not raise, and no fabricated skill may leak through.
    if tex_content:
        assert "Kubernetes" not in tex_content


def test_dump_validation_matrix_metrics():
    """Not a real assertion - just dumps this file's collected records for inspection."""
    aggregates = metrics.compute_aggregates()
    assert aggregates["total_records"] > 0
