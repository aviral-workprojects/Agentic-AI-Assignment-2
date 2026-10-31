"""Authentication: WHO is signed in. Local demo mechanism (salted scrypt password hashes, per-user lockout).
NOT production authentication: no MFA/OTP, no password reset, no HTTPS/session-cookie hardening."""
import functools, hashlib, hmac, os, time
from . import audit, db
from .principal import Principal

KDF = "scrypt:n=16384,r=8,p=1"
MAX_FAILS, LOCK_SECONDS = 5, 60

# LOCAL DEMO CREDENTIALS (fictional accounts). Only salted hashes are stored in the database.
DEMO_CREDENTIALS = {"S101": ("student", "S101", "Demo-S101-pw"), "S102": ("student", "S102", "Demo-S102-pw"),
                    "S103": ("student", "S103", "Demo-S103-pw"), "registrar": ("approver", None, "Demo-Registrar-pw")}

def _hash(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)

@functools.lru_cache(maxsize=1)
def _seed():
    """Unique random salt per demo user, generated once per process and stored hashed."""
    return tuple((uid, role, sid, (salt := os.urandom(16)), _hash(pw, salt), KDF) for uid, (role, sid, pw) in DEMO_CREDENTIALS.items())

def seed_rows():
    return _seed()

_DUMMY_SALT = os.urandom(16)

def authenticate(username, password):
    """Return a Principal on success, else None. Same generic outcome for unknown user, wrong password, locked or disabled."""
    uid = str(username or "").strip()[:32]; pw = str(password or "")[:128]; now = time.time()
    with db.connect() as c:
        row = c.execute("SELECT * FROM users WHERE lower(user_id)=lower(?) AND active=1", (uid,)).fetchone()
        if row is None or row["locked_until"] > now:
            _hash(pw, row["salt"] if row else _DUMMY_SALT)            # equalise timing
            ok = False; row_id = row["user_id"] if row else None
        else:
            ok = hmac.compare_digest(_hash(pw, row["salt"]), row["pw_hash"]); row_id = row["user_id"]
            if ok:
                c.execute("UPDATE users SET failed_count=0, locked_until=0 WHERE user_id=?", (row_id,))
            else:
                fails = row["failed_count"] + 1
                c.execute("UPDATE users SET failed_count=?, locked_until=? WHERE user_id=?",
                          (0 if fails >= MAX_FAILS else fails, now + LOCK_SECONDS if fails >= MAX_FAILS else 0, row_id))
    if ok:
        p = Principal(row["user_id"], row["role"], row["student_id"])
        audit.record(actor=p.user_id, role=p.role, action="login", decision="allowed", outcome="signed_in", student_id=p.student_id)
        return p
    # failed attempts are audited without the typed username (it may contain a mistyped secret)
    audit.record(actor="anonymous", role="none", action="login", decision="denied", outcome="invalid_credentials", detail="invalid credentials")
    return None

def sign_out(p):
    if isinstance(p, Principal):
        audit.record(actor=p.user_id, role=p.role, action="logout", decision="allowed", outcome="signed_out", student_id=p.student_id)

def principal_for(user_id):
    """TRUSTED-CODE ONLY (tests, eval harness): build a Principal without a password. The UI never calls this."""
    with db.connect() as c:
        r = c.execute("SELECT user_id, role, student_id FROM users WHERE user_id=? AND active=1", (user_id,)).fetchone()
    if r is None: raise KeyError("unknown user")
    return Principal(r["user_id"], r["role"], r["student_id"])
