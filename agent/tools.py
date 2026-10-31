import os
from . import authz, auth, audit, memory, retrieval, services

SID_PROP = {"student_id": {"type": "string", "description": "Leave empty. Only the signed-in student's own record can be accessed; any other id is denied by the application."}}

SCHEMAS = [
    {"name": "get_student_record", "description": "Get the signed-in student's profile (name, programme, batch, email).",
     "input_schema": {"type": "object", "properties": dict(SID_PROP)}},
    {"name": "get_fee_status", "description": "Get the signed-in student's fee status: total, paid, balance, due date.",
     "input_schema": {"type": "object", "properties": dict(SID_PROP)}},
    {"name": "get_scholarship_status", "description": "Get the signed-in student's scholarship records and status.",
     "input_schema": {"type": "object", "properties": dict(SID_PROP)}},
    {"name": "create_support_ticket", "description": "Create a support ticket. Needs a clear subject and details.",
     "input_schema": {"type": "object", "properties": {"subject": {"type": "string"}, "details": {"type": "string"}}, "required": ["subject", "details"]}},
    {"name": "search_policy", "description": "Search the student handbook for policy/rules. Returns the relevant chunks only.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "save_memory", "description": "Persist a stable fact about the student. Keys: programme, batch, preferred_contact, language. Never sensitive or temporary info.",
     "input_schema": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]}},
    {"name": "get_course_info", "description": "Get the signed-in student's current course enrollments and the electives offered with seats available (use to find course codes).",
     "input_schema": {"type": "object", "properties": dict(SID_PROP)}},
    {"name": "request_elective_change", "description": "Request changing the student's own elective (course codes like E101). SENSITIVE: this only creates a PENDING request that a human approver must decide; it does not change the enrollment.",
     "input_schema": {"type": "object", "properties": {"from_course": {"type": "string"}, "to_course": {"type": "string"}}, "required": ["from_course", "to_course"]}},
    {"name": "get_my_requests", "description": "List the signed-in student's own approval requests and their status.",
     "input_schema": {"type": "object", "properties": {}}},
]

def _w(fn, takes_sid=False):
    def f(p, cid, requested=None, **a):
        return fn(p, requested, cid=cid) if takes_sid else fn(p, **a, cid=cid)
    return f

FUNCS = {
    "get_student_record": _w(services.get_student_record, True), "get_fee_status": _w(services.get_fee_status, True),
    "get_scholarship_status": _w(services.get_scholarship_status, True), "get_course_info": _w(services.get_course_info, True),
    "create_support_ticket": _w(services.create_support_ticket), "request_elective_change": _w(services.request_elective_change),
    "get_my_requests": _w(services.get_my_requests),
    "search_policy": lambda p, cid, requested=None, **a: _search_policy(**a),
    "save_memory": lambda p, cid, requested=None, **a: memory.save(p.student_id, **a),
}

def _search_policy(query):
    ch = retrieval.search(query)
    return {"chunks": [{"id": c["id"], "text": c["text"]} for c in ch]} if ch else {"chunks": [], "note": "no relevant policy found: the handbook does not cover this. Tell the student you do not have this information and offer a support ticket; do not state a policy."}

MAX_LEN = 500
_BY_NAME = {t["name"]: t["input_schema"] for t in SCHEMAS}

def validate(name, args):
    """Return None if ok, else a safe error string."""
    if name not in _BY_NAME: return "unknown tool"
    if not isinstance(args, dict): return "arguments must be an object"
    props, req = _BY_NAME[name].get("properties", {}), _BY_NAME[name].get("required", [])
    if set(args) - set(props): return "unexpected arguments"
    if [k for k in req if k not in args]: return "missing required arguments"
    for v in args.values():
        if not isinstance(v, str) or len(v) > MAX_LEN or (not v.strip() and v != ""): return "invalid argument value"
    return None

def run(name, args, principal, cid=None):
    """Execute one model-requested tool for the AUTHENTICATED principal. `student_id` (if the model supplies it) is only a
    claim: the service layer compares it with the principal and denies/audits any mismatch."""
    meta = {"authz": "allowed"}
    if os.getenv("FAIL_TOOL") == name:          # eval hook: simulate outage
        return {"error": "service temporarily unavailable", "_meta": {"authz": "n/a"}}
    bad = validate(name, args)
    if bad: return {"error": f"invalid tool call: {bad}", "_meta": {"authz": "n/a"}}
    if not (isinstance(principal, auth.Principal) and principal.role == "student"):
        audit.record(actor=getattr(principal, "user_id", "unknown"), role=getattr(principal, "role", "none"), action=f"tool:{name}", decision="denied", outcome="denied", correlation_id=cid, detail="not a signed-in student")
        return {"error": authz.PUBLIC_DENIAL, "_meta": {"authz": "denied"}}
    args = dict(args); requested = args.pop("student_id", None)
    try:
        res = FUNCS[name](principal, cid, requested=requested, **args)
    except authz.Denied as e:
        return {"error": str(e) or authz.PUBLIC_DENIAL, "_meta": {"authz": "denied"}}
    except Exception:                            # never leak DB/stack details to the model or student
        return {"error": "tool failed; please try again or raise a support ticket", "_meta": {"authz": "allowed"}}
    if isinstance(res, dict) and "request_id" in res: meta["approval_request_id"] = res["request_id"]; meta["approval_status"] = "pending"
    return {**res, "_meta": meta}
