import json, os, time
from .. import config, redact
from .base import LLMError, LLMResponse, ToolCall, safe_args

MAX_OUT = 1024  # completion cap (includes reasoning tokens on gpt-oss)

def _retry_after(e):
    try:
        v = e.response.headers.get("retry-after")
        return float(v) if v is not None else None
    except Exception:
        return None

def _body(e):
    b = getattr(e, "body", None)
    err = b.get("error", b) if isinstance(b, dict) else {}
    return (err.get("code") or ""), (err.get("message") or "")

def map_error(e):
    """groq SDK exception -> LLMError (sanitized detail; no raw exception chain, headers or key)."""
    import groq as g
    if isinstance(e, g.APITimeoutError):
        return LLMError("timeout", "request timed out")
    if isinstance(e, g.APIConnectionError):
        return LLMError("network", type(e).__name__)
    if isinstance(e, g.APIStatusError):
        code, msg = _body(e); low = (code + " " + msg).lower(); st = e.status_code
        if st == 429 or "rate_limit" in low:
            kind = "quota"
        elif st == 401:
            kind = "auth"
        elif st == 403:
            kind = "permission"
        elif st == 404 or "model_not_found" in low or "decommissioned" in low or "does not exist" in low:
            kind = "model_unavailable"
        elif "tool_use_failed" in low:
            kind = "tool_call_failed"
        elif st == 400 and ("terms" in low or "not available" in low or "access" in low):
            kind = "permission"
        elif st in (400, 413, 422):
            kind = "bad_request"
        elif st >= 500:
            kind = "server"
        else:
            kind = "unknown"
        return LLMError(kind, f"{code}: {redact.clip(msg, 200)}".strip(": "), st, _retry_after(e))
    return LLMError("unknown", type(e).__name__)

def parse_response(resp):
    """OpenAI-style chat completion -> LLMResponse. Pure function (unit-tested with fakes)."""
    ch = (getattr(resp, "choices", None) or [None])[0]
    msg = getattr(ch, "message", None)
    if msg is None:
        raise LLMError("empty", "no message in response")
    calls = []
    for i, tc in enumerate(getattr(msg, "tool_calls", None) or []):
        fn = getattr(tc, "function", None)
        if fn is None or not getattr(fn, "name", None):
            calls.append(ToolCall(id=getattr(tc, "id", None) or f"call_{i}", name="", args={}, args_valid=False)); continue
        args, ok = safe_args(getattr(fn, "arguments", None))
        calls.append(ToolCall(id=getattr(tc, "id", None) or f"call_{i}", name=fn.name, args=args, args_valid=ok))
    text = getattr(msg, "content", None) or ""
    u = getattr(resp, "usage", None)
    in_tok, out_tok = (getattr(u, "prompt_tokens", None), getattr(u, "completion_tokens", None)) if u else (None, None)
    if not text.strip() and not calls:
        raise LLMError("empty", "no text or tool call in response")
    return LLMResponse(text, calls, in_tok, out_tok)

class GroqProvider:
    name = "groq"

    def __init__(self, model=None, api_key=None, min_interval=None):
        key = api_key or os.getenv("GROQ_API_KEY")
        if not key:
            raise LLMError("config", "GROQ_API_KEY is not set (put it in .env)")
        from groq import Groq
        # SDK retries 408/429/5xx with backoff and honours Retry-After (when short); capped by GROQ_MAX_RETRIES (<=3).
        self.client = Groq(api_key=key, timeout=config.groq_timeout(), max_retries=config.groq_max_retries())
        self.model = model or config.model_name("groq")
        self.min_interval = config.min_interval("groq") if min_interval is None else min_interval
        self._last = 0.0

    @staticmethod
    def tool_specs(tools):
        return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                   "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}} for t in tools]

    @staticmethod
    def chat_messages(system, messages):
        out = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["text"]})
            elif m["role"] == "assistant":
                d = {"role": "assistant", "content": m.get("text") or ""}
                if m.get("tool_calls"):
                    d["tool_calls"] = [{"id": t.id, "type": "function", "function": {"name": t.name, "arguments": json.dumps(t.args)}} for t in m["tool_calls"]]
                out.append(d)
            else:
                out += [{"role": "tool", "tool_call_id": r["id"], "content": r["content"]} for r in m["results"]]
        return out

    def generate(self, system, messages, tools):
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0: time.sleep(wait)
        kw = {"tools": self.tool_specs(tools), "tool_choice": "auto"} if tools else {}
        try:
            resp = self.client.chat.completions.create(model=self.model, messages=self.chat_messages(system, messages),
                                                       temperature=0.2, max_completion_tokens=MAX_OUT, **kw)
        except LLMError:
            raise
        except Exception as e:
            raise map_error(e) from None   # drop raw exception chain
        finally:
            self._last = time.time()
        return parse_response(resp)
