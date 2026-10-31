import os

DEFAULT_MODELS = {"groq": "openai/gpt-oss-20b", "gemini": "gemini-flash-latest", "anthropic": "claude-haiku-5-5"}
DEFAULT_MIN_INTERVAL = {"groq": 2.0, "gemini": 4.0, "anthropic": 0.0}  # seconds between LLM calls (free-tier RPM friendliness)

def provider_name():
    return os.getenv("LLM_PROVIDER", "groq").strip().lower()

def model_name(provider=None):
    p = provider or provider_name()
    return os.getenv("MODEL") or DEFAULT_MODELS.get(p, "")

def min_interval(provider=None):
    p = provider or provider_name()
    v = os.getenv("LLM_MIN_INTERVAL_S")
    return float(v) if v else DEFAULT_MIN_INTERVAL.get(p, 0.0)

def groq_timeout():
    return float(os.getenv("GROQ_TIMEOUT_S", "30"))

def groq_max_retries():
    return max(0, min(int(os.getenv("GROQ_MAX_RETRIES", "2")), 3))  # hard cap 3: bounded retries only
