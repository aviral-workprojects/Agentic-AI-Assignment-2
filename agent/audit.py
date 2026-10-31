"""Append-only audit log (SQLite). Written ONLY by application/service code, never by the LLM.
Stores no passwords, OTPs, keys, tool arguments or raw chat text; `detail` is redacted and clipped."""
import time
from . import authz, db, redact

COLS = ("id", "ts", "actor_id", "actor_role", "student_id", "action", "resource", "decision", "approval_required",
        "approval_status", "approver_id", "outcome", "request_id", "correlation_id", "detail")

def _insert(c, row):
    c.execute("INSERT INTO audit_log(ts,actor_id,actor_role,student_id,action,resource,decision,approval_required,approval_status,"
              "approver_id,outcome,request_id,correlation_id,detail) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)

def record(*, conn=None, actor, role, action, decision, outcome, student_id=None, resource=None, approval_required=False,
           approval_status=None, approver_id=None, request_id=None, correlation_id=None, detail=""):
    """Pass `conn` to write inside the caller's transaction (state change and audit commit or roll back together)."""
    row = (time.time(), str(actor)[:32], str(role)[:16], student_id, action, resource, decision, int(bool(approval_required)),
           approval_status, approver_id, outcome, request_id, correlation_id, redact.clip(detail, 200))
    if conn is not None:
        _insert(conn, row)
    else:
        with db.connect() as c: _insert(c, row)

def query(p, *, student_id=None, request_id=None, limit=100):
    """Approvers see everything; a student sees only events about their own record."""
    own = getattr(p, "student_id", None)
    d = authz.can_view_audit(p, student_id if student_id is not None else own)
    if not d.allowed: raise authz.Denied(d.reason)
    where, args = [], []
    if getattr(p, "role", None) != "approver": where.append("student_id=?"); args.append(own)
    elif student_id is not None: where.append("student_id=?"); args.append(student_id)
    if request_id is not None: where.append("request_id=?"); args.append(request_id)
    sql = "SELECT * FROM audit_log" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
    with db.connect() as c:
        return [dict(r) for r in c.execute(sql, (*args, int(limit))).fetchall()]

def export_rows():
    """TRUSTED-CODE ONLY (sample/eval export): all events, oldest first."""
    with db.connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM audit_log ORDER BY id").fetchall()]
