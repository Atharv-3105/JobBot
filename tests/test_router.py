import pytest
import time
from unittest.mock import patch, AsyncMock
from router.llm_router import LLMRouter, Provider, ProviderStatus

@pytest.fixture
def mock_router():
    """Create an LLMRouter instance with mocked clients to prevent actual network calls."""
    with patch("router.llm_router.AsyncGroq"), patch("router.llm_router.genai.Client"):
        router = LLMRouter()
        # Reset providers to a deterministic, easily mockable set
        router.providers = [
            Provider(name="groq", priority=1, rpm_limit=2, tpm_limit=500),
            Provider(name="gemini", priority=2, rpm_limit=5, tpm_limit=10000),
            Provider(name="cerebras", priority=3, rpm_limit=1, tpm_limit=1000),
            Provider(name="openrouter", priority=4, rpm_limit=10, tpm_limit=20000),
        ]
        return router

def test_provider_availability():
    """Test that providers correctly determine availability based on limits and cooldowns."""
    p = Provider(name="test_prov", priority=1, rpm_limit=2, tpm_limit=1000)
    
    # 1. Initially available
    assert p.is_available() is True
    
    # 2. Consume limit
    p.mark_used(tokens_used=500)
    assert p.is_available() is True
    p.mark_used(tokens_used=500)
    # RPM limit is 2. We've done 2 requests, so it should be unavailable now.
    assert p.requests_this_minute == 2
    
    # 3. Simulate rate-limiting cooldown
    p.mark_rate_limited(retry_after=5)
    assert p.status == ProviderStatus.RATE_LIMITED
    assert p.is_available() is False
    
    # Simulate time passing beyond cooldown
    p.rate_limit_until = time.time() - 1
    assert p.is_available() is True
    assert p.status == ProviderStatus.AVAILABLE
    assert p.requests_this_minute == 0

def test_get_available_provider(mock_router):
    """Test LLM selection priorities based on task types."""
    # Current TASK_PROVIDER_ORDERS["default"] = [openrouter, groq, gemini, cerebras]
    prov = mock_router._get_available_provider("default")
    assert prov.name == "openrouter"

    # Mark openrouter rate limited, next should be groq (priority 2 for "default")
    openrouter_prov = next(p for p in mock_router.providers if p.name == "openrouter")
    openrouter_prov.mark_rate_limited(60)
    prov = mock_router._get_available_provider("default")
    assert prov.name == "groq"

    # Mark groq rate limited too - "default" falls to gemini next (before cerebras)
    groq_prov = next(p for p in mock_router.providers if p.name == "groq")
    groq_prov.mark_rate_limited(60)
    prov = mock_router._get_available_provider("default")
    assert prov.name == "gemini"

    # Verify task-specific ordering: TASK_PROVIDER_ORDERS["tailoring"] prioritizes
    # cerebras over gemini (opposite of "default"), with openrouter+groq still down
    prov = mock_router._get_available_provider("tailoring")
    assert prov.name == "cerebras"

@pytest.mark.asyncio
async def test_complete_success(mock_router):
    """Test LLM complete calls route and succeed."""
    # Rate-limit openrouter so groq (next in "scoring" priority) is selected
    openrouter_prov = next(p for p in mock_router.providers if p.name == "openrouter")
    openrouter_prov.mark_rate_limited(60)

    # Mock calls to Groq - _call_provider dispatch expects a (content, tokens_used) tuple
    mock_router._call_groq = AsyncMock(return_value=("Mocked response from Groq", 42))

    response = await mock_router.complete("sys", "user", task_type="scoring")
    assert response == "Mocked response from Groq"
    mock_router._call_groq.assert_called_once_with("sys", "user", 0.1, 500)

    # Verify provider usage was recorded
    groq_prov = next(p for p in mock_router.providers if p.name == "groq")
    assert groq_prov.requests_this_minute == 1

@pytest.mark.asyncio
async def test_complete_failover(mock_router):
    """Test that a 429 rate limit triggers failover to the next provider."""
    # Rate-limit openrouter so groq is the first one actually attempted
    openrouter_prov = next(p for p in mock_router.providers if p.name == "openrouter")
    openrouter_prov.mark_rate_limited(60)

    # Groq throws rate limit exception
    mock_router._call_groq = AsyncMock(side_effect=Exception("429 Rate Limit Exceeded"))
    # Gemini succeeds - _call_provider dispatch expects a (content, tokens_used) tuple
    mock_router._call_gemini = AsyncMock(return_value=("Mocked response from Gemini", 42))

    response = await mock_router.complete("sys", "user", task_type="scoring")
    assert response == "Mocked response from Gemini"

    groq_prov = next(p for p in mock_router.providers if p.name == "groq")
    gemini_prov = next(p for p in mock_router.providers if p.name == "gemini")

    # Groq should be marked rate limited
    assert groq_prov.status == ProviderStatus.RATE_LIMITED
    # Gemini should have been used
    assert gemini_prov.requests_this_minute == 1

@pytest.mark.asyncio
async def test_all_providers_exhausted(mock_router):
    """Test behavior when all providers are unavailable for the entire test window."""
    for p in mock_router.providers:
        p.status = ProviderStatus.RATE_LIMITED
        #Far in the future so is_available()'s auto-recovery never fires during this test -
        #the exhaustion-cycle cap (not accidental recovery) must be what ends the loop.
        p.rate_limit_until = time.time() + 100000.0

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        # All providers stay unavailable the whole time - complete() must give up
        # after a bounded number of exhaustion cycles instead of looping forever.
        with pytest.raises(RuntimeError, match="all providers remained unavailable"):
            await mock_router.complete("sys", "user")

        assert mock_sleep.call_count == 3  # 3 waited cycles before the 4th trips the cap and raises
