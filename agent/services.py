"""Service layer: the ONLY code that touches private student data. Every function authorizes against the authenticated
Principal immediately before the query, ignores any model-supplied identity, and writes audit rows."""
import re, time
from . import approvals, audit, authz, db

_SID = re.compile(r"^[A-Z]\d{2,6}$")

def _guard(p, requested, action, cid):
    """Resolve the target student. `requested` (e.g. an LLM-supplied student_id) can never widen access: if present it
    must equal the principal's own student id, otherwise the call is denied and audited."""
    own = getattr(p, "student_id", None)
    target = own if requested in (None, "") else str(requested).strip().upper()[:32]
    d = authz.can_read_student(p, target)
    if not d.allowed:
        audit.record(actor=getattr(p, "user_id", "unknown"), role=getattr(p, "role", "none"), action=action, decision="denied", outcome="denied",
                     student_id=target if target and _SID.match(target) else None, correlation_id=cid, detail=d.reason)
        raise authz.Denied(authz.PUBLIC_DENIAL)
    return own

def _ok(p, action, cid, detail=""):
    audit.record(actor=p.user_id, role=p.role, action=action, decision="allowed", outcome="success", student_id=p.student_id, correlation_id=cid, detail=detail)

def get_student_record(p, requested=None, cid=None):
    sid = _guard(p, requested, "read_student_record", cid)
    with db.connect() as c: r = c.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
    _ok(p, "read_student_record", cid)
    return dict(r) if r else {"error": "student not found"}

def get_fee_status(p, requested=None, cid=None):
    sid = _guard(p, requested, "read_fee_status", cid)
    with db.connect() as c: r = c.execute("SELECT * FROM fees WHERE student_id=?", (sid,)).fetchone()
    _ok(p, "read_fee_status", cid)
    if not r: return {"error": "no fee record"}
    d = dict(r); d["balance"] = d["total"] - d["paid"]
    return d

def get_scholarship_status(p, requested=None, cid=None):
    sid = _guard(p, requested, "read_scholarship_status", cid)
    with db.connect() as c: rows = c.execute("SELECT name, status, amount FROM scholarships WHERE student_id=?", (sid,)).fetchall()
    _ok(p, "read_scholarship_status", cid)
    return {"scholarships": [dict(r) for r in rows]} if rows else {"scholarships": [], "note": "no scholarship record"}

def get_course_info(p, requested=None, cid=None):
    sid = _guard(p, requested, "read_course_info", cid)
    with db.connect() as c:
        mine = [dict(r) for r in c.execute("SELECT k.code, k.name, k.kind FROM enrollments e JOIN courses k ON k.code=e.course_code WHERE e.student_id=? ORDER BY k.code", (sid,))]
        avail = [dict(r) for r in c.execute("SELECT code, name, seats FROM courses WHERE kind='elective' ORDER BY code")]
    _ok(p, "read_course_info", cid)
    return {"enrolled": mine, "electives_offered": avail}

def create_support_ticket(p, subject, details, cid=None):
    sid = _guard(p, None, "create_support_ticket", cid)
    with db.connect() as c:
        cur = c.execute("INSERT INTO tickets(student_id,subject,body,ts) VALUES(?,?,?,?)", (sid, subject, details, time.time()))
    _ok(p, "create_support_ticket", cid)
    return {"ticket_id": cur.lastrowid, "status": "open"}

def request_elective_change(p, from_course, to_course, cid=None):
    _guard(p, None, "request_elective_change", cid)
    return approvals.request_change(p, from_course, to_course, cid)

def get_my_requests(p, cid=None):
    _guard(p, None, "list_my_requests", cid)
    rows = approvals.list_requests(p)
    _ok(p, "list_my_requests", cid)
    return {"requests": [{k: r[k] for k in ("id", "summary", "status")} for r in rows]}
