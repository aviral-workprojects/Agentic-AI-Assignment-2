import os, pathlib, sqlite3, time
from contextlib import contextmanager

_DEFAULT = str(pathlib.Path(__file__).parent.parent / "data" / "students.db")

def path():
    return os.getenv("DB_PATH", _DEFAULT)

SCHEMA = """
CREATE TABLE students(id TEXT PRIMARY KEY, name TEXT, programme TEXT, batch TEXT, email TEXT);
CREATE TABLE fees(student_id TEXT PRIMARY KEY, total INT, paid INT, due_date TEXT, status TEXT);
CREATE TABLE scholarships(student_id TEXT, name TEXT, status TEXT, amount INT);
CREATE TABLE tickets(id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT, subject TEXT, body TEXT, ts REAL);
CREATE TABLE memory(student_id TEXT, key TEXT, value TEXT, ts REAL, PRIMARY KEY(student_id, key));
"""

TABLES = ("students", "fees", "scholarships", "tickets", "memory")          # Assignment 1 schema (user_version 1)
A2_TABLES = ("audit_log", "approval_requests", "enrollments", "users", "courses")   # added by migration to user_version 2
DROP_ORDER = A2_TABLES + TABLES                                                # children first (foreign keys)
SCHEMA_VERSION = 2

SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS users(
  user_id TEXT PRIMARY KEY, role TEXT NOT NULL CHECK(role IN ('student','approver')),
  student_id TEXT REFERENCES students(id), salt BLOB NOT NULL, pw_hash BLOB NOT NULL, kdf TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1, failed_count INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0,
  CHECK((role='student' AND student_id IS NOT NULL) OR (role='approver' AND student_id IS NULL)));
CREATE TABLE IF NOT EXISTS courses(
  code TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('core','elective')),
  seats INTEGER NOT NULL CHECK(seats>=0));
CREATE TABLE IF NOT EXISTS enrollments(
  student_id TEXT NOT NULL REFERENCES students(id), course_code TEXT NOT NULL REFERENCES courses(code),
  PRIMARY KEY(student_id, course_code));
CREATE TABLE IF NOT EXISTS approval_requests(
  id INTEGER PRIMARY KEY AUTOINCREMENT, requester_id TEXT NOT NULL REFERENCES users(user_id),
  student_id TEXT NOT NULL REFERENCES students(id), action TEXT NOT NULL, payload TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected','failed','expired')),
  created_ts REAL NOT NULL, expires_ts REAL NOT NULL, decided_by TEXT REFERENCES users(user_id), decided_ts REAL,
  decision_note TEXT, executed_ts REAL, result TEXT);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, actor_id TEXT NOT NULL, actor_role TEXT NOT NULL,
  student_id TEXT, action TEXT NOT NULL, resource TEXT,
  decision TEXT NOT NULL CHECK(decision IN ('allowed','denied','n/a')), approval_required INTEGER NOT NULL DEFAULT 0,
  approval_status TEXT, approver_id TEXT, outcome TEXT NOT NULL, request_id INTEGER, correlation_id TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_audit_student ON audit_log(student_id);
CREATE INDEX IF NOT EXISTS ix_audit_request ON audit_log(request_id);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
"""

DEMO_COURSES = [("C101", "Statistics for Analytics", "core", 0), ("C102", "Big Data Systems", "core", 0),
                ("E101", "Deep Learning Applications", "elective", 2), ("E102", "Natural Language Processing", "elective", 1),
                ("E103", "Computer Vision", "elective", 0), ("E104", "Time Series Forecasting", "elective", 5)]
DEMO_ENROLLMENTS = [("S101", "C101"), ("S101", "C102"), ("S101", "E101"), ("S102", "C101"), ("S102", "C102"), ("S102", "E102"),
                    ("S103", "C101"), ("S103", "C102"), ("S103", "E104")]

def is_app_db():
    return os.path.abspath(path()) == os.path.abspath(_DEFAULT)

@contextmanager
def connect():
    """Always-closed connection. NOTE: `with sqlite3.connect()` alone only commits/rolls back and leaves the
    connection (and file handle) open, which breaks file deletion on Windows. Use this instead."""
    p = path()
    pathlib.Path(p).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()

def _init():
    """(Re)create schema + synthetic seed inside ONE closed connection. Never deletes the file."""
    with connect() as c:
        for t in DROP_ORDER:                  # fixed constant names, not user input
            c.execute(f"DROP TABLE IF EXISTS {t}")
        c.execute("PRAGMA user_version=1")
        c.executescript(SCHEMA)
        c.executemany("INSERT INTO students VALUES(?,?,?,?,?)", [
            ("S101", "Aarav Mehta", "PGDM Big Data Analytics", "2025-27", "aarav@example.edu"),
            ("S102", "Diya Rao", "PGDM AI & Data Science", "2025-27", "diya@example.edu"),
            ("S103", "Kabir Singh", "PGDM Big Data Analytics", "2025-27", "kabir@example.edu")])
        c.executemany("INSERT INTO fees VALUES(?,?,?,?,?)", [
            ("S101", 120000, 75000, "2026-11-05", "Partially paid"),
            ("S102", 120000, 120000, "2026-11-05", "Paid"),
            ("S103", 120000, 0, "2026-10-30", "Pending")])
        c.executemany("INSERT INTO scholarships VALUES(?,?,?,?)", [
            ("S102", "Merit Scholarship", "Approved", 30000),
            ("S103", "Need-based Scholarship", "Under review", 20000)])
        migrate(c)

def reset(allow_app_db=False):
    """Reset CONTENTS of the current DB (drop/recreate tables, reseed). Does not delete the file.
    Refuses to touch the application database (data/students.db) unless explicitly allowed."""
    if is_app_db() and not allow_app_db:
        raise RuntimeError("refusing to reset the application database; set DB_PATH to an isolated file")
    _init()

def ensure():
    """Create the DB on first run only. Never wipes existing data."""
    exists = os.path.exists(path())
    if exists:
        with connect() as c:
            have = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if set(TABLES) <= have:
            with connect() as c:
                migrate(c)            # additive, idempotent upgrade; preserves all existing rows
            return
        if have & set(TABLES):
            raise RuntimeError("database has an unexpected schema; not modifying it")
    _init()


def migrate(c):
    """Controlled, additive schema upgrade (never drops or rewrites existing data). Idempotent."""
    v = c.execute("PRAGMA user_version").fetchone()[0]
    if v >= SCHEMA_VERSION:
        return
    c.executescript(SCHEMA_V2)                       # CREATE ... IF NOT EXISTS only
    have = {r[0] for r in c.execute("SELECT id FROM students")}
    from . import auth                               # lazy: auth imports db
    c.executemany("INSERT OR IGNORE INTO courses VALUES(?,?,?,?)", DEMO_COURSES)
    c.executemany("INSERT OR IGNORE INTO enrollments VALUES(?,?)", [e for e in DEMO_ENROLLMENTS if e[0] in have])
    for uid, role, sid, salt, h, kdf in auth.seed_rows():
        if role == "approver" or sid in have:
            c.execute("INSERT OR IGNORE INTO users(user_id,role,student_id,salt,pw_hash,kdf) VALUES(?,?,?,?,?,?)", (uid, role, sid, salt, h, kdf))
    c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
