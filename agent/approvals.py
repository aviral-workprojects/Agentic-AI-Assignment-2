"""Pending-approval workflow for a sensitive change (elective change).
request -> authorization -> PENDING -> human decision -> (execute exactly once | rejected, no execution).
State changes and their audit rows share one transaction. The LLM can only create a pending request."""
import json, os, re, time
from . import audit, authz, db, redact

ACTION = "elective_change"
TTL_S = lambda: float(os.getenv("APPROVAL_TTL_S", "86400"))
_CODE = re.compile(r"^[A-Z]{1,3}\d{2,4}$")

def norm_code(x):
    s = str(x or "").strip().upper()
    return s if _CODE.match(s) else None

def _check_change(c, student_id, frm, to):
    """Validate the requested change against current data. Returns (ok, reason)."""
    if frm == to: return False, "the new elective must differ from the current one"
    cur = c.execute("SELECT 1 FROM enrollments e JOIN courses k ON k.code=e.course_code WHERE e.student_id=? AND e.course_code=? AND k.kind='elective'", (student_id, frm)).fetchone()
    if not cur: return False, f"you are not currently enrolled in elective {frm}"
    new = c.execute("SELECT kind, seats FROM courses WHERE code=?", (to,)).fetchone()
    if not new or new["kind"] != "elective": return False, f"{to} is not an available elective"
    if c.execute("SELECT 1 FROM enrollments WHERE student_id=? AND course_code=?", (student_id, to)).fetchone(): return False, f"you are already enrolled in {to}"
    if new["seats"] <= 0: return False, f"{to} has no seats available"
    return True, ""

def request_change(p, from_course, to_course, cid=None):
    """Student phase: create a PENDING request. Never modifies enrollments."""
    d = authz.can_modify_student(p, getattr(p, "student_id", None))   # may only touch own record; target is never taken from input
    if not d.allowed:
        audit.record(actor=getattr(p, "user_id", "unknown"), role=getattr(p, "role", "none"), action="elective_change_request", decision="denied",
                     outcome="denied", correlation_id=cid, detail=d.reason)
        raise authz.Denied(authz.PUBLIC_DENIAL)
    frm, to = norm_code(from_course), norm_code(to_course)
    base = dict(actor=p.user_id, role=p.role, student_id=p.student_id, action="elective_change_request", correlation_id=cid)
    if not frm or not to:
        audit.record(**base, decision="allowed", outcome="invalid_request", detail="malformed course code")
        return {"error": "invalid course code (expected something like E101)"}
    with db.connect() as c:
        ok, why = _check_change(c, p.student_id, frm, to)
        if not ok:
            audit.record(conn=c, **base, decision="allowed", outcome="invalid_request", detail=why)
            return {"error": why}
        dup = c.execute("SELECT id FROM approval_requests WHERE student_id=? AND action=? AND status='pending' AND payload=? AND expires_ts>?",
                        (p.student_id, ACTION, json.dumps({"from": frm, "to": to}), time.time())).fetchone()
        if dup:
            return {"status": "pending_approval", "request_id": dup["id"], "duplicate": True,
                    "message": f"Request #{dup['id']} for this change is already PENDING approval. Your enrollment has NOT changed."}
        now = time.time()
        cur = c.execute("INSERT INTO approval_requests(requester_id,student_id,action,payload,status,created_ts,expires_ts) VALUES(?,?,?,?, 'pending',?,?)",
                        (p.user_id, p.student_id, ACTION, json.dumps({"from": frm, "to": to}), now, now + TTL_S()))
        rid = cur.lastrowid
        audit.record(conn=c, **base, decision="allowed", approval_required=True, approval_status="pending", outcome="pending",
                     request_id=rid, resource=f"{frm}->{to}")
    return {"status": "pending_approval", "request_id": rid,
            "message": f"Request #{rid} to change {frm} to {to} was created and is PENDING human approval. Your enrollment has NOT changed yet."}

def decide(p, request_id, decision, note="", cid=None):
    """Approver phase. Authorization, state check, revalidation, execution and audit happen in ONE immediate transaction."""
    if decision not in ("approve", "reject"): return {"error": "decision must be 'approve' or 'reject'"}
    try: rid = int(request_id)
    except (TypeError, ValueError): return {"error": "invalid request id"}
    denied = None; out = None
    actor = dict(actor=getattr(p, "user_id", "unknown"), role=getattr(p, "role", "none"), correlation_id=cid)
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM approval_requests WHERE id=?", (rid,)).fetchone()
        if row is None:
            audit.record(conn=c, **actor, action="approval_decision", decision="denied" if getattr(p, "role", "") != "approver" else "allowed", outcome="not_found", request_id=None)
            return {"error": "request not found"}
        d = authz.can_decide_approval(p, row["requester_id"])           # checked AGAIN at execution time
        base = dict(**actor, student_id=row["student_id"], request_id=rid, approval_required=True)
        if not d.allowed:
            audit.record(conn=c, **base, action="approval_decision", decision="denied", approval_status=row["status"], outcome="denied", detail=d.reason)
            denied = d.reason
        elif row["status"] != "pending":
            audit.record(conn=c, **base, action="approval_decision", decision="allowed", approval_status=row["status"], approver_id=p.user_id,
                         outcome=f"ignored_already_{row['status']}")
            out = {"status": row["status"], "executed": False, "message": f"Request #{rid} is already {row['status']}; nothing was executed."}
        elif row["expires_ts"] < time.time():
            c.execute("UPDATE approval_requests SET status='expired', result='expired_no_execution' WHERE id=? AND status='pending'", (rid,))
            audit.record(conn=c, **base, action="approval_decision", decision="allowed", approval_status="expired", approver_id=p.user_id, outcome="expired_no_execution")
            out = {"status": "expired", "executed": False, "message": f"Request #{rid} expired; nothing was executed."}
        else:
            now = time.time(); note = redact.clip(note or "", 200)
            if decision == "reject":
                c.execute("UPDATE approval_requests SET status='rejected', decided_by=?, decided_ts=?, decision_note=?, result='rejected_no_execution' WHERE id=? AND status='pending'", (p.user_id, now, note, rid))
                audit.record(conn=c, **base, action="approval_decision", decision="allowed", approval_status="rejected", approver_id=p.user_id,
                             outcome="rejected_no_execution", detail=f"requester={row['requester_id']}")
                out = {"status": "rejected", "executed": False, "message": f"Request #{rid} rejected; the record was not changed."}
            else:
                out = _approve_and_execute(c, row, p, now, note, base)
    if denied: raise authz.Denied(denied)
    return out

def _approve_and_execute(c, row, p, now, note, base):
    rid, payload = row["id"], json.loads(row["payload"])
    req_user = c.execute("SELECT student_id FROM users WHERE user_id=? AND active=1", (row["requester_id"],)).fetchone()
    ok, why = (False, "requester no longer valid")
    if req_user and req_user["student_id"] == row["student_id"]:                       # integrity: target is the requester's OWN record
        ok, why = _check_change(c, row["student_id"], payload["from"], payload["to"])  # revalidate against CURRENT data
    if not ok:
        c.execute("UPDATE approval_requests SET status='failed', decided_by=?, decided_ts=?, decision_note=?, result=? WHERE id=? AND status='pending'", (p.user_id, now, note, f"failed: {why}"[:200], rid))
        audit.record(conn=c, **base, action="elective_change_execute", decision="allowed", approval_status="failed", approver_id=p.user_id, outcome="failed_revalidation", detail=why)
        return {"status": "failed", "executed": False, "message": f"Request #{rid} could not be executed: {why}."}
    n = c.execute("UPDATE approval_requests SET status='approved', decided_by=?, decided_ts=?, decision_note=?, executed_ts=?, result='executed' WHERE id=? AND status='pending'", (p.user_id, now, note, now, rid)).rowcount
    if n != 1: raise RuntimeError("request state changed concurrently")               # rolls back everything
    c.execute("UPDATE enrollments SET course_code=? WHERE student_id=? AND course_code=?", (payload["to"], row["student_id"], payload["from"]))
    c.execute("UPDATE courses SET seats=seats-1 WHERE code=?", (payload["to"],))
    c.execute("UPDATE courses SET seats=seats+1 WHERE code=?", (payload["from"],))
    audit.record(conn=c, **base, action="approval_decision", decision="allowed", approval_status="approved", approver_id=p.user_id, outcome="approved",
                 detail=f"requester={row['requester_id']}")
    audit.record(conn=c, **base, action="elective_change_execute", decision="allowed", approval_status="approved", approver_id=p.user_id, outcome="executed",
                 resource=f"{payload['from']}->{payload['to']}")
    return {"status": "approved", "executed": True, "message": f"Request #{rid} approved and executed once."}

def list_requests(p, status=None):
    """Approver: all requests. Student: only their own. Course names joined for review."""
    isappr = getattr(p, "role", None) == "approver"
    if not isappr and not (getattr(p, "role", None) == "student" and getattr(p, "student_id", None)): raise authz.Denied("not permitted")
    sql, args = "SELECT * FROM approval_requests", []
    cond = []
    if not isappr: cond.append("requester_id=?"); args.append(p.user_id)
    if status: cond.append("status=?"); args.append(status)
    if cond: sql += " WHERE " + " AND ".join(cond)
    out = []
    with db.connect() as c:
        for r in c.execute(sql + " ORDER BY id DESC", args).fetchall():
            pl = json.loads(r["payload"])
            names = {k["code"]: k["name"] for k in c.execute("SELECT code,name FROM courses WHERE code IN (?,?)", (pl["from"], pl["to"]))}
            out.append({"id": r["id"], "requester": r["requester_id"], "student_id": r["student_id"], "action": r["action"], "from": pl["from"], "to": pl["to"],
                        "summary": f"{pl['from']} {names.get(pl['from'], '')} -> {pl['to']} {names.get(pl['to'], '')}", "status": r["status"],
                        "created_ts": r["created_ts"], "expires_ts": r["expires_ts"], "decided_by": r["decided_by"], "result": r["result"]})
    return out
