"""API-backed evaluation. Requires --yes (each case costs live API calls). Uses an isolated DB/trace under eval/.eval_data/."""
import argparse, datetime, json, os, pathlib, re, shutil, sys, time
ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from agent import auth, db, memory, redact                      # noqa: E402  (paths are dynamic; isolation is set in main())
from agent.providers.base import FATAL_KINDS      # noqa: E402

def mem_rows(sid):
    with db.connect() as c:
        return {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM memory WHERE student_id=?", (sid,))}

def tickets():
    with db.connect() as c: return c.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]

_NEG = r"(?:\bnot\b|n't\b|\bno\b|\bnever\b|\bcannot\b|\bunable\b|\bwithout\b|\binsufficient\b|\black(?:s|ing)?\b)"
_ABSTAIN = [
    r"\b(?:don't|do not|doesn't|does not|didn't|did not|can't|cannot|couldn't|could not|unable to|not able to)\b[^.?!]{0,60}\b(?:have|find|locate|provide|confirm|say|specify|state|mention|contain|cover|address|include|see|know|answer|support)\b",
    r"\bno\s+(?:relevant|specific|such|available|further)?\s*(?:information|policy|details|data|rule|section|guidance|mention|provision|content)\b",
    r"\bnot\s+(?:enough|sufficient|covered|addressed|mentioned|specified|included|available|documented|listed|stated|provided)\b",
    r"\b(?:insufficient|lacks?|lacking|silent on|outside (?:the|of) (?:the )?handbook)\b",
]
_CLAIM = re.compile(r"\brefund(?:ed|able|s)?\b[^.?!]{0,40}\b(?:within|in|after|upon|by)\b|\b(?:will be|is|are|can be|usually|typically|normally|generally)\s+(?:fully |partially |usually |typically )*refund(?:ed|able)\b", re.I)
_CITE = re.compile(r"\[[a-z0-9]+(?:-[a-z0-9]+)+\]")

def _norm(a): return a.lower().replace("\u2019", "'").replace("\u2018", "'")

def abstains(ans):
    """True if the answer acknowledges missing information (pattern-based, wording-tolerant)."""
    a = _norm(ans)
    return any(re.search(p, a) for p in _ABSTAIN)

def unsupported_claims(ans, chunks_retrieved):
    """Policy assertions the evidence cannot support: citations when nothing was retrieved, or affirmative
    refund statements in a sentence that does not itself negate them."""
    found = []
    if not chunks_retrieved and _CITE.search(ans):
        found.append("cites a handbook chunk although none was retrieved")
    for sent in re.split(r"(?<=[.!?])\s+|\n+|[,;:]\s+|\s+(?:but|however|although|though|while)\s+", _norm(ans)):
        if _CLAIM.search(sent) and not re.search(_NEG, sent):
            found.append(f"asserts policy without evidence: {sent.strip()[:60]!r}")
    return found

def check(case, ans, trs, t_before):
    tools = {t["name"] for tr in trs for t in tr["tools"]}
    chunks = {c for tr in trs for c in tr.get("chunks", [])}
    a = ans.lower().replace(",", ""); why = []
    why += [f"missing tool {t}" for t in case.get("must_tools", []) if t not in tools]
    why += [f"forbidden tool {t}" for t in case.get("must_not_tools", []) if t in tools]
    why += [f"missing {g}" for g in case.get("contains", []) if not any(s in a for s in g)]
    why += [f"forbidden text {s}" for s in case.get("not_contains", []) if s in a]
    if case.get("abstain"):
        if not abstains(ans): why.append("did not acknowledge missing information")
        why += unsupported_claims(ans, chunks)
    mem = mem_rows(case["student"]); blob = " ".join(mem.values()).lower()
    why += [f"memory missing {k}" for k, v in case.get("mem_has", {}).items() if v not in mem.get(k, "").lower()]
    why += [f"memory leaked {s}" for s in case.get("mem_forbid", []) if s in blob]
    why += [f"memory not used {k}" for k in case.get("mem_used", []) if not any(k in tr["memories"] for tr in trs)]
    if "tickets_delta" in case and tickets() - t_before != case["tickets_delta"]: why.append("ticket count")
    return not why, "; ".join(why)

def tool_choice(case, trs):
    """'✓' if every required tool was used and no forbidden one; '✗' otherwise; 'n/a' if the case sets no tool expectation."""
    if "must_tools" not in case and "must_not_tools" not in case: return "n/a"
    used = {t["name"] for tr in trs for t in tr["tools"]}
    return "✓" if set(case.get("must_tools", [])) <= used and not (used & set(case.get("must_not_tools", []))) else "✗"

def fmt(x): return "n/a" if x is None else str(x)
def _cell(x): return re.sub(r"\s+", " ", str(x)).replace("|", "/")

HEAD = ["case", "result", "tools used", "tool choice", "evidence", "latency s", "in tok", "out tok", "note"]
LIMITS = [
    "Pass/fail is rule-based (tool use, answer content, memory and ticket side-effects, abstention); it is not human-graded. Wording checks can mis-grade correct answers, so read FAIL notes.",
    "Evidence = handbook chunk ids returned by `search_policy` plus `mem:<key>` for memories injected into context. `-` means none: for an unsupported question this is the correct outcome (nothing relevant was retrieved).",
    "Tool choice: ✓ = all required tools used and no forbidden tool used for that case; n/a = the case sets no tool expectation (or the row comes from an earlier run without that column).",
    "Latency is wall-clock per case and INCLUDES the configured spacing between LLM calls (`LLM_MIN_INTERVAL_S`) and any bounded SDK retry waits; it is not pure model latency.",
    "Token counts are provider-reported (Groq `prompt_tokens` / `completion_tokens`; completion includes reasoning tokens). `n/a` = not reported; nothing is estimated.",
    "13 cases on synthetic data is a small sample. The Assignment 1 student selector is a demo, not authentication.",
]

def _num(x, cast):
    try: return cast(x)
    except (TypeError, ValueError): return None

def parse_md(path):
    """Read rows back from a previous results table (old 8-column or new 9-column format)."""
    lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
    head = next((l for l in lines if l.startswith("Provider/model:")), "")
    rows, cols = [], None
    for l in lines:
        if not l.startswith("|") or set(l.replace("|", "").strip()) <= {"-", " "}: continue
        cells = [c.strip() for c in l.strip().strip("|").split(" | ")]
        if cols is None: cols = [c.lower() for c in cells]; continue
        d = dict(zip(cols, cells[:len(cols)]))
        if len(cells) > len(cols): d[cols[-1]] = " | ".join(cells[len(cols) - 1:])
        rows.append({"id": d["case"], "st": d["result"], "tools": d.get("tools used", d.get("tools", "-")), "tc": d.get("tool choice", "n/a"),
                     "ev": d.get("evidence", "-"), "lat": _num(d.get("latency s"), float), "in": _num(d.get("in tok"), int),
                     "out": _num(d.get("out tok"), int), "why": d.get("note", ""), "src": "earlier run"})
    return head, rows

def report(rows, provider, out, notes=()):
    ran = [r for r in rows if r["st"] in ("PASS", "FAIL")]; lat = sorted(r["lat"] for r in ran if r["lat"] is not None)
    tok = [r for r in ran if r["in"] is not None]; tcs = [r for r in ran if r.get("tc") in ("✓", "✗")]
    md = [f"Provider/model: `{provider.name}/{provider.model}`  (call spacing {getattr(provider, 'min_interval', 0)}s included in latency)", "",
          "| " + " | ".join(HEAD) + " |", "|" + "---|" * len(HEAD)]
    md += [f"| {r['id']} | {r['st']} | {r['tools']} | {r.get('tc', 'n/a')} | {r['ev']} | {fmt(r['lat'])} | {fmt(r['in'])} | {fmt(r['out'])} | {_cell(r['why'])} |" for r in rows]
    n = len(ran); p = sum(r["st"] == "PASS" for r in ran)
    md += ["", f"**Accuracy:** {p}/{n} executed ({100*p/n if n else 0:.0f}%); {len(rows)-n} errored/not run  "]
    if tcs: md.append(f"**Tool selection appropriate:** {sum(r['tc'] == '✓' for r in tcs)}/{len(tcs)} cases with a tool expectation  ")
    if lat: md.append(f"**Latency:** mean {sum(lat)/len(lat):.1f}s, p95 {lat[min(len(lat)-1, int(0.95*len(lat)))]:.1f}s  ")
    md.append(f"**Tokens:** in {sum(r['in'] for r in tok)}, out {sum(r['out'] for r in tok)} over {len(tok)}/{n} cases with provider-reported usage" if tok else "**Tokens:** unavailable (provider did not report usage)")
    if notes: md += ["", "**Provenance**"] + [f"- {x}" for x in notes]
    md += ["", "**Key observations / known limitations**"] + [f"- {x}" for x in LIMITS]
    pathlib.Path(out).write_text("\n".join(md) + "\n", encoding="utf-8")
    return "\n".join(md)

def archive(out):
    """Never overwrite earlier evidence: keep a timestamped copy of an existing results file."""
    o = pathlib.Path(out)
    if o.exists() and o.stat().st_size:
        dst = o.with_name(f"{o.stem}.prev-{datetime.datetime.fromtimestamp(o.stat().st_mtime):%Y%m%d-%H%M%S}{o.suffix}")
        if not dst.exists(): shutil.copy2(o, dst)
        return dst

def run_case(case, provider, loop):
    db.reset()                                   # contents only; file is never deleted
    os.environ["FAIL_TOOL"] = case.get("fail_tool", "")
    for k, v in case.get("setup_memory", {}).items(): memory.save(case["student"], k, v)
    hist, trs, t0n, ans = [], [], tickets(), ""
    principal = auth.principal_for(case["student"])   # trusted harness: stands in for a completed sign-in
    for q in case["turns"]:
        ans, tr = loop.chat(provider, principal, hist, q); trs.append(tr)
        if tr["error"]: break
    err = next((tr["error"] for tr in trs if tr["error"]), None)
    ua = all(tr["usage_available"] for tr in trs)
    row = {"id": case["id"], "why": "", "fatal": None, "src": "this run", "tc": tool_choice(case, trs),
           "tools": ",".join(t["name"] for tr in trs for t in tr["tools"]) or "-",
           "ev": ",".join(sorted({c for tr in trs for c in tr["chunks"]} | {f"mem:{m}" for tr in trs for m in tr["memories"]})) or "-",
           "lat": round(sum(tr["latency_s"] for tr in trs), 1),
           "in": sum(tr["in_tok"] or 0 for tr in trs) if ua else None, "out": sum(tr["out_tok"] or 0 for tr in trs) if ua else None}
    if err:
        row["st"], row["why"] = "ERROR", f"provider error: {err['kind']}"
        if err["kind"] in FATAL_KINDS: row["fatal"] = f"stopped after fatal provider error ({err['kind']})"
    else:
        ok, row["why"] = check(case, ans, trs, t0n); row["st"] = "PASS" if ok else "FAIL"
        if not ok: row["why"] += f" || answer: {redact.clip(ans, 160)}"   # redacted excerpt to make failures diagnosable
    return row

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(ROOT / "eval" / "cases_a1.json"))
    ap.add_argument("--only", default="", help="comma-separated case ids (writes results_a1_partial.md unless --merge/--out)")
    ap.add_argument("--merge", action="store_true", help="with --only: replace just those rows in the existing results table (previous file is archived)")
    ap.add_argument("--out", default="")
    ap.add_argument("--yes", action="store_true", help="confirm live API calls")
    a = ap.parse_args()
    with open(a.cases, encoding="utf-8") as f: cases = json.load(f)
    if a.only:
        want = a.only.split(","); cases = [c for c in cases if c["id"] in want]
        if not cases: print("No matching case ids."); return 2
    if a.merge and not a.only: print("--merge requires --only"); return 2
    full = str(ROOT / "eval" / "results_a1.md")
    out = a.out or (full if (a.merge or not a.only) else str(ROOT / "eval" / "results_a1_partial.md"))
    turns = sum(len(c["turns"]) for c in cases)
    print(f"{len(cases)} cases, {turns} user turns -> LLM calls: min ~{turns}, typical ~{int(turns*2.2)}, hard max {turns*5}. Results -> {out}")
    if not a.yes:
        print("Dry run. Re-run with --yes to execute against the live API."); return 0
    d = ROOT / "eval" / ".eval_data"; d.mkdir(parents=True, exist_ok=True)
    os.environ["DB_PATH"] = str(d / "eval.db"); os.environ["TRACE_PATH"] = str(d / "trace.jsonl")
    if db.is_app_db() or pathlib.Path(db.path()).resolve() != (d / "eval.db").resolve():   # hard safety guard
        print("Refusing to run: evaluation DB is not the isolated eval/.eval_data/eval.db"); return 2
    from agent import loop
    from agent.providers import get_provider
    from agent.providers.base import LLMError
    try:
        provider = get_provider()
    except LLMError as e:
        print(f"CONFIG: {e.detail}"); return 2
    old_head, old_rows = (parse_md(out) if (a.merge and pathlib.Path(out).exists()) else ("", []))
    if a.merge and not old_rows: print(f"--merge: no existing results table at {out}"); return 2
    prev = archive(out)
    if prev: print(f"Previous results preserved at {prev.name}")
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows, stop = [], None
    def final_rows():
        if not a.merge: return rows
        new = {r["id"]: r for r in rows if r["st"] != "NOT RUN"}
        return [new.get(r["id"], r) for r in old_rows]
    def notes():
        if not a.merge: return []
        ids = ",".join(r["id"] for r in rows if r["st"] != "NOT RUN")
        return [f"Rows [{ids}] come from a targeted live rerun at {ts}; all other rows were carried over unchanged from an earlier live run ({old_head or 'header unavailable'}).",
                "This table therefore combines two separate executions and is NOT a single full-suite run."]
    try:
        for case in cases:
            if stop:
                rows.append({"id": case["id"], "st": "NOT RUN", "why": stop, "tools": "-", "tc": "n/a", "ev": "-", "lat": None, "in": None, "out": None})
            else:
                try:
                    row = run_case(case, provider, loop)
                    stop = row.pop("fatal", None) or stop
                except Exception as e:     # one broken case must not kill the run or lose earlier results
                    row = {"id": case["id"], "st": "ERROR", "why": f"harness error: {type(e).__name__}",
                           "tools": "-", "tc": "n/a", "ev": "-", "lat": None, "in": None, "out": None}
                finally:
                    os.environ["FAIL_TOOL"] = ""
                rows.append(row)
            print({k: v for k, v in rows[-1].items() if k not in ("fatal", "src")})
            report(final_rows(), provider, out, notes())   # incremental: partial results survive a crash/Ctrl+C
    except KeyboardInterrupt:
        print("Interrupted; partial results saved.")
    finally:
        os.environ["FAIL_TOOL"] = ""
        if rows: print(report(final_rows(), provider, out, notes()))
    return 0

if __name__ == "__main__":
    sys.exit(main())
