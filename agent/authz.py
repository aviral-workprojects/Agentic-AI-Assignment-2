"""Authorization: WHAT a principal may access or do. Pure functions, no I/O, independently testable.
Called by the service layer immediately before private data is read or changed (and again at execution time)."""
from dataclasses import dataclass
from .principal import Principal

PUBLIC_DENIAL = "access denied: you can only access your own records"

class Denied(Exception):
    """Authorization failure. Message is safe to show to the requester."""

@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""

def _is_student(p):
    return isinstance(p, Principal) and p.role == "student" and bool(p.student_id)

def can_read_student(p, target):
    if not _is_student(p): return Decision(False, "not a student principal")
    if target != p.student_id: return Decision(False, "cross-student access")
    return Decision(True)

def can_modify_student(p, target):
    if not _is_student(p): return Decision(False, "not a student principal")
    if target != p.student_id: return Decision(False, "cross-student modification")
    return Decision(True)

def can_decide_approval(p, requester_user_id):
    """Only an approver may decide; never on their own request (separation of duties)."""
    if not (isinstance(p, Principal) and p.role == "approver"): return Decision(False, "approver role required")
    if p.user_id == requester_user_id: return Decision(False, "cannot decide own request")
    return Decision(True)

def can_view_audit(p, student_id=None):
    if isinstance(p, Principal) and p.role == "approver": return Decision(True)
    if _is_student(p) and student_id == p.student_id: return Decision(True)
    return Decision(False, "audit access denied")
