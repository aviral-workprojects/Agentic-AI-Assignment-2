"""Persistent trace. Everything is redacted/clipped here again as a last line of defence; tool RESULTS and prompts are never stored."""
import json, os, pathlib, time
from . import redact

_DEFAULT = str(pathlib.Path(__file__).parent.parent / "traces" / "trace.jsonl")

def path():
    return os.getenv("TRACE_PATH", _DEFAULT)

def clean(tr):
    t = dict(tr)
    t["q"] = redact.clip(t.get("q", ""), 200)
    t["answer"] = redact.clip(t.get("answer", ""), 500)
    t["tools"] = [{**x, "args": {k: redact.clip(v, 80) for k, v in (x.get("args") or {}).items()}} for x in t.get("tools", [])]
    t["ts"] = round(time.time(), 1)
    return t

def log(tr):
    p = pathlib.Path(path()); p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(clean(tr), default=str) + "\n")
