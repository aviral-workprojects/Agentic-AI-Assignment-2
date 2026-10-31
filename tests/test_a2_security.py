"""Assignment 2 acceptance tests (scenarios A-G) + auth, authz, approvals, audit, migration. Mocked model, synthetic data, no network."""
import json, os, sqlite3, time, unittest
from agent import approvals, audit, auth, authz, db, loop, memory, services, tools
from agent.providers.base import LLMError
from tests.helpers import Base, FakeProvider, P, call, text

SECRETS = ["482913", "hunter2secret"]

def q(sql, *a):
    with db.connect() as c: return [dict(r) for r in c.execute(sql, a).fetchall()]

def enrolled(sid): return sorted(r["course_code"] for r in q("SELECT course_code FROM enrollments WHERE student_id=?", sid))
def seats(code): return q("SELECT seats FROM courses WHERE code=?", code)[0]["seats"]
def audits(**w):
    rows = q("SELECT * FROM audit_log ORDER BY id")
    return [r for r in rows if all(r.get(k) == v for k, v in w.items())]

class TestAuthentication(Base):
    def test_success_roles_and_case_insensitive(self):
        p = auth.authenticate("s101", "Demo-S101-pw")
        self.assertEqual((p.user_id, p.role, p.student_id), ("S101", "student", "S101"))
        r = auth.authenticate("registrar", "Demo-Registrar-pw"); self.assertEqual((r.role, r.student_id), ("approver", None))
    def test_invalid_credentials_generic_and_audited_without_username(self):
        for u, pw in [("S101", "wrong"), ("nobody", "x"), ("hunter2secret", "x"), ("", ""), (None, None)]:
            self.assertIsNone(auth.authenticate(u, pw))
        rows = audits(action="login", decision="denied")
        self.assertEqual(len(rows), 5); self.assertTrue(all(r["outcome"] == "invalid_credentials" and r["actor_id"] == "anonymous" for r in rows))
        self.assertNotIn("hunter2secret", json.dumps(audit.export_rows()))
    def test_passwords_hashed_salted_and_separate_from_student_data(self):
        rows = q("SELECT * FROM users"); self.assertEqual(len(rows), 4)
        self.assertEqual(len({r["salt"] for r in rows}), 4)                     # unique salts
        for r in rows:
            self.assertNotIn(b"Demo-", bytes(r["pw_hash"])); self.assertTrue(r["kdf"].startswith("scrypt"))
        with open(os.environ["DB_PATH"], "rb") as f: raw = f.read()
        for pw in ("Demo-S101-pw", "Demo-Registrar-pw"): self.assertNotIn(pw.encode(), raw)   # never plaintext on disk
        self.assertNotIn("pw_hash", [c["name"] for c in q("PRAGMA table_info(students)")])
    def test_lockout_after_repeated_failures(self):
        for _ in range(auth.MAX_FAILS): self.assertIsNone(auth.authenticate("S101", "bad"))
        self.assertIsNone(auth.authenticate("S101", "Demo-S101-pw"))            # correct password refused while locked
        with db.connect() as c: c.execute("UPDATE users SET locked_until=0 WHERE user_id='S101'")
        self.assertIsNotNone(auth.authenticate("S101", "Demo-S101-pw"))
    def test_sign_out_audited_and_ui_does_not_use_trusted_helper(self):
        auth.sign_out(P("S101")); self.assertEqual(len(audits(action="logout")), 1)
        with open("app.py", encoding="utf-8") as f: self.assertNotIn("principal_for", f.read())

class TestAuthzPure(unittest.TestCase):
    S1, S2, AP = auth.Principal("S101", "student", "S101"), auth.Principal("S102", "student", "S102"), auth.Principal("registrar", "approver", None)
    def test_matrix(self):
        self.assertTrue(authz.can_read_student(self.S1, "S101").allowed); self.assertFalse(authz.can_read_student(self.S1, "S102").allowed)
        self.assertFalse(authz.can_read_student(self.AP, "S101").allowed); self.assertFalse(authz.can_read_student(None, "S101").allowed)
        self.assertFalse(authz.can_read_student("S101", "S101").allowed)       # a bare string is not an identity
        self.assertTrue(authz.can_modify_student(self.S1, "S101").allowed); self.assertFalse(authz.can_modify_student(self.S1, "S102").allowed)
        self.assertTrue(authz.can_decide_approval(self.AP, "S101").allowed); self.assertFalse(authz.can_decide_approval(self.S1, "S101").allowed)
        self.assertFalse(authz.can_decide_approval(self.AP, "registrar").allowed)   # no self-approval
        self.assertTrue(authz.can_view_audit(self.AP).allowed); self.assertFalse(authz.can_view_audit(self.S1, "S102").allowed)

class TestScenarioA_OwnRecord(Base):
    def test_own_fee_allowed_traced_and_audited(self):
        p = FakeProvider([call("get_fee_status"), text("Your balance is 45000.")])
        ans, tr = loop.chat(p, P("S101"), [], "What is my current fee status?")
        res = json.loads(p.calls[1][1][-1]["results"][0]["content"])
        self.assertEqual((res["student_id"], res["balance"]), ("S101", 45000))
        self.assertNotIn("_meta", res)
        self.assertEqual((tr["tools"][0]["name"], tr["tools"][0]["authz"], tr["actor"], tr["role"]), ("get_fee_status", "allowed", "S101", "student"))
        a = audits(action="read_fee_status")
        self.assertEqual([(r["actor_id"], r["decision"], r["outcome"], r["student_id"], r["correlation_id"]) for r in a], [("S101", "allowed", "success", "S101", tr["corr"])])
        self.assertIn(tr["corr"], self.trace_text())
    def test_explicit_own_id_is_fine(self):
        self.assertEqual(tools.run("get_fee_status", {"student_id": "S101"}, P("S101"))["student_id"], "S101")

class TestScenarioB_CrossStudentBlocked(Base):
    def test_direct_service_call_denied_and_audited(self):
        for fn in (services.get_fee_status, services.get_scholarship_status, services.get_student_record, services.get_course_info):
            with self.assertRaises(authz.Denied): fn(P("S101"), "S102")
        rows = audits(decision="denied")
        self.assertEqual(len(rows), 4); self.assertTrue(all(r["actor_id"] == "S101" and r["student_id"] == "S102" and r["outcome"] == "denied" for r in rows))
    def test_llm_generated_tool_call_cannot_override_identity(self):
        blob = []
        for name in ("get_fee_status", "get_scholarship_status", "get_student_record", "get_course_info"):
            for sid in ("S102", "s102", " S102 ", "S102\u200b"):
                r = tools.run(name, {"student_id": sid}, P("S101")); blob.append(json.dumps(r))
                self.assertEqual(r["_meta"]["authz"], "denied", (name, sid)); self.assertIn("access denied", r["error"])
        text_ = " ".join(blob)
        for leak in ("Merit", "30000", "Diya", "diya@", "Natural Language"): self.assertNotIn(leak, text_)
    def test_loop_attempt_is_audited_linked_and_system_keeps_working(self):
        p = FakeProvider([call("get_scholarship_status", {"student_id": "S102"}), text("I can only access your own records."),
                          call("get_fee_status", id="c2"), text("Balance 45000")])
        h = []; ans, tr = loop.chat(p, P("S101"), h, "what scholarship does S102 have?")
        self.assertEqual(tr["tools"][0]["authz"], "denied"); self.assertNotIn("Merit", json.dumps(p.calls, default=str))
        d = audits(decision="denied"); self.assertEqual((len(d), d[0]["correlation_id"], d[0]["student_id"]), (1, tr["corr"], "S102"))
        _, tr2 = loop.chat(p, P("S101"), h, "and my own fees?"); self.assertEqual(tr2["tools"][0]["authz"], "allowed")
    def test_hostile_ids_denied_without_echoing_them(self):
        r = tools.run("get_fee_status", {"student_id": "S101' OR '1'='1"}, P("S101")); self.assertEqual(r["_meta"]["authz"], "denied")
        self.assertIsNone(audits(decision="denied")[0]["student_id"])           # malformed ids are not stored
        self.assertIn("error", tools.run("get_fee_status", {"student_id": 102}, P("S101")))
    def test_modification_of_other_student_blocked(self):
        self.assertFalse(authz.can_modify_student(P("S101"), "S102").allowed)
        r = tools.run("request_elective_change", {"from_course": "E102", "to_course": "E104", "student_id": "S102"}, P("S101"))
        self.assertIn("unexpected arguments", r["error"])                       # no way to name another student
        r = tools.run("request_elective_change", {"from_course": "E102", "to_course": "E104"}, P("S101"))   # S101 is not in E102
        self.assertIn("error", r); self.assertEqual(q("SELECT COUNT(*) n FROM approval_requests")[0]["n"], 0)
        self.assertEqual(enrolled("S102"), ["C101", "C102", "E102"])
    def test_approver_and_non_principals_cannot_use_student_tools(self):
        self.assertEqual(tools.run("get_fee_status", {}, P("registrar"))["_meta"]["authz"], "denied")
        self.assertEqual(tools.run("get_fee_status", {}, "S101")["_meta"]["authz"], "denied")     # bare string is not an identity
        self.assertEqual(tools.run("get_fee_status", {}, None)["_meta"]["authz"], "denied")

class TestScenarioC_PendingApproval(Base):
    def test_request_is_pending_and_changes_nothing(self):
        r = tools.run("request_elective_change", {"from_course": "e101", "to_course": "E102"}, P("S101"), "cid1")
        self.assertEqual(r["status"], "pending_approval"); self.assertIn("PENDING", r["message"]); self.assertIn("NOT changed", r["message"])
        self.assertEqual(enrolled("S101"), ["C101", "C102", "E101"]); self.assertEqual((seats("E101"), seats("E102")), (2, 1))
        row = q("SELECT * FROM approval_requests")[0]
        self.assertEqual((row["requester_id"], row["student_id"], row["status"], row["action"], row["decided_by"]), ("S101", "S101", "pending", "elective_change", None))
        self.assertEqual(json.loads(row["payload"]), {"from": "E101", "to": "E102"})
        a = audits(action="elective_change_request")[0]
        self.assertEqual((a["actor_id"], a["decision"], a["approval_required"], a["approval_status"], a["outcome"], a["request_id"], a["correlation_id"]), ("S101", "allowed", 1, "pending", "pending", row["id"], "cid1"))
    def test_duplicate_pending_request_reused(self):
        a = tools.run("request_elective_change", {"from_course": "E101", "to_course": "E102"}, P("S101")); b = tools.run("request_elective_change", {"from_course": "E101", "to_course": "E102"}, P("S101"))
        self.assertEqual(a["request_id"], b["request_id"]); self.assertEqual(q("SELECT COUNT(*) n FROM approval_requests")[0]["n"], 1)
    def test_invalid_requests_create_nothing(self):
        for frm, to in [("E101", "E103"), ("C101", "E102"), ("E101", "C102"), ("E104", "E102"), ("E101", "E101"), ("E101", "E999"), ("bad code", "E102"), ("E101", "E101; DROP TABLE x")]:
            r = tools.run("request_elective_change", {"from_course": frm, "to_course": to}, P("S101")); self.assertIn("error", r, (frm, to))
        self.assertEqual(q("SELECT COUNT(*) n FROM approval_requests")[0]["n"], 0); self.assertEqual(enrolled("S101"), ["C101", "C102", "E101"])
    def test_llm_text_cannot_grant_approval(self):
        names = [t["name"] for t in tools.SCHEMAS]; self.assertFalse([n for n in names if "approv" in n and n != "request_elective_change" and n != "get_my_requests"])
        self.assertIn("error", tools.run("approve_request", {"request_id": "1"}, P("S101")))
        p = FakeProvider([call("request_elective_change", {"from_course": "E101", "to_course": "E102"}), text("Done, your change has been approved!")])
        loop.chat(p, P("S101"), [], "change E101 to E102")                      # the model LIES; state lives only in the database
        self.assertEqual(q("SELECT status FROM approval_requests")[0]["status"], "pending"); self.assertEqual(enrolled("S101"), ["C101", "C102", "E101"])

class TestScenarioD_Rejected(Base):
    def test_reject_leaves_record_unchanged_and_cannot_be_executed_later(self):
        rid = tools.run("request_elective_change", {"from_course": "E101", "to_course": "E102"}, P("S101"))["request_id"]
        r = approvals.decide(P("registrar"), rid, "reject", "no seat priority")
        self.assertEqual((r["status"], r["executed"]), ("rejected", False))
        row = q("SELECT * FROM approval_requests")[0]; self.assertEqual((row["status"], row["decided_by"], row["result"]), ("rejected", "registrar", "rejected_no_execution"))
        self.assertEqual(enrolled("S101"), ["C101", "C102", "E101"]); self.assertEqual((seats("E101"), seats("E102")), (2, 1))
        a = audits(request_id=rid); self.assertEqual([x["action"] for x in a], ["elective_change_request", "approval_decision"])
        self.assertEqual((a[1]["actor_id"], a[1]["approver_id"], a[1]["approval_status"], a[1]["outcome"]), ("registrar", "registrar", "rejected", "rejected_no_execution"))
        self.assertFalse(audits(action="elective_change_execute"))
        again = approvals.decide(P("registrar"), rid, "approve")                # re-approval attempt after rejection
        self.assertEqual((again["status"], again["executed"]), ("rejected", False)); self.assertEqual(enrolled("S101"), ["C101", "C102", "E101"])
        self.assertEqual(audits(request_id=rid)[-1]["outcome"], "ignored_already_rejected")

class TestScenarioE_Approved(Base):
    def req(self, sid, frm, to): return tools.run("request_elective_change", {"from_course": frm, "to_course": to}, P(sid))["request_id"]
    def test_approve_executes_exactly_once_with_linked_audit(self):
        rid = self.req("S101", "E101", "E102")
        r = approvals.decide(P("registrar"), rid, "approve"); self.assertEqual((r["status"], r["executed"]), ("approved", True))
        self.assertEqual(enrolled("S101"), ["C101", "C102", "E102"]); self.assertEqual((seats("E101"), seats("E102")), (3, 0))
        self.assertEqual(enrolled("S102"), ["C101", "C102", "E102"])            # other students untouched
        for _ in range(3):
            r2 = approvals.decide(P("registrar"), rid, "approve"); self.assertFalse(r2["executed"])
        self.assertEqual((seats("E101"), seats("E102")), (3, 0)); self.assertEqual(len(audits(action="elective_change_execute")), 1)
        chain = [(a["action"], a["outcome"]) for a in audits(request_id=rid)]
        self.assertEqual(chain[:3], [("elective_change_request", "pending"), ("approval_decision", "approved"), ("elective_change_execute", "executed")])
        self.assertTrue(all(a["outcome"] == "ignored_already_approved" for a in audits(request_id=rid)[3:]))
        row = q("SELECT * FROM approval_requests")[0]; self.assertEqual((row["status"], row["decided_by"], row["result"]), ("approved", "registrar", "executed")); self.assertTrue(row["executed_ts"])
    def test_student_cannot_decide_and_nothing_changes(self):
        rid = self.req("S101", "E101", "E102")
        for who in ("S101", "S102", "S103"):
            with self.assertRaises(authz.Denied): approvals.decide(P(who), rid, "approve")
        self.assertEqual(q("SELECT status FROM approval_requests")[0]["status"], "pending"); self.assertEqual(enrolled("S101"), ["C101", "C102", "E101"])
        self.assertEqual(len(audits(action="approval_decision", decision="denied")), 3)
    def test_revalidated_at_execution_time_last_seat(self):
        a, b = self.req("S101", "E101", "E102"), self.req("S103", "E104", "E102")   # both want the single E102 seat
        self.assertTrue(approvals.decide(P("registrar"), a, "approve")["executed"])
        r = approvals.decide(P("registrar"), b, "approve"); self.assertEqual((r["status"], r["executed"]), ("failed", False))
        self.assertEqual(enrolled("S103"), ["C101", "C102", "E104"]); self.assertEqual(audits(request_id=b)[-1]["outcome"], "failed_revalidation")
    def test_expired_request_does_not_execute(self):
        rid = self.req("S101", "E101", "E102")
        with db.connect() as c: c.execute("UPDATE approval_requests SET expires_ts=? WHERE id=?", (time.time() - 5, rid))
        r = approvals.decide(P("registrar"), rid, "approve"); self.assertEqual((r["status"], r["executed"]), ("expired", False)); self.assertEqual(enrolled("S101"), ["C101", "C102", "E101"])
    def test_tampered_target_is_refused(self):
        rid = self.req("S101", "E101", "E102")
        with db.connect() as c: c.execute("UPDATE approval_requests SET student_id='S102' WHERE id=?", (rid,))   # request no longer targets requester's own record
        r = approvals.decide(P("registrar"), rid, "approve"); self.assertEqual((r["status"], r["executed"]), ("failed", False))
        self.assertEqual(enrolled("S102"), ["C101", "C102", "E102"]); self.assertEqual(enrolled("S101"), ["C101", "C102", "E101"])
    def test_failure_during_execution_rolls_everything_back(self):
        rid = self.req("S101", "E101", "E102"); real = audit.record
        def boom(**kw):
            if kw.get("action") == "elective_change_execute": raise RuntimeError("disk full")
            return real(**kw)
        audit.record = boom
        try:
            with self.assertRaises(RuntimeError): approvals.decide(P("registrar"), rid, "approve")
        finally: audit.record = real
        self.assertEqual(q("SELECT status FROM approval_requests")[0]["status"], "pending"); self.assertEqual(enrolled("S101"), ["C101", "C102", "E101"])
        self.assertEqual((seats("E101"), seats("E102")), (2, 1)); self.assertFalse(audits(action="approval_decision"))   # audit rolled back with the state
    def test_approval_survives_reruns(self):
        rid = self.req("S101", "E101", "E104"); approvals.decide(P("registrar"), rid, "approve"); db.ensure()
        self.assertEqual(enrolled("S101"), ["C101", "C102", "E104"]); self.assertEqual(q("SELECT status FROM approval_requests")[0]["status"], "approved"); self.assertTrue(audits(request_id=rid))

class TestScenarioF_Secrets(Base):
    def test_otp_and_password_never_stored_or_forwarded(self):
        memory.save("S101", "programme", "PGDM Big Data Analytics")
        msg = "My OTP is 482913 and my password is hunter2secret, please remember them"
        p = FakeProvider([call("save_memory", {"key": "preferred_contact", "value": "otp 482913"}), call("save_memory", {"key": "language", "value": "password hunter2secret"}, id="c2"), text("I can't store secrets.")])
        h = []; ans, tr = loop.chat(p, P("S101"), h, msg)
        # What the USER typed must never reach the model: system prompt, user turns and tool results are clean on every call.
        # (The scripted "model" here invents the secret in its own tool-call args; those model-authored args are not user input
        # and are never persisted, which the stores check below proves.)
        sent = json.dumps([(system, [m for m in msgs if m["role"] in ("user", "tool")]) for system, msgs in p.calls], default=str)
        stores = json.dumps(h) + self.trace_text() + json.dumps(audit.export_rows()) + json.dumps(q("SELECT * FROM memory"))
        for s in SECRETS:
            self.assertNotIn(s, sent, s); self.assertNotIn(s, stores, s)
        self.assertEqual(memory.relevant("S101", "programme"), {"programme": "PGDM Big Data Analytics"})          # useful memory intact
        self.assertEqual(q("SELECT COUNT(*) n FROM memory")[0]["n"], 1)
        self.assertIn("[REDACTED_OTP]", h[0]["content"])
    def test_audit_detail_is_redacted(self):
        audit.record(actor="S101", role="student", action="x", decision="n/a", outcome="o", detail="my otp is 482913 password: hunter2secret")
        self.assertNotIn("482913", json.dumps(audit.export_rows())); self.assertNotIn("hunter2secret", json.dumps(audit.export_rows()))

class TestScenarioG_AssignmentOneStillWorks(Base):
    def test_normal_support_tasks(self):
        r = tools.run("get_scholarship_status", {}, P("S102")); self.assertEqual(r["scholarships"][0]["status"], "Approved")
        r = tools.run("search_policy", {"query": "payment portal failed next working day"}, P("S101")); self.assertEqual(r["chunks"][0]["id"], "payment-portal-failure")
        self.assertEqual(tools.run("search_policy", {"query": "hostel security deposit refund"}, P("S101"))["chunks"], [])
        self.assertIn("saved", tools.run("save_memory", {"key": "preferred_contact", "value": "WhatsApp"}, P("S101")))
        p = FakeProvider([text("You prefer WhatsApp.")]); loop.chat(p, P("S101"), [], "what contact method do I prefer?")     # new session, no history
        self.assertIn("preferred_contact=WhatsApp", p.calls[0][0])
        t = tools.run("create_support_ticket", {"subject": "portal", "details": "TXN778"}, P("S101")); self.assertEqual(t["status"], "open")
        self.assertEqual(q("SELECT student_id FROM tickets")[0]["student_id"], "S101")
    def test_tool_error_still_handled(self):
        os.environ["FAIL_TOOL"] = "get_fee_status"
        try: self.assertIn("error", tools.run("get_fee_status", {}, P("S101")))
        finally: os.environ.pop("FAIL_TOOL")

class TestAuditLog(Base):
    def test_append_only(self):
        audit.record(actor="S101", role="student", action="x", decision="n/a", outcome="o")
        with self.assertRaises(sqlite3.DatabaseError): q("UPDATE audit_log SET outcome='tampered'")
        with self.assertRaises(sqlite3.DatabaseError): q("DELETE FROM audit_log")
    def test_query_authorization(self):
        services.get_fee_status(P("S101")); services.get_fee_status(P("S102"))
        mine = audit.query(P("S101")); self.assertTrue(mine and all(r["student_id"] == "S101" for r in mine))
        with self.assertRaises(authz.Denied): audit.query(P("S101"), student_id="S102")
        self.assertEqual({r["student_id"] for r in audit.query(P("registrar"))} - {None}, {"S101", "S102"})
        with self.assertRaises(authz.Denied): audit.query(None)
    def test_foreign_keys_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError): q("INSERT INTO enrollments VALUES('S999','C101')")

class TestMigration(Base):
    A1_DDL = """CREATE TABLE students(id TEXT PRIMARY KEY, name TEXT, programme TEXT, batch TEXT, email TEXT);
    CREATE TABLE fees(student_id TEXT PRIMARY KEY, total INT, paid INT, due_date TEXT, status TEXT);
    CREATE TABLE scholarships(student_id TEXT, name TEXT, status TEXT, amount INT);
    CREATE TABLE tickets(id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT, subject TEXT, body TEXT, ts REAL);
    CREATE TABLE memory(student_id TEXT, key TEXT, value TEXT, ts REAL, PRIMARY KEY(student_id, key));"""
    def a1_db(self, path, extra_student=False):
        c = sqlite3.connect(path); c.executescript(self.A1_DDL)
        c.execute("INSERT INTO students VALUES('S101','Aarav Mehta','PGDM BDA','2025-27','a@example.edu')")
        if extra_student: c.execute("INSERT INTO students VALUES('S900','Real Student','X','2025-27','r@example.edu')")
        c.execute("INSERT INTO fees VALUES('S101',120000,75000,'2026-11-05','Partially paid')"); c.execute("INSERT INTO memory VALUES('S101','programme','keep me',1.0)")
        c.execute("INSERT INTO tickets(student_id,subject,body,ts) VALUES('S101','old','ticket',1.0)"); c.commit(); c.close()
    def test_upgrade_preserves_a1_data_and_is_idempotent(self):
        path = os.path.join(self.tmp, "a1.db"); os.environ["DB_PATH"] = path; self.a1_db(path, extra_student=True)
        db.ensure()
        self.assertEqual(q("SELECT value FROM memory")[0]["value"], "keep me"); self.assertEqual(q("SELECT subject FROM tickets")[0]["subject"], "old")
        self.assertEqual(q("SELECT paid FROM fees")[0]["paid"], 75000); self.assertEqual(q("PRAGMA user_version")[0]["user_version"], db.SCHEMA_VERSION)
        users = {r["user_id"] for r in q("SELECT user_id FROM users")}; self.assertEqual(users, {"S101", "registrar"})   # no demo login invented for S900
        self.assertEqual(enrolled("S101"), ["C101", "C102", "E101"]); self.assertEqual(enrolled("S900"), [])
        audit.record(actor="S101", role="student", action="x", decision="n/a", outcome="o")
        counts = {t: q(f"SELECT COUNT(*) n FROM {t}")[0]["n"] for t in db.TABLES + db.A2_TABLES}
        db.ensure(); db.ensure()
        self.assertEqual(counts, {t: q(f"SELECT COUNT(*) n FROM {t}")[0]["n"] for t in db.TABLES + db.A2_TABLES})
        self.assertEqual(q("SELECT value FROM memory")[0]["value"], "keep me")
    def test_fresh_db_created_with_everything(self):
        os.environ["DB_PATH"] = os.path.join(self.tmp, "fresh.db"); db.ensure()
        self.assertEqual({r["name"] for r in q("SELECT name FROM sqlite_master WHERE type='table'")} >= set(db.TABLES + db.A2_TABLES), True)
        self.assertEqual(len(q("SELECT * FROM students")), 3)
    def test_ensure_never_wipes_a2_state(self):
        rid = tools.run("request_elective_change", {"from_course": "E101", "to_course": "E102"}, P("S101"))["request_id"]; db.ensure(); db.ensure()
        self.assertEqual(q("SELECT id FROM approval_requests")[0]["id"], rid); self.assertTrue(audits(request_id=rid))

if __name__ == "__main__":
    unittest.main()
