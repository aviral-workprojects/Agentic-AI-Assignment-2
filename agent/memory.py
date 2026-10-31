import re, time
from . import db, redact

ALLOWED = {"programme", "batch", "preferred_contact", "language"}
HINTS = {
    "programme": {"programme", "program", "course", "trimester", "specialisation"},
    "batch": {"batch", "year", "cohort"},
    "preferred_contact": {"contact", "reach", "call", "email", "whatsapp", "phone", "notify", "prefer", "told"},
    "language": {"language", "hindi", "english", "speak"},
}

def save(sid, key, value):
    key, value = str(key).lower().strip(), str(value).strip()
    if redact.contains_secret(value) or redact.contains_secret(key):
        return {"rejected": "sensitive information is never stored"}
    if key not in ALLOWED:
        return {"rejected": f"key not allowed; allowed: {sorted(ALLOWED)}"}
    if not value or len(value) > 80:
        return {"rejected": "value empty or too long"}
    with db.connect() as c:
        c.execute("INSERT INTO memory VALUES(?,?,?,?) ON CONFLICT(student_id,key) DO UPDATE SET value=excluded.value, ts=excluded.ts",
                  (sid, key, value, time.time()))
    return {"saved": {key: value}}

def relevant(sid, question):
    words = set(re.findall(r"[a-z]+", question.lower()))
    with db.connect() as c:
        rows = c.execute("SELECT key, value FROM memory WHERE student_id=?", (sid,)).fetchall()
    return {r["key"]: r["value"] for r in rows if r["key"] == "programme" or HINTS.get(r["key"], set()) & words}
