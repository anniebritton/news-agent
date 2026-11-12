"""LLM provider abstraction. Implement `LLMProvider.generate` to plug in another backend."""
import logging
import os
from typing import List, Optional, Protocol

from .errors import ConfigError
from .redact import redact

logger = logging.getLogger(__name__)


class LLMProvider(Protocol):
    def generate(self, prompt: str) -> Optional[str]:
        """Return the model's text response, or None if every attempt failed. Blocking."""
        ...


DEFAULT_GEMINI_MODELS = ["gemini-3.8-flash", "gemini-3.6-flash"]


class GeminiProvider:
    """Google Gemini via the google-genai SDK, trying each model in order until one returns text."""

    def __init__(self, api_key: str, models: Optional[List[str]] = None, temperature: float = 0.0):
        from google import genai
        self.api_key = api_key
        self.client = genai.Client(api_key=api_key)
        self.models = models or DEFAULT_GEMINI_MODELS
        self.temperature = temperature

    def generate(self, prompt: str) -> Optional[str]:
        last_error = None
        for name in self.models:
            try:
                response = self.client.models.generate_content(
                    model=name, contents=prompt, config={"temperature": self.temperature})
                if response.text:
                    logger.info(f"Successfully used model: {name}")
                    return response.text
                logger.warning(f"Empty response from {name}")
            except Exception as e:
                last_error = redact(e, [self.api_key])
                logger.warning(f"Model {name} failed: {last_error}. Trying fallback...")
        logger.error(f"All models failed. Last error: {last_error}")
        return None


def build_provider(config: dict) -> LLMProvider:
    """Create the provider named in config['llm'] (default: gemini)."""
    llm_cfg = config.get("llm") or {}
    provider = llm_cfg.get("provider", "gemini")
    if provider == "gemini":
        key_env = llm_cfg.get("api_key_env", "GEMINI_API_KEY")
        api_key = os.getenv(key_env, "").strip('"')
        if not api_key:
            raise ConfigError(
                f"{key_env} is not set. Export it in your own terminal (export {key_env}=...), "
                "or use --check, which needs no key.")
        return GeminiProvider(api_key, llm_cfg.get("models"), llm_cfg.get("temperature", 0.0))
    raise ConfigError(f"Unknown llm.provider '{provider}'")
