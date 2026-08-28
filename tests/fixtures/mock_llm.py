"""
Mock LLM harness: patches llm_router.complete with scripted, deterministic
responses so validation-logic tests run with zero network calls and zero cost.
"""
import re
from contextlib import contextmanager
from unittest.mock import patch


def _extract_section_count(user_message: str) -> int:
    return len(re.findall(r'<section_\d+', user_message))


def _section_type(user_message: str, i: int) -> str:
    m = re.search(rf'<section_{i}[^>]*type="(\w+)"', user_message)
    return m.group(1) if m else "unknown"


def _section_original(user_message: str, i: int) -> str:
    m = re.search(rf'<section_{i}[^>]*>\s*(.*?)\s*</section_{i}>', user_message, re.DOTALL)
    return m.group(1) if m else f"Content {i}"


def _echo_all(user_message: str, transform) -> str:
    """Rebuild all <section_i> tags, applying `transform(section_type, original_text, i)` per section."""
    n = _extract_section_count(user_message)
    parts = []
    for i in range(n):
        section_type = _section_type(user_message, i)
        original = _section_original(user_message, i)
        parts.append(f"<section_{i}>\n{transform(section_type, original, i)}\n</section_{i}>")
    return "\n".join(parts)


def _make_compliant_response(user_message: str) -> str:
    """Light rephrase of every section - preserves retention, adds no new skill tokens."""
    return _echo_all(user_message, lambda t, orig, i: f"{orig} Adapted for this role.")


def _make_fabricated_skill_response(user_message: str, fake_skill: str = "Kubernetes") -> str:
    def transform(section_type, orig, i):
        if section_type == "skills":
            return f"{orig}, {fake_skill}"
        return f"{orig} Adapted for this role."
    return _echo_all(user_message, transform)


def _make_synonym_response(user_message: str, alias: str = "K8s") -> str:
    """Uses a synonym/alias of an already-allowed skill instead of a fabricated one."""
    def transform(section_type, orig, i):
        if section_type == "skills":
            return f"{orig}, {alias}"
        return f"{orig} Adapted for this role."
    return _echo_all(user_message, transform)


def _make_quantifier_response(user_message: str) -> str:
    def transform(section_type, orig, i):
        if section_type == "experience":
            return f"{orig} Increased throughput by 75%."
        return f"{orig} Adapted for this role."
    return _echo_all(user_message, transform)


def _make_over_rewrite_response(user_message: str) -> str:
    """Replaces content almost entirely - should trip the 60% retention-rate guard."""
    return _echo_all(user_message, lambda t, orig, i: "Completely different unrelated rewritten content with no overlap whatsoever.")


def _make_malformed_xml_response(user_message: str) -> str:
    return "This is not XML at all, just prose explaining the changes."


def _make_empty_response(user_message: str) -> str:
    return ""


def _make_latex_injection_response(user_message: str, payload: str = r"\input{/etc/passwd}") -> str:
    def transform(section_type, orig, i):
        if i == 0:
            return f"{orig} {payload}"
        return f"{orig} Adapted for this role."
    return _echo_all(user_message, transform)


SCENARIOS = {
    "compliant": _make_compliant_response,
    "fabricated_skill": _make_fabricated_skill_response,
    "synonym_skill": _make_synonym_response,
    "quantifier_change": _make_quantifier_response,
    "over_rewrite": _make_over_rewrite_response,
    "malformed_xml": _make_malformed_xml_response,
    "empty_content": _make_empty_response,
    "latex_injection": _make_latex_injection_response,
}


@contextmanager
def mock_llm_scenario(scenario: str, provider_name: str = "mock-provider"):
    """
        Patches llm_router.complete so resume/tailor.py's LLM calls are answered
        deterministically per `scenario`, with zero network calls. The diff-summary
        LLM call (which doesn't send <section_i> tags) gets a trivial stub response
        regardless of scenario, since it's not what these tests are targeting.
    """
    from router.llm_router import llm_router

    if scenario == "provider_exhausted":
        async def fake_complete(*args, **kwargs):
            raise RuntimeError("[LLMRouter]: all providers remained unavailable after 3 retry cycles. Giving up.")
    else:
        responder = SCENARIOS[scenario]

        async def fake_complete(system_prompt, user_message, temperature=0.1, max_tokens=500,
                                 max_retries=3, task_type="default", validate_fn=None):
            if "<section_0" not in user_message:
                return "Resume updated for this role."

            content = responder(user_message)
            if validate_fn is not None and not validate_fn(content):
                raise ValueError(f"{provider_name} returned content that failed caller validation")
            return content

    with patch.object(llm_router, "complete", side_effect=fake_complete):
        yield
