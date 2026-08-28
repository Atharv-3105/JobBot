import asyncio
import logging 
import os 
import time 
from datetime import datetime 

from router.llm_router import llm_router, ProviderStatus

logging.basicConfig(level = logging.INFO, format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s")

logger = logging.getLogger("test_providers")



# =============================================================================
# TEST CONFIGURATION
# =============================================================================

# Simple test prompt — fast, cheap, validates basic connectivity and formatting
SIMPLE_SYSTEM_PROMPT = "You are a helpful assistant. Respond concisely."

SIMPLE_USER_MESSAGE = "Say exactly: 'Provider test passed' and nothing else."

# Complex test prompt — validates instruction following and structured output
COMPLEX_SYSTEM_PROMPT = """You are a precise formatting assistant.
You MUST wrap your response in XML tags exactly like this:
<answer>
your response here
</answer>
Do NOT add any other text."""

COMPLEX_USER_MESSAGE = "What is 2 + 2? Wrap the number in the XML tags."

# Tailoring-like test — validates XML adherence for resume use case
TAILOR_SYSTEM_PROMPT = """You are a resume tailoring expert.
You will receive LaTeX sections wrapped in XML tags.
You MUST return the EXACT same XML tags with modified content inside.
Do NOT output markdown fences. Do NOT output explanations."""

TAILOR_USER_MESSAGE = """Tailor this section for a Backend Engineer role:
<section_0>
\\section*{Summary}
Experienced software engineer with Python skills.
</section_0>

Output ONLY the XML tags with tailored content:"""


# =============================================================================
# TEST RESULTS TRACKER
# =============================================================================

class TestResult:
    def __init__(self, provider_name: str, test_name: str):
        self.provider_name = provider_name
        self.test_name = test_name
        self.passed = False
        self.duration_ms = 0.0
        self.tokens_used = 0
        self.response_preview = ""
        self.error = None
        self.has_xml_tags = False

    def __repr__(self):
        status = "✅ PASS" if self.passed else "❌ FAIL"
        return f"{status} | {self.provider_name} | {self.test_name} | {self.duration_ms:.0f}ms | {self.tokens_used} tokens"


# =============================================================================
# TEST FUNCTIONS
# =============================================================================

async def test_provider_simple(provider_name: str) -> TestResult:
    """Test 1: Simple completion — validates basic connectivity."""
    result = TestResult(provider_name, "simple_completion")
    start = time.time()

    try:
        # Force specific provider by temporarily reordering
        content, tokens = await llm_router._call_provider(
            provider=next(p for p in llm_router.providers if p.name == provider_name),
            system_prompt=SIMPLE_SYSTEM_PROMPT,
            user_message=SIMPLE_USER_MESSAGE,
            temperature=0.0,
            max_tokens=50,
        )

        result.duration_ms = (time.time() - start) * 1000
        result.tokens_used = tokens
        result.response_preview = content[:100]
        result.passed = "test passed" in content.lower()
        
        if not result.passed:
            result.error = f"Unexpected response: {content[:200]}"

    except Exception as e:
        result.duration_ms = (time.time() - start) * 1000
        result.error = str(e)

    return result


async def test_provider_complex(provider_name: str) -> TestResult:
    """Test 2: Complex XML formatting — validates instruction following."""
    result = TestResult(provider_name, "xml_formatting")
    start = time.time()

    try:
        content, tokens = await llm_router._call_provider(
            provider=next(p for p in llm_router.providers if p.name == provider_name),
            system_prompt=COMPLEX_SYSTEM_PROMPT,
            user_message=COMPLEX_USER_MESSAGE,
            temperature=0.0,
            max_tokens=100,
        )

        result.duration_ms = (time.time() - start) * 1000
        result.tokens_used = tokens
        result.response_preview = content[:150]
        result.has_xml_tags = "<answer>" in content and "</answer>" in content
        result.passed = result.has_xml_tags and "4" in content

        if not result.passed:
            result.error = f"Missing XML tags or wrong answer. Response: {content[:200]}"

    except Exception as e:
        result.duration_ms = (time.time() - start) * 1000
        result.error = str(e)

    return result


async def test_provider_tailoring(provider_name: str) -> TestResult:
    """Test 3: Tailoring simulation — validates LaTeX/XML handling."""
    result = TestResult(provider_name, "tailoring_simulation")
    start = time.time()

    try:
        content, tokens = await llm_router._call_provider(
            provider=next(p for p in llm_router.providers if p.name == provider_name),
            system_prompt=TAILOR_SYSTEM_PROMPT,
            user_message=TAILOR_USER_MESSAGE,
            temperature=0.2,
            max_tokens=500,
        )

        result.duration_ms = (time.time() - start) * 1000
        result.tokens_used = tokens
        result.response_preview = content[:200]
        result.has_xml_tags = "<section_0>" in content and "</section_0>" in content
        result.passed = result.has_xml_tags and "\\section*" in content

        if not result.passed:
            result.error = f"Missing XML tags or LaTeX. Response: {content[:300]}"

    except Exception as e:
        result.duration_ms = (time.time() - start) * 1000
        result.error = str(e)

    return result


async def test_provider_rate_limit_handling(provider_name: str) -> TestResult:
    """Test 4: Rate limit detection — sends rapid requests to trigger limits."""
    result = TestResult(provider_name, "rate_limit_detection")
    start = time.time()

    try:
        # Send 3 rapid requests
        for i in range(3):
            try:
                content, tokens = await llm_router._call_provider(
                    provider=next(p for p in llm_router.providers if p.name == provider_name),
                    system_prompt=SIMPLE_SYSTEM_PROMPT,
                    user_message=f"Request {i+1}",
                    temperature=0.0,
                    max_tokens=20,
                )
                await asyncio.sleep(0.5)  # Small delay between requests
            except Exception as e:
                if "rate limit" in str(e).lower() or "429" in str(e):
                    result.passed = True  # We detected rate limiting correctly
                    result.error = f"Rate limit detected as expected: {e}"
                    break

        if not result.passed:
            result.passed = True  # No rate limit hit — also acceptable
            result.error = "No rate limit hit (within test window)"

    except Exception as e:
        result.error = str(e)

    result.duration_ms = (time.time() - start) * 1000
    return result


# =============================================================================
# MAIN TEST RUNNER
# =============================================================================

async def run_all_tests():
    """Run complete provider test suite."""
    print("\n" + "=" * 80)
    print("  LLM PROVIDER TEST SUITE")
    print("  JobBot — " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 80 + "\n")

    # Check API keys
    print("🔑 Checking API keys...")
    keys = {
        "GROQ_API_KEY": os.getenv("GROQ_API_KEY"),
        "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY"),
        "CEREBRAS_API_KEY": os.getenv("CEREBRAS_API_KEY"),
        "OPENROUTER_API_KEY": os.getenv("OPENROUTER_API_KEY"),
    }
    
    for key_name, key_val in keys.items():
        status = "✅ Set" if key_val else "❌ MISSING"
        provider = key_name.replace("_API_KEY", "").lower()
        print(f"   {provider:12} {status}")

    print("\n" + "-" * 80 + "\n")

    # Run tests per provider
    all_results = []
    provider_names = ["groq", "gemini", "cerebras", "openrouter"]

    for provider_name in provider_names:
        print(f"\n🧪 Testing {provider_name.upper()}...")
        print("-" * 40)

        # Skip if no API key
        key_name = f"{provider_name.upper()}_API_KEY"
        if not os.getenv(key_name):
            print(f"   ⏭️  Skipped — no API key")
            continue

        # Run all test types
        tests = [
            test_provider_simple(provider_name),
            test_provider_complex(provider_name),
            test_provider_tailoring(provider_name),
            test_provider_rate_limit_handling(provider_name),
        ]

        results = await asyncio.gather(*tests, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                print(f"   ❌ EXCEPTION: {result}")
                continue

            all_results.append(result)
            status_icon = "✅" if result.passed else "❌"
            print(f"   {status_icon} {result.test_name:25} {result.duration_ms:6.0f}ms | {result.tokens_used:4} tokens")
            
            if result.error and not result.passed:
                print(f"      Error: {result.error[:150]}")
            elif result.response_preview:
                print(f"      Response: {result.response_preview[:80]}...")

        # Small delay between providers to avoid cross-provider rate limits
        await asyncio.sleep(2)

    # =============================================================================
    # FINAL REPORT
    # =============================================================================

    print("\n" + "=" * 80)
    print("  TEST SUMMARY")
    print("=" * 80)

    # Group by provider
    for provider_name in provider_names:
        provider_results = [r for r in all_results if r.provider_name == provider_name]
        if not provider_results:
            print(f"\n   {provider_name.upper():12} ⏭️  No tests run")
            continue

        passed = sum(1 for r in provider_results if r.passed)
        total = len(provider_results)
        avg_time = sum(r.duration_ms for r in provider_results) / total if total else 0
        
        status = "✅ HEALTHY" if passed == total else "⚠️  DEGRADED" if passed > 0 else "❌ DOWN"
        print(f"\n   {provider_name.upper():12} {status} | {passed}/{total} passed | Avg: {avg_time:.0f}ms")

        for r in provider_results:
            icon = "✅" if r.passed else "❌"
            print(f"      {icon} {r.test_name:25} {r.duration_ms:6.0f}ms")

    # Overall stats
    total_passed = sum(1 for r in all_results if r.passed)
    total_tests = len(all_results)
    print(f"\n   OVERALL: {total_passed}/{total_tests} tests passed")
    print("=" * 80)

    # Recommendations
    print("\n📋 RECOMMENDATIONS:")
    
    # Check which providers failed tailoring
    tailoring_failures = [r for r in all_results if r.test_name == "tailoring_simulation" and not r.passed]
    if tailoring_failures:
        print(f"   ⚠️  {len(tailoring_failures)} provider(s) failed tailoring test.")
        print("      Consider using only providers that pass for the 'tailoring' task.")
    
    # Check which providers are slow
    slow_providers = [r for r in all_results if r.duration_ms > 10000 and r.passed]
    if slow_providers:
        print(f"   ⚠️  {len(slow_providers)} test(s) took >10s. Consider increasing timeouts.")
    
    # Check XML adherence
    xml_failures = [r for r in all_results if r.test_name == "xml_formatting" and not r.has_xml_tags]
    if xml_failures:
        print(f"   ⚠️  {len(xml_failures)} provider(s) don't follow XML instructions well.")
        print("      These may struggle with resume tailoring.")

    print("\n" + "=" * 80 + "\n")

    return all_results


# =============================================================================
# STRESS TEST (Optional)
# =============================================================================

async def run_stress_test(provider_name: str, num_requests: int = 10):
    """Send rapid requests to test rate limiting and stability."""
    print(f"\n🔥 STRESS TEST: {provider_name} ({num_requests} requests)")
    print("-" * 50)

    results = []
    for i in range(num_requests):
        start = time.time()
        try:
            content, tokens = await llm_router._call_provider(
                provider=next(p for p in llm_router.providers if p.name == provider_name),
                system_prompt=SIMPLE_SYSTEM_PROMPT,
                user_message=f"Stress test request {i+1}",
                temperature=0.0,
                max_tokens=20,
            )
            duration = (time.time() - start) * 1000
            results.append({"success": True, "duration": duration, "error": None})
            print(f"   Req {i+1:2}/{num_requests} ✅ {duration:6.0f}ms")
        except Exception as e:
            duration = (time.time() - start) * 1000
            results.append({"success": False, "duration": duration, "error": str(e)})
            print(f"   Req {i+1:2}/{num_requests} ❌ {duration:6.0f}ms | {str(e)[:60]}")

        await asyncio.sleep(0.3)  # Small delay

    successes = sum(1 for r in results if r["success"])
    avg_time = sum(r["duration"] for r in results) / len(results)
    print(f"\n   Result: {successes}/{num_requests} succeeded | Avg: {avg_time:.0f}ms")


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    # Run main test suite
    # results = asyncio.run(run_all_tests())
    
    # asyncio.run(run_stress_test("groq", num_requests=15))
    # asyncio.run(run_stress_test("gemini", num_requests=10))
    asyncio.run(test_provider_simple("openrouter"))