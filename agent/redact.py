"""Secret/PII redaction. Used before text reaches the model, traces, audit logs, or memory."""
import re

_P = [  # (label, regex)
    ("API_KEY", r"\b(?:AIza[0-9A-Za-z_\-]{20,}|gsk_[0-9A-Za-z]{16,}|sk-[0-9A-Za-z_\-]{16,}|ghp_[0-9A-Za-z]{20,}|xox[bp]-[0-9A-Za-z\-]{10,}|AKIA[0-9A-Z]{12,})\b"),
    ("TOKEN", r"\b(?:bearer|token|session|jwt|api[\s_-]?key|secret)\b\s*(?:is|=|:)?\s*[A-Za-z0-9._\-]{12,}"),
    ("OTP", r"\b(?:otp|one[\s-]?time\s*\w*|(?:verification|security|auth\w*|login|sms)\s+code|pin)\b\D{0,15}\d{4,8}\b"),
    ("PASSWORD", r"\b(?:password|passcode|passwd|pwd)\b\s*(?:is|=|:)?\s*\S+"),
    ("CVV", r"\bcvv2?\b\D{0,10}\d{3,4}\b"),
    ("CARD", r"\b(?:\d[ -]?){13,19}\b"),
    ("EMAIL", r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    ("PHONE", r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)"),
]
_RX = [(l, re.compile(p, re.I)) for l, p in _P]
# subset that must never be persisted in memory (contact details are allowed in memory)
_SECRET = {"API_KEY", "TOKEN", "OTP", "PASSWORD", "CVV", "CARD"}
_BARE = re.compile(r"\b(?:otp|password|passcode|cvv|pin|aadhaar|card number|api key|secret key)\b", re.I)

def redact(text, labels=None):
    s = str(text)
    for label, rx in _RX:
        s = rx.sub(f"[REDACTED_{label}]", s)
    return s

def contains_secret(text):
    s = str(text)
    return bool(_BARE.search(s)) or any(rx.search(s) for l, rx in _RX if l in _SECRET)

def clip(text, n):
    s = redact(text)
    return s if len(s) <= n else s[:n] + "…"
