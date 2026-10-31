import pathlib, re
from rank_bm25 import BM25Okapi

HANDBOOK = pathlib.Path(__file__).parent.parent / "data" / "handbook.md"
THRESH = 0.7   # absolute floor; see eval/calibrate_retrieval.py
REL = 0.5      # drop chunks scoring < REL * best (keeps prompt small)
STOP = set("a an the of to is are in on for and or can i my me do does what how if be by with at it as this that".split())
def _stem(w):
    for suf in ("ies", "ing", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w

_tok = lambda s: [_stem(w) for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP]

def _load():
    chunks = []
    for part in HANDBOOK.read_text().split("\n## ")[1:]:
        title, _, body = part.partition("\n")
        chunks.append({"id": re.sub(r"\W+", "-", title.lower()).strip("-"), "title": title.strip(), "text": body.strip()})
    return chunks

CHUNKS = _load()
_bm25 = BM25Okapi([_tok(c["title"] + " " + c["title"] + " " + c["text"]) for c in CHUNKS])

def search(query, k=2):
    scores = _bm25.get_scores(_tok(query))
    ranked = sorted(zip(scores, CHUNKS), key=lambda x: -x[0])[:k]
    best = ranked[0][0] if ranked else 0
    return [{**c, "score": round(float(s), 2)} for s, c in ranked if s >= THRESH and s >= REL * best]
