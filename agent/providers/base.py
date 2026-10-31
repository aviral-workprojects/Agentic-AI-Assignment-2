import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Optional

USER_MESSAGES = {
    "config": "The assistant is not configured correctly. Please contact the administrator.",
    "auth": "The assistant could not authenticate with its AI service. Please contact the administrator.",
    "permission": "The AI service refused access for this project. Please contact the administrator.",
    "billing": "The AI service is not available for this project's plan. Please contact the administrator.",
    "model_unavailable": "The configured AI model is unavailable. Please contact the administrator.",
    "quota": "The assistant is busy or its usage limit is reached. Please try again later.",
    "network": "Could not reach the AI service. Please check the connection and try again.",
    "server": "The AI service had a temporary problem. Please try again.",
    "bad_request": "The assistant could not process that request. Please rephrase and try again.",
    "timeout": "The AI service took too long to respond. Please try again.",
    "tool_call_failed": "The assistant produced an invalid tool call. Please rephrase and try again.",
    "empty": "The assistant returned no answer. Please rephrase and try again.",
    "unknown": "Something went wrong. Please try again.",
}
FATAL_KINDS = {"config", "auth", "permission", "billing", "model_unavailable", "quota", "server", "timeout"}  # eval stops on these (never hammer a failing/limited API)

class LLMError(Exception):
    """kind: see USER_MESSAGES. `detail` is sanitized and for operators/smoke test only, never for students."""
    def __init__(self, kind, detail="", code=None, retry_after=None):
        super().__init__(kind)
        self.kind, self.detail, self.code, self.retry_after = kind, detail, code, retry_after

@dataclass
class ToolCall:
    id: str
    name: str
    args: dict = field(default_factory=dict)
    args_valid: bool = True
    raw: Any = None  # provider-native part, echoed back verbatim (e.g. Gemini thought signatures)

@dataclass
class LLMResponse:
    text: str
    tool_calls: list
    in_tok: Optional[int]   # None = provider did not report
    out_tok: Optional[int]

# Internal message format (provider-neutral):
#   {"role":"user","text":str}
#   {"role":"assistant","text":str,"tool_calls":[ToolCall]}
#   {"role":"tool","results":[{"id":str,"name":str,"content":str(JSON)}]}

def safe_args(a):
    """Model-produced tool arguments (dict, JSON string, None, other) -> (dict, valid)."""
    if a is None or a == "": return {}, True
    if isinstance(a, Mapping): return dict(a), True
    if isinstance(a, (str, bytes)):
        try:
            v = json.loads(a)
            if isinstance(v, dict): return v, True
        except ValueError:
            pass
    return {}, False
