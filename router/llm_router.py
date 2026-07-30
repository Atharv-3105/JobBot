import asyncio 
import logging 
import os 
import time 
from enum import Enum 
from dataclasses import dataclass, field 
from typing import Optional 
from groq import AsyncGroq
from google.generativeai import genai 
import httpx 

logger = logging.getLogger(__name__)

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
            else:
                return False 
            
        #Reset per-minute counters every 60 seconds
        if now - self.last_reset >= 60:
            self._reset_counters()
        
        return (
            self.status == ProviderStatus.AVAILABLE and self.requests_this_minute <= self.rpm_limit
        )
        
    def _reset_counters(self):
        """ 
            Function resets the minute counters for a model
        """
        self.requests_this_minute = 0
        self.tokens_this_minute = 0
        self.last_reset = time.time()
        
    def mark_used(self, tokens_used: int = 500):
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
        self.status = ProviderStatus.ERROR
        logger.error(f"LLMRouter: {self.name} marked as error state")
        
class LLMRouter:
    """ 
        Routes LLM calls across multiple free-tier providers
        Handles Rate-Limiting, failover and Load-Balancing
        
        Priority Order: Groq --> Gemini --> Cerebras --> OpenRouter
    """
    
    def __init__(self):
        self.providers = [
            Provider(name = "groq", priority = 1, rpm_limit = 30, tpm_limit = 6000),
            Provider(name = "gemini", priority = 2, rpm_limit = 15, tpm_limit = 1000000),
            Provider(name = "cerebras", priority = 3, rpm_limit = 30, tpm_limit = 60000),
            Provider(name = "openrouter", priority = 4, rpm_limit = 20, tpm_limit = 200000),
        ]
        
        #Get a mutex lock
        self._lock = asyncio.Lock()
        self._init_clients()
        
    def _init_clients(self):
        #==========Groq============
        self._groq = AsyncGroq(api_key = os.getenv("GROQ_API_KEY"))
        
        #==========Gemini===========
        genai.configure(api_key = os.getenv("GEMINI_API_KEY"))
        self._gemini = genai.GenerativeModel("gemini-2.5-flash")
        
        #==========Cerebras==========
        self._cerebras_base = "https://api.cerebras.ai/v1"
        
        #==========OpenRouter=========
        self._openrouter_base = "https://openrouter.ai/api/v1"
        
        
    def _get_available_provider(self) -> Optional[Provider]:
        """ 
            Function which returns the highest-priority available provider
        """
        available = [p for p in self.providers if p.is_available()]
        if not available:
            return None 
        
        return min(available, key = lambda p: p.priority)
    
    async def complete(self, system_prompt: str, user_message: str, temperature: float = 0.1, max_tokens: int = 500, max_retries: int = 3)-> str:
        """ 
            Function which sends a Completion Request, routing to the best available provider,
            Automatically fails over on rate-limit or error
        
        """
        
        async with self._lock:
            provider = self._get_available_provider()
            
            
            if not provider:
                #All Providers are exhausted ---- wait and retry
                logger.warning("LLMRouter: all providers exhausted -- waiting 30s")
                await asyncio.sleep(30)
                return await self.complete(system_prompt, user_message, temperature, max_tokens, max_retries)
            
            try:
                logger.info(f"LLMRouter: routing to {provider.name}")
                
                result = await self._call_provider(provider, system_prompt, user_message, temperature, max_tokens)
                
                async with self._lock:
                    provider.mark_used(tokens_used = max_tokens)
                return result 
            
            except Exception as e:
                error_str = str(e).lower()
                
                #Check for rate-limit or rate-limit status code '429' in the error response
                if "rate limit" in error_str or "429" in error_str:
                    
                    #Check if 'retry-after' is present in the error_str
                    retry_after = 60
                    if "retry_after" in error_str:
                        try:
                            retry_after = int(''.join(filter(str.isdigit, error_str[:50])))
                        except ValueError:
                            retry_after = 60

                    async with self._lock:
                        provider.mark_rate_limited(retry_after)

                    if max_retries > 0:
                        logger.info(f"LLMRouter: retrying with different provider")
                        return await self.complete(system_prompt, user_message, temperature, max_tokens, max_retries - 1)
                
                #Error received in the response, retry
                else:
                    async with self._lock:
                        provider.mark_error()
                    logger.error(f"LLMRouter: {provider.name} error: {e}")
                    
                    if max_retries > 0:
                        return await self.complete(system_prompt, user_message, temperature, max_tokens, max_retries - 1)

            raise RuntimeError(f"LLMRouter: all retries exhausted - {e}")
    
      
    async def _call_provider(self, provider: Provider, system_prompt: str, user_message: str, temperature: float, max_tokens: int) -> str:
        """ 
            Function to dispatch to the correct provider client
        """
        
        if provider.name == "groq":
            return await self._call_groq(system_prompt, user_message, temperature, max_tokens)
        
        elif provider.name == "gemini":
            return await self._call_gemini(system_prompt, user_message)
        
        elif provider.name == "cerebras":
            return await self._call_openai_compatible(
                base_url = self._cerebras_base, api_key = os.getenv("CEREBRAS_API_KEY"), model = "llama3.3-70b",
                system_prompt = system_prompt, user_message = user_message, temperature = temperature, max_tokens = max_tokens
            )
            
        elif provider.name == "openrouter":
            return await self._call_openai_compatible(
                base_url = self._openrouter_base, api_key = os.getenv("OPENROUTER_API_KEY"), model = "meta-llama/llama-3.3-70b-instruct:free",
                system_prompt = system_prompt, user_message = user_message, temperature = temperature, max_tokens = max_tokens
            )
            
        
        raise ValueError(f"Unknown Provider: {provider.name}")
    
    
    
    async def _call_groq(self, system_prompt, user_message, temperature, max_tokens) -> str:
        """ 
            Function to generate response by calling Groq provider
        """
        response = await self._groq.chat.completions.create(
            model = "llama-3.3-70b-versatile",
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature = temperature,
            max_tokens = max_tokens
        )
        
        return response.choices[0].message.content.strip()
    
    
    async def _call_gemini(self, system_prompt, user_message) -> str:
        """ 
            Function to generate response by calling Gemini Provider
        """
        prompt = f"{system_prompt}\n\n{user_message}"
        response = await asyncio.to_thread(self._gemini.generate_content, prompt)
        
        return response.text.strip()
    
    
    async def _call_openai_compatible(self, base_url, api_key, model, systemp_prompt, user_message, temperature, max_tokens)-> str:
        """ 
            Function to generate response by calling Cerebras & OpenRouter providers
        """
        async with httpx.AsyncClient(timeout = 30.0) as client:
            
            response = await client.post(f"{base_url}/chat/completions", 
                                         headers = {
                                             "Authorization": f"Bearer {api_key}",
                                             "Content-Type": "application/json",
                                         },
                                         json = {
                                             "model": model,
                                             "messages": [
                                                 {"role": "system", "content": systemp_prompt},
                                                 {"role": "user", "content": user_message},
                                             ],
                                             "temperature": temperature,
                                             "max_tokens": max_tokens,
                                         })
            
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"].strip()
        
    
    def get_status(self) -> dict:
        """ 
            Function which Returns current status of all providers - will be used for Admin view only /admin_status  telegram command.
        """
        
        return {
            p.name: {
                "status": p.status.value,
                "rpm_used": p.requests_this_minute,
                "rpm_limit": p.rpm_limit,
            }
            for p in self.providers
        }
        
        