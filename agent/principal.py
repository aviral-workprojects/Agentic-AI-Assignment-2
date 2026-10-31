from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class Principal:
    """Authenticated identity. Created only by auth.authenticate(); lives in server-side session state,
    never in chat text or tool arguments."""
    user_id: str
    role: str                     # 'student' | 'approver'
    student_id: Optional[str]     # own record for students, None for approvers
