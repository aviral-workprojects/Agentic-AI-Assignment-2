"""ONE minimal live Groq request. Run manually:  python scripts/smoke_groq.py [--list-models] [--with-tool]
Synthetic prompt only; never touches the student DB; never prints the key; SDK retries are capped (GROQ_MAX_RETRIES<=3)."""
import argparse, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import agent  # loads .env
from agent import redact
from agent.providers.base import LLMError, USER_MESSAGES
from agent.providers.groq_p import GroqProvider, map_error

HINT = {
    "auth": "401: key missing/invalid. Create a key at console.groq.com/keys and put it in .env as GROQ_API_KEY.",
    "permission": "403: access denied for this key/org/model (check console Settings/Limits and model terms).",
    "model_unavailable": "Model id not available to your account. Run --list-models and set MODEL to one listed.",
    "quota": "429: rate/daily limit reached. Check console.groq.com/settings/limits; wait. Do NOT upgrade or add payment.",
    "timeout": "Request timed out (GROQ_TIMEOUT_S).",
    "server": "5xx from Groq after bounded retries. Try later; do not loop.",
    "network": "Cannot reach api.groq.com.",
    "tool_call_failed": "Model produced an invalid tool call (Groq tool_use_failed).",
    "bad_request": "Request rejected as malformed.",
}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--list-models", action="store_true"); ap.add_argument("--with-tool", action="store_true")
    a = ap.parse_args()
    try:
        p = GroqProvider(min_interval=0)
    except LLMError as e:
        print(f"CONFIG: {e.detail}"); return 2
    print(f"model={p.model} (set MODEL in .env to change)")
    if a.list_models:
        try:
            ids = sorted(m.id for m in p.client.models.list().data)
        except Exception as e:
            err = map_error(e); print(f"LIST FAILED [{err.kind}/{err.code}]: {redact.redact(err.detail)}\n  meaning: {HINT.get(err.kind, '')}"); return 1
        print("models available to this key:\n  " + "\n  ".join(ids))
        print(f"\nconfigured model {'IS' if p.model in ids else 'is NOT'} in the list. (Listing does not show free-plan limits: see console Settings > Limits.)")
        return 0
    tools = [{"name": "ping", "description": "Connectivity test tool. Call with message='hi'.",
              "input_schema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}}] if a.with_tool else []
    prompt = "Call the ping tool with message hi." if a.with_tool else "Reply with the single word OK."
    try:
        r = p.generate("You are a connectivity test.", [{"role": "user", "text": prompt}], tools)
    except LLMError as e:
        ra = f" retry_after={e.retry_after}s" if e.retry_after else ""
        print(f"FAILED [{e.kind}] http={e.code}{ra}\n  detail: {redact.redact(e.detail)}\n  meaning: {HINT.get(e.kind, USER_MESSAGES.get(e.kind, ''))}"); return 1
    if a.with_tool:
        tc = r.tool_calls[0] if r.tool_calls else None
        good = bool(tc) and tc.name == "ping" and tc.args_valid and isinstance(tc.args.get("message"), str)
        ok_tool = good
        print(("SUCCESS" if good else "FAILED (no valid tool call)") + f" tool_calls={[(t.name, t.args, t.args_valid) for t in r.tool_calls]} text={r.text[:40]!r}")
    else:
        print(f"SUCCESS text={r.text[:60]!r}")
    print(f"usage: prompt={r.in_tok} completion(incl. reasoning)={r.out_tok}" + ("" if r.in_tok is not None else "  (usage not reported)"))
    return 0 if (not a.with_tool or ok_tool) else 1

if __name__ == "__main__":
    sys.exit(main())
