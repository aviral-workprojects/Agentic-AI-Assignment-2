import json, os, time
from .. import config, redact
from .base import LLMError, LLMResponse, ToolCall

def map_error(e):
    import anthropic as a
    table = [(a.AuthenticationError, "auth"), (a.PermissionDeniedError, "permission"), (a.NotFoundError, "model_unavailable"),
             (a.RateLimitError, "quota"), (a.APIConnectionError, "network"), (a.BadRequestError, "bad_request"),
             (a.InternalServerError, "server")]
    for cls, kind in table:
        if isinstance(e, cls):
            return LLMError(kind, redact.clip(str(getattr(e, "message", "")), 200), getattr(e, "status_code", None))
    return LLMError("unknown", type(e).__name__)

class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model=None, api_key=None, min_interval=None):
        key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise LLMError("config", "ANTHROPIC_API_KEY is not set")
        import anthropic
        self.client = anthropic.Anthropic(api_key=key)
        self.model = model or config.model_name("anthropic")
        self.min_interval = config.min_interval("anthropic") if min_interval is None else min_interval
        self._last = 0.0

    @staticmethod
    def blocks(messages):
        out = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["text"]})
            elif m["role"] == "assistant":
                b = ([{"type": "text", "text": m["text"]}] if m.get("text") else []) + [
                    {"type": "tool_use", "id": t.id, "name": t.name, "input": t.args} for t in m.get("tool_calls", [])]
                out.append({"role": "assistant", "content": b})
            else:
                out.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": r["id"], "content": r["content"]} for r in m["results"]]})
        return out

    def generate(self, system, messages, tools):
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0: time.sleep(wait)
        try:
            r = self.client.messages.create(model=self.model, max_tokens=800, system=system, tools=tools or [], messages=self.blocks(messages))
        except Exception as e:
            raise map_error(e) from None
        finally:
            self._last = time.time()
        text = "".join(b.text for b in r.content if b.type == "text")
        calls = [ToolCall(id=b.id, name=b.name, args=b.input if isinstance(b.input, dict) else {}, args_valid=isinstance(b.input, dict))
                 for b in r.content if b.type == "tool_use"]
        if not text and not calls:
            raise LLMError("empty")
        return LLMResponse(text, calls, r.usage.input_tokens, r.usage.output_tokens)
