"""LLM provider abstraction. Implement `LLMProvider.generate` to plug in another backend."""
import logging
from typing import List, Optional, Protocol

logger = logging.getLogger(__name__)


class LLMProvider(Protocol):
    def generate(self, prompt: str) -> Optional[str]:
        """Return the model's text response, or None if every attempt failed. Blocking."""
        ...


class GeminiProvider:
    """Google Gemini, trying each model in order until one returns text."""

    def __init__(self, api_key: str, models: Optional[List[str]] = None, temperature: float = 0.0):
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        self._genai = genai
        self.models = models or ["gemini-2.5-flash", "gemini-2.0-flash"]
        self.temperature = temperature

    def generate(self, prompt: str) -> Optional[str]:
        last_error = None
        for name in self.models:
            try:
                model = self._genai.GenerativeModel(name, generation_config={"temperature": self.temperature})
                response = model.generate_content(prompt)
                try:
                    if response.text:
                        logger.info(f"Successfully used model: {name}")
                        return response.text
                except ValueError as e:
                    logger.warning(f"Response had no text on {name}: {response.prompt_feedback}")
                    last_error = e
            except Exception as e:
                logger.warning(f"Model {name} failed: {e}. Trying fallback...")
                last_error = e
        logger.error(f"All models failed. Last error: {last_error}")
        return None


def build_provider(config: dict) -> LLMProvider:
    """Create the provider named in config['llm'] (default: gemini)."""
    import os
    llm_cfg = config.get("llm", {})
    provider = llm_cfg.get("provider", "gemini")
    if provider == "gemini":
        key_env = llm_cfg.get("api_key_env", "GEMINI_API_KEY")
        api_key = os.getenv(key_env, "").strip('"')
        if not api_key:
            raise ValueError(f"{key_env} environment variable is not set")
        return GeminiProvider(api_key, llm_cfg.get("models"), llm_cfg.get("temperature", 0.0))
    raise ValueError(f"Unknown llm.provider '{provider}'")
