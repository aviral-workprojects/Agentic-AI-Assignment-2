from .. import config
from .base import LLMError

def get_provider(name=None, model=None):
    name = (name or config.provider_name()).lower()
    if name == "groq":
        from .groq_p import GroqProvider
        return GroqProvider(model=model)
    if name == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider(model=model)
    if name == "anthropic":
        from .anthropic_p import AnthropicProvider
        return AnthropicProvider(model=model)
    raise LLMError("config", f"unknown LLM_PROVIDER '{name}' (use groq, gemini or anthropic)")
