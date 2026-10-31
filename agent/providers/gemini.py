import json, os, time
from collections.abc import Mapping
from .. import config, redact
from .base import LLMError, LLMResponse, ToolCall, safe_args

MAX_OUT = 1024  # includes thinking tokens on thinking models

_safe_args = safe_args

def parse_response(resp):
    """Gemini response -> LLMResponse. Pure function (unit-tested with fakes)."""
    cands = getattr(resp, "candidates", None) or []
    parts = (getattr(getattr(cands[0], "content", None), "parts", None) or []) if cands else []
    text, calls = [], []
    for i, p in enumerate(parts):
        fc = getattr(p, "function_call", None)
        if fc is not None and getattr(fc, "name", None):
            args, ok = _safe_args(getattr(fc, "args", None))
            calls.append(ToolCall(id=getattr(fc, "id", None) or f"call_{i}", name=fc.name, args=args, args_valid=ok, raw=p))
        elif getattr(p, "text", None) and not getattr(p, "thought", False):
            text.append(p.text)
    u = getattr(resp, "usage_metadata", None)
    in_tok = getattr(u, "prompt_token_count", None) if u else None
    c, t = (getattr(u, "candidates_token_count", None), getattr(u, "thoughts_token_count", None)) if u else (None, None)
    out_tok = None if (c is None and t is None) else (c or 0) + (t or 0)
    if not text and not calls:
        raise LLMError("empty", "no text or tool call in response")
    return LLMResponse("".join(text), calls, in_tok, out_tok)

def map_error(e):
    from google.genai import errors
    if isinstance(e, errors.APIError):
        code, status = e.code, (getattr(e, "status", "") or "").upper()
        msg = (getattr(e, "message", "") or "")
        low = msg.lower()
        if code == 429 or status == "RESOURCE_EXHAUSTED": kind = "quota"
        elif code == 404 or status == "NOT_FOUND": kind = "model_unavailable"
        elif code == 401 or status == "UNAUTHENTICATED" or "api key not valid" in low or "api_key_invalid" in low: kind = "auth"
        elif status == "FAILED_PRECONDITION" or "billing" in low or "free tier is not available" in low: kind = "billing"
        elif code == 403 or status == "PERMISSION_DENIED": kind = "permission"
        elif code == 400: kind = "bad_request"
        elif code and code >= 500: kind = "server"
        else: kind = "unknown"
        return LLMError(kind, redact.clip(msg, 200), code)
    if isinstance(e, (OSError, TimeoutError)) or type(e).__module__.startswith(("httpx", "httpcore", "requests")):
        return LLMError("network", type(e).__name__)
    return LLMError("unknown", type(e).__name__)

class GeminiProvider:
    name = "gemini"

    def __init__(self, model=None, api_key=None, min_interval=None):
        key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise LLMError("config", "GEMINI_API_KEY is not set (put it in .env)")
        from google import genai
        self.client = genai.Client(api_key=key)
        self.model = model or config.model_name("gemini")
        self.min_interval = config.min_interval("gemini") if min_interval is None else min_interval
        self._last = 0.0

    # --- conversion -------------------------------------------------
    @staticmethod
    def declarations(tools):
        from google.genai import types
        out = []
        for t in tools:
            schema = t.get("input_schema") or {}
            out.append(types.FunctionDeclaration(
                name=t["name"], description=t["description"],
                parameters_json_schema=schema if schema.get("properties") else None))
        return out

    @staticmethod
    def contents(messages):
        from google.genai import types
        P, out = types.Part, []
        for m in messages:
            if m["role"] == "user":
                out.append(types.Content(role="user", parts=[P(text=m["text"])]))
            elif m["role"] == "assistant":
                parts = [P(text=m["text"])] if m.get("text") else []
                for tc in m.get("tool_calls", []):
                    parts.append(tc.raw if tc.raw is not None else P(function_call=types.FunctionCall(id=tc.id, name=tc.name, args=tc.args)))
                out.append(types.Content(role="model", parts=parts))
            else:
                parts = [P.from_function_response(name=r["name"], response={"output": json.loads(r["content"])}) for r in m["results"]]
                out.append(types.Content(role="user", parts=parts))
        return out

    def generate(self, system, messages, tools):
        from google.genai import types
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0: time.sleep(wait)
        try:
            cfg = types.GenerateContentConfig(
                system_instruction=system, temperature=0.2, max_output_tokens=MAX_OUT,
                tools=[types.Tool(function_declarations=self.declarations(tools))] if tools else None,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
            resp = self.client.models.generate_content(model=self.model, contents=self.contents(messages), config=cfg)
        except LLMError:
            raise
        except Exception as e:
            raise map_error(e) from None   # drop raw exception chain (may contain request details)
        finally:
            self._last = time.time()
        return parse_response(resp)
