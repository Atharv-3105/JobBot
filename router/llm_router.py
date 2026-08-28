import asyncio 
import logging 
import os 
import time 
from enum import Enum 
from dataclasses import dataclass, field 
from typing import Optional , List, Callable
from groq import AsyncGroq
from google import genai 
from google.genai import types
import httpx 
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

#Priority of LLMs based on the task
#openrouter/openrouter_glm/openrouter_minimax are 3 different free models all
#routed through OpenRouter, so a single free model being deprecated/overloaded
#(as nvidia/nemotron was mid-session) doesn't take out the whole "openrouter" slot -
#the router fails over between models before ever falling through to groq/gemini/cerebras.
TASK_PROVIDER_ORDERS = {
    "scoring": ["openrouter", "openrouter_glm", "openrouter_minimax", "groq", "gemini", "cerebras"],
    "tailoring": ["openrouter", "openrouter_glm", "openrouter_minimax", "groq", "cerebras", "gemini"],
    "default": ["openrouter", "openrouter_glm", "openrouter_minimax", "groq", "gemini", "cerebras"]
}

#Provider Timeouts 
PROVIDER_TIMEOUTS = {
    "groq": 30.0,
    "gemini": 30.0,
    "cerebras": 45.0,
    "openrouter": 60.0,
}

#Auto-Recovery seconds: If a provider is marked as ERROR, it will retry after this many secs
ERROR_RECOVERY_SECONDS = 300

class ProviderStatus(Enum):
    AVAILABLE = "available"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"
    
@dataclass 
class Provider:
    name:       str 
    priority:     int         #priority-lower = preferred
    rpm_limit:  int         #requests per minute
    tpm_limit:  int         #tokens per minute
    status:     ProviderStatus = ProviderStatus.AVAILABLE
    requests_this_minute:   int = 0
    tokens_this_minute:     int = 0
    rate_limit_until:       float = 0.0
    error_count:            int = 0
    error_since:            float = 0.0    #To track time since marked as ERROR
    last_reset:             float = field(default_factory=time.time)
    
    def is_available(self)-> bool:
        """ 
            Function which checks whether the model is available or not
        """
        
        now = time.time()
        
        #Check if rate-limit cooldown has expired
        if self.status == ProviderStatus.RATE_LIMITED:
            if now >= self.rate_limit_until:
                self._reset_counters()
                self.status = ProviderStatus.AVAILABLE
                logger.info(f"[LLMRouter]: {self.name} recovered from rate-limit")
            else:
                return False 
            
        #Check if ERROR provider can auto-recover
        if self.status == ProviderStatus.ERROR:
            if now - self.error_since >= ERROR_RECOVERY_SECONDS:
                self._reset_counters()
                
                #TODO: Check on how we can implement Error checking auto-recovery
                self.status = ProviderStatus.AVAILABLE
                
                self.error_count = 0
                logger.info(f"[LLMRouter]: {self.name} auto-recovered from ERROR")
            else:
                return False 
            
        #Reset per-minute counters every 60 seconds
        if now - self.last_reset >= 60:
            self._reset_counters()
        
        return self.status == ProviderStatus.AVAILABLE and self.requests_this_minute < self.rpm_limit
        
    def _reset_counters(self):
        """ 
            Function resets the minute counters for a model
        """
        self.requests_this_minute = 0
        self.tokens_this_minute = 0
        self.last_reset = time.time()
        
    def mark_used(self, tokens_used: int):
        """ 
            Function which updates the model usage metrics
        """
        self.requests_this_minute += 1
        self.tokens_this_minute += tokens_used
        
    def mark_rate_limited(self, retry_after: int = 60):
        """ 
            Function which updates the model status to Rate-Limited
        """
        self.status = ProviderStatus.RATE_LIMITED
        self.rate_limit_until = time.time() + retry_after
        logger.warning(f"LLMRouter: {self.name} rate-limited --> cooling down {retry_after}s")
    
    def mark_error(self):
        """ 
            Funtion which updates the model status to ERROR in-case of Error
        """
        self.error_count += 1
        
        #Only mark ERROR after 3 consecutive failures
        if self.error_count >= 3:
            self.status = ProviderStatus.ERROR
            self.error_since = time.time()
            logger.error(f"[LLMRouter]: {self.name} marked as ERRROR after {self.error_count} failures")
        else:
            logger.warning(f"[LLMRouter]: {self.name} error #{self.error_count}, will retry")
        
class LLMRouter:
    """ 
        Routes LLM calls across multiple free-tier providers
        Handles Rate-Limiting, failover and Load-Balancing
        
        Priority Order: Groq --> Gemini --> Cerebras --> OpenRouter
    """
    
    def __init__(self):
        self.providers = [
            Provider(name = "openrouter", priority = 1, rpm_limit = 20, tpm_limit = 200000),
            Provider(name = "openrouter_glm", priority = 1, rpm_limit = 20, tpm_limit = 200000),
            Provider(name = "openrouter_minimax", priority = 1, rpm_limit = 20, tpm_limit = 200000),
            Provider(name = "groq", priority = 2, rpm_limit = 30, tpm_limit = 6000),
            Provider(name = "gemini", priority = 3, rpm_limit = 15, tpm_limit = 1000000),
            Provider(name = "cerebras", priority = 4, rpm_limit = 30, tpm_limit = 60000),
        ]
        
        #Get a mutex lock
        self._lock = asyncio.Lock()
        self._init_clients()
        
    def _init_clients(self):
        #==========Groq============
        self._groq = AsyncGroq(api_key = os.getenv("GROQ_API_KEY"))
        
        #==========Gemini===========
        self._gemini = genai.Client(api_key = os.getenv("GEMINI_API_KEY"))
        
        #==========Cerebras==========
        self._cerebras_base = "https://api.cerebras.ai/v1"
        
        #==========OpenRouter=========
        self._openrouter_base = "https://openrouter.ai/api/v1"
        
        
    def _get_available_provider(self, task_type: str, exclude: List[str] = None) -> Optional[Provider]:
        """ 
            Function which returns the highest-priority available provider based on the task-type,
            Optionally excludes providers that already failed this request
        """
        exclude = exclude or []
        available = [p for p in self.providers if p.is_available() and p.name not in exclude]
        
        if not available:
            return None 
        
        order = TASK_PROVIDER_ORDERS.get(task_type, TASK_PROVIDER_ORDERS['default'])
        available.sort(key = lambda p : order.index(p.name) if p.name in order else 99)
        
        return available[0]
    
    async def complete(self, system_prompt: str, user_message: str, temperature: float = 0.1, 
                       max_tokens: int = 500, max_retries: int = 3, task_type: str = "default", validate_fn: Optional[Callable[[str], bool]] = None)-> str:
        """ 
            Function which sends a Completion Request, routing to the best available provider,
            Automatically fails over on rate-limit or error
        
        """
        attempts = 0
        max_provider_attempts = len(self.providers)  #We will try each provider at most once
        failed_providers: List[str] = []
        exhaustion_cycles = 0
        max_exhaustion_cycles = 3  #We cap total-outage retries instead of looping forever

        while attempts < max_provider_attempts:
            provider = None

            async with self._lock:
                provider = self._get_available_provider(task_type, exclude=failed_providers)

            if not provider:
                exhaustion_cycles += 1
                if exhaustion_cycles > max_exhaustion_cycles:
                    raise RuntimeError(
                        f"[LLMRouter]: all providers remained unavailable after {max_exhaustion_cycles} retry cycles. Giving up."
                    )
                logger.warning(f"[LLMRouter]: all providers are exhausted --- waiting 30s (cycle {exhaustion_cycles}/{max_exhaustion_cycles})")

                await asyncio.sleep(30)
                #Reset the failed list after cooldown to allow retry
                failed_providers = []
                attempts = 0
                continue
            
            try:
                logger.info(f"[LLMRouter]: routing to {provider.name}")
                
                result, token_used = await self._call_provider(provider, system_prompt, user_message, temperature, max_tokens)
                
                
                #We are using caller-defined validation, A provider can return a perfectly successfull, non-empty response that still fails
                #the caller's structural requirements
                #We will treat this a ProviderError so our error handling can manage it by calling other providers, instead of failover/returning unsuable content
                
                if validate_fn is not None and not validate_fn(result):
                    raise ValueError(f"{provider.name} returned content that failed caller validation")
                
                async with self._lock:
                    provider.mark_used(tokens_used = token_used)
                return result 
            
            except Exception as e:
                error_str = str(e).lower()
                #Append the failed-provider to our failed-providers list
                failed_providers.append(provider.name)
                
                #Check for rate-limit or rate-limit status code '429' in the error response
                if "rate limit" in error_str or "429" in error_str or "too many requests" in error_str:
                    
                    #Check if 'retry-after' is present in the error_str
                    retry_after = 60
                    
                    #Extract retry-after if present in the response
                    for word in error_str.split():
                        if word.isdigit() and int(word) < 3600:
                            retry_after = int(word)
                            break

                    async with self._lock:
                        provider.mark_rate_limited(retry_after)
                        
                    logger.warning(f"[LLMRouter]: {provider.name} rate-limited, retry_after = {retry_after}s")
                        # if max_retries > 0:
                        #     logger.info(f"LLMRouter: retrying with different provider")
                        #     return await self.complete(system_prompt, user_message, temperature, max_tokens, max_retries - 1)
                 
                   
                #Error received in the response, retry
                else:
                    #Non-rate limit error (TypeError, valueError, network error etc)
                    #Mark PROVIDER as ERROR so it won't be selected again
                    async with self._lock:
                        provider.mark_error()
                    logger.error(f"[LLMRouter]: {provider.name} error: {e}")
                
                attempts += 1 
                # if attempts > max_retries :
                #     raise RuntimeError(f"LLMRouter: all retries exhausted. Last error: {e}")

                #Add small delay before retry to prevent CPU-spped spinning 
                await asyncio.sleep(1.0)
                
        raise RuntimeError(f"[LLMRouter]: All providers failed. Tried: {failed_providers}")
    
      
    async def _call_provider(self, provider: Provider, system_prompt: str, user_message: str, temperature: float, max_tokens: int) -> tuple[str, int]:
        """ 
            Function to dispatch to the correct provider client
        """
        
        if provider.name == "groq":
            return await self._call_groq(system_prompt, user_message, temperature, max_tokens)
        
        elif provider.name == "gemini":
            return await self._call_gemini(system_prompt, user_message, temperature, max_tokens)
        
        elif provider.name == "cerebras":
            return await self._call_openai_compatible(
                base_url = self._cerebras_base, api_key = os.getenv("CEREBRAS_API_KEY"), model = "llama3.3-70b",
                system_prompt = system_prompt, user_message = user_message, temperature = temperature, max_tokens = max_tokens, timeout = PROVIDER_TIMEOUTS['cerebras'],
            )
            
        elif provider.name == "openrouter":
            return await self._call_openai_compatible(
                base_url = self._openrouter_base, api_key = os.getenv("OPEN_ROUTER_API_KEY"), model = "nvidia/nemotron-3-ultra-550b-a55b:free",
                system_prompt = system_prompt, user_message = user_message, temperature = temperature, max_tokens = max_tokens, timeout = PROVIDER_TIMEOUTS['openrouter'],
            )

        elif provider.name == "openrouter_glm":
            return await self._call_openai_compatible(
                base_url = self._openrouter_base, api_key = os.getenv("OPEN_ROUTER_API_KEY"), model = "z-ai/glm-5.2:free",
                system_prompt = system_prompt, user_message = user_message, temperature = temperature, max_tokens = max_tokens, timeout = PROVIDER_TIMEOUTS['openrouter'],
            )

        elif provider.name == "openrouter_minimax":
            return await self._call_openai_compatible(
                base_url = self._openrouter_base, api_key = os.getenv("OPEN_ROUTER_API_KEY"), model = "minimax/minimax-m3:free",
                system_prompt = system_prompt, user_message = user_message, temperature = temperature, max_tokens = max_tokens, timeout = PROVIDER_TIMEOUTS['openrouter'],
            )


        
        raise ValueError(f"Unknown Provider: {provider.name}")
    
    
    
    async def _call_groq(self, system_prompt, user_message, temperature, max_tokens) -> tuple[str, int]:
        """ 
            Function to generate response by calling Groq provider
        """
        response = await self._groq.chat.completions.create(
            model = "openai/gpt-oss-120b",
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature = temperature,
            max_tokens = max_tokens
        )
        
        content = response.choices[0].message.content.strip() if response.choices[0].message.content else ""
        
        #Validation: Empty Content Check 
        if not content:
            raise ValueError("Groq returned empty content")
        tokens_used = response.usage.total_tokens if response.usage else max_tokens // 2
        return content, tokens_used
    
    
    async def _call_gemini(self, system_prompt, user_message, temperature, max_tokens) -> tuple[str, int]:
        """ 
            Function to generate response by calling Gemini Provider
        """
        try:
            response = await self._gemini.aio.models.generate_content(
                model = 'gemini-2.5-flash',
                contents = user_message,
                config = types.GenerateContentConfig(
                    system_instruction = system_prompt,
                    temperature = temperature,
                    max_output_tokens = max_tokens
                )
            )
            
            if not response.candidates:
                finish_reason = getattr(response, "prompt_feedback", None)
                logger.warning(f"Gemini returned blocked response: {finish_reason}")
                raise ValueError(f"Gemini response blocked: {finish_reason}")
            
            content = response.text.strip() if response.text else ""
            if not content:
                raise ValueError("Gemini returned empty content")
            
            #Estimate token usage
            tokens_used = len(content.split())*2
            return content, tokens_used
       
        except Exception as e:
            raise e 
    
    
    async def _call_openai_compatible(self, base_url, api_key, model, system_prompt, user_message, temperature, max_tokens, timeout: float)-> tuple[str, int]:
        """ 
            Function to generate response by calling Cerebras & OpenRouter providers
        """
        if not api_key:
            raise ValueError(f"No API key configured for {base_url}")

        async with httpx.AsyncClient(timeout = timeout) as client:

            response = await client.post(f"{base_url}/chat/completions",
                                         headers = {
                                             "Authorization": f"Bearer {api_key}",
                                             "Content-Type": "application/json",
                                            #  **({"HTTP-Referer": os.getenv("APP_URL", ""), "X-Title": "JobBot"} if "openrouter" in base_url else {}),
                                         },
                                         json = {
                                             "model": model,
                                             "messages": [
                                                 {"role": "system", "content": system_prompt},
                                                 {"role": "user", "content": user_message},
                                             ],
                                             "temperature": temperature,
                                             "max_tokens": max_tokens,
                                         })
            
            response.raise_for_status()

            data = response.json()

            if "choices" not in data:
                err = data.get("error", data)
                raise ValueError(f"{model} returned no choices: {err}")

            content = data["choices"][0]["message"]["content"].strip() if data["choices"][0]["message"].get("content") else ""
            #Validation: Empty Content Check           
            if not content:
                raise ValueError(f"{model} returned empty content")
            
            tokens_used = data.get("usage", {}).get("total_tokens", len(content.split()) * 2)
            return content, tokens_used
        
    
    def get_status(self) -> dict:
        """ 
            Function which Returns current status of all providers - will be used for Admin view only /admin_status  telegram command.
        """
        
        return {
            p.name: {
                "status": p.status.value,
                "rpm_used": p.requests_this_minute,
                "rpm_limit": p.rpm_limit,
                "tpm_used": p.tokens_this_minute,
                "tpm_limit": p.tpm_limit,
                "error_count": p.error_count
            }
            for p in self.providers
        }
                
llm_router = LLMRouter()