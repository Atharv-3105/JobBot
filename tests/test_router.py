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
    # Initially, groq is highest priority (1)
    prov = mock_router._get_available_provider("default")
    assert prov.name == "groq"
    
    # Mark groq as rate limited, next should be gemini (priority 2)
    groq_prov = next(p for p in mock_router.providers if p.name == "groq")
    groq_prov.mark_rate_limited(60)
    prov = mock_router._get_available_provider("default")
    assert prov.name == "gemini"
    
    # Verify task-specific ordering for "tailoring" (gemini preferred over groq)
    # Let's reset groq to available
    groq_prov.status = ProviderStatus.AVAILABLE
    groq_prov.rate_limit_until = 0.0
    
    prov = mock_router._get_available_provider("tailoring")
    # For tailoring, TASK_PROVIDER_ORDERS prioritizes gemini over groq
    assert prov.name == "gemini"

@pytest.mark.asyncio
async def test_complete_success(mock_router):
    """Test LLM complete calls route and succeed."""
    # Mock calls to Groq
    mock_router._call_groq = AsyncMock(return_value="Mocked response from Groq")
    
    response = await mock_router.complete("sys", "user", task_type="scoring")
    assert response == "Mocked response from Groq"
    mock_router._call_groq.assert_called_once_with("sys", "user", 0.1, 500)
    
    # Verify provider usage was recorded
    groq_prov = next(p for p in mock_router.providers if p.name == "groq")
    assert groq_prov.requests_this_minute == 1

@pytest.mark.asyncio
async def test_complete_failover(mock_router):
    """Test that a 429 rate limit triggers failover to the next provider."""
    # Groq throws rate limit exception
    mock_router._call_groq = AsyncMock(side_effect=Exception("429 Rate Limit Exceeded"))
    # Gemini succeeds
    mock_router._call_gemini = AsyncMock(return_value="Mocked response from Gemini")
    
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
    """Test behavior when all providers fail or throw errors."""
    for p in mock_router.providers:
        p.status = ProviderStatus.RATE_LIMITED
        p.rate_limit_until = time.time() + 30.0
        
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        # Since all providers are rate limited, complete should wait and retry
        # Let's mock a short retry limit to exit quickly
        with pytest.raises(RuntimeError, match="all retries exhausted"):
            await mock_router.complete("sys", "user", max_retries=1)
            
        assert mock_sleep.call_count > 0
