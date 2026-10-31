"""ONE minimal live Gemini request. Run manually:  python scripts/smoke_gemini.py [--list-models] [--with-tool]
Sends only a synthetic prompt. Never prints the API key. No retries."""
import argparse, os, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import agent  # loads .env
from agent import config, redact
from agent.providers.base import LLMError, USER_MESSAGES
from agent.providers.gemini import GeminiProvider

HINT = {
    "auth": "API key missing/invalid. Create/copy a key in Google AI Studio and put it in .env as GEMINI_API_KEY.",
    "permission": "403: project denied access / not allowed. Check AI Studio project status, account age verification, and that the key's project has the Gemini API enabled. Do NOT enable billing just to fix this unless you choose to.",
    "billing": "Free tier not available for this model/region/project. Try another model (--list-models) or a different region/account.",
    "model_unavailable": "Model id not found for this key. Run with --list-models and set MODEL to one that supports generateContent.",
    "quota": "429: free-tier quota/rate limit exhausted (or limit 0 for this model). Wait, or choose another free-tier model. Do not loop retries.",
    "network": "Network problem reaching generativelanguage.googleapis.com.",
}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--list-models", action="store_true"); ap.add_argument("--with-tool", action="store_true")
    a = ap.parse_args()
    try:
        p = GeminiProvider()
    except LLMError as e:
        print(f"CONFIG: {e.detail}"); return 2
    print(f"model={p.model} (set MODEL in .env to change)")
    if a.list_models:   # metadata call, no generation quota
        try:
            names = [m.name.replace("models/", "") for m in p.client.models.list() if "generateContent" in (m.supported_actions or [])]
            print("models supporting generateContent for this key:\n  " + "\n  ".join(sorted(names)))
            print("NOTE: listing does not tell you which are free-tier; check AI Studio rate-limit page.")
        except Exception as e:
            err = __import__("agent.providers.gemini", fromlist=["map_error"]).map_error(e)
            print(f"LIST FAILED [{err.kind}/{err.code}]: {err.detail}"); return 1
        return 0
    tools = [{"name": "ping", "description": "Call this tool with message='hi'.", "input_schema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}}] if a.with_tool else []
    prompt = "Call the ping tool with message hi." if a.with_tool else "Reply with the single word OK."
    try:
        r = p.generate("You are a connectivity test.", [{"role": "user", "text": prompt}], tools)
    except LLMError as e:
        print(f"FAILED [{e.kind}] http={e.code}\n  detail: {redact.redact(e.detail)}\n  meaning: {HINT.get(e.kind, USER_MESSAGES.get(e.kind, ''))}"); return 1
    print(f"SUCCESS text={r.text[:60]!r} tool_calls={[(t.name, t.args, t.args_valid) for t in r.tool_calls]}")
    print(f"usage: input={r.in_tok} output(incl. thinking)={r.out_tok}" + ("" if r.in_tok is not None else "  (usage not reported)"))
    return 0

if __name__ == "__main__":
    sys.exit(main())
