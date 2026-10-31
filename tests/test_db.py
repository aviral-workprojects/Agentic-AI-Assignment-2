import os, pathlib, sqlite3, sys, unittest
from agent import db, memory, tools, loop
from agent.providers.base import LLMError
from tests.helpers import P, Base, FakeProvider, text, call

def rows(path, sql):
    c = sqlite3.connect(path)
    try: return c.execute(sql).fetchall()
    finally: c.close()

def open_handles(path):
    d = "/proc/self/fd"
    if not os.path.isdir(d): return None
    n = 0
    for fd in os.listdir(d):
        try:
            if os.readlink(f"{d}/{fd}").endswith(os.path.basename(path)): n += 1
        except OSError: pass
    return n

class TestDbReset(Base):
    def test_repeated_reset_never_deletes_file_and_reseeds(self):
        p = os.environ["DB_PATH"]; real_remove = os.remove
        def no_remove(*a, **k): raise AssertionError("db reset must not delete the file")
        os.remove = no_remove
        try:
            for i in range(30):
                db.reset()
                self.assertEqual(rows(p, "SELECT id FROM students ORDER BY id"), [("S101",), ("S102",), ("S103",)])
                self.assertEqual(rows(p, "SELECT COUNT(*) FROM fees")[0][0], 3)
                self.assertEqual(rows(p, "SELECT COUNT(*) FROM scholarships")[0][0], 2)
                self.assertEqual(rows(p, "SELECT COUNT(*) FROM tickets")[0][0], 0)     # previous iteration's writes are gone
                self.assertEqual(rows(p, "SELECT COUNT(*) FROM memory")[0][0], 0)
                memory.save("S101", "programme", f"p{i}"); tools.run("create_support_ticket", {"subject": "s", "details": "d"}, P("S101"))
                tools.run("get_fee_status", {}, P("S101")); tools.run("get_scholarship_status", {}, P("S102"))
                self.assertEqual(rows(p, "SELECT COUNT(*) FROM tickets")[0][0], 1)
                self.assertEqual(tools.run("get_fee_status", {}, P("S101"))["balance"], 45000)
        finally:
            os.remove = real_remove
        self.assertEqual(os.path.getsize(p) > 0, True)

    def test_ticket_ids_restart_after_reset(self):
        tools.run("create_support_ticket", {"subject": "s", "details": "d"}, P("S101")); db.reset()
        self.assertEqual(tools.run("create_support_ticket", {"subject": "s", "details": "d"}, P("S101"))["ticket_id"], 1)

    def test_connect_closes_on_success_and_on_error(self):
        with db.connect() as c: c.execute("SELECT 1")
        with self.assertRaises(sqlite3.ProgrammingError): c.execute("SELECT 1")
        with self.assertRaises(ValueError):
            with db.connect() as c2:
                c2.execute("INSERT INTO tickets(student_id,subject,body,ts) VALUES('S101','x','y',0)"); raise ValueError("boom")
        with self.assertRaises(sqlite3.ProgrammingError): c2.execute("SELECT 1")
        self.assertEqual(rows(os.environ["DB_PATH"], "SELECT COUNT(*) FROM tickets")[0][0], 0)   # rolled back

    def test_no_open_handles_after_agent_activity(self):
        p = os.environ["DB_PATH"]
        if open_handles(p) is None: self.skipTest("needs /proc (Linux); closure is covered by test_connect_closes")
        memory.save("S101", "programme", "x"); memory.relevant("S101", "programme")
        loop.chat(FakeProvider([call("get_fee_status"), text("ok")]), P("S101"), [], "fee?")
        loop.chat(FakeProvider([LLMError("quota")]), P("S101"), [], "x")
        try: tools.run("get_fee_status", {}, P("S999"))
        except Exception: pass
        self.assertEqual(open_handles(p), 0)

    def test_reset_refuses_application_db(self):
        saved = os.environ.pop("DB_PATH")
        try:
            existed = os.path.exists(db.path()); mtime = os.path.getmtime(db.path()) if existed else None
            with self.assertRaises(RuntimeError): db.reset()
            self.assertEqual(os.path.exists(db.path()), existed)
            if existed: self.assertEqual(os.path.getmtime(db.path()), mtime)
        finally:
            os.environ["DB_PATH"] = saved

    def test_ensure_does_not_wipe_existing_data(self):
        memory.save("S101", "programme", "keep me"); db.ensure()
        self.assertEqual(memory.relevant("S101", "programme"), {"programme": "keep me"})
        p = os.environ["DB_PATH"]; os.remove(p); db.ensure()      # first run creates
        self.assertEqual(rows(p, "SELECT COUNT(*) FROM students")[0][0], 3)
        c = sqlite3.connect(p); c.execute("DROP TABLE fees"); c.commit(); c.close()
        with self.assertRaises(RuntimeError): db.ensure()         # partial schema: refuse, don't rewrite

class TestEvalRunner(Base):
    def run_main(self, argv, provider):
        import agent.providers as P
        from eval import run_eval
        saved_env, old_get, old_argv = dict(os.environ), P.get_provider, sys.argv
        P.get_provider = lambda *a, **k: provider; sys.argv = ["run_eval"] + argv
        try: return run_eval.main(), run_eval
        finally:
            sys.argv, P.get_provider = old_argv, old_get
            os.environ.clear(); os.environ.update(saved_env)
            import shutil; shutil.rmtree(pathlib.Path(run_eval.ROOT) / "eval" / ".eval_data", ignore_errors=True)

    def test_case_crash_is_reported_and_run_continues_with_partial_results(self):
        out = os.path.join(self.tmp, "r.md")
        prov = FakeProvider([RuntimeError("boom /secret/path"), text("Hello, I can help with fees.")])
        rc, _ = self.run_main(["--yes", "--only", "fee_status,small_talk", "--out", out], prov)
        r = pathlib.Path(out).read_text()
        self.assertEqual(rc, 0); self.assertIn("harness error: RuntimeError", r); self.assertIn("| small_talk | PASS", r)
        self.assertEqual(os.environ.get("FAIL_TOOL", ""), "")

    def test_fail_tool_is_per_case_and_cleared(self):
        out = os.path.join(self.tmp, "r.md"); seen = []
        class P(FakeProvider):
            def generate(s, system, messages, tools_):
                seen.append(os.environ.get("FAIL_TOOL", "")); return text("Unable to fetch, please try again or raise a ticket.")
        rc, _ = self.run_main(["--yes", "--only", "tool_error,small_talk", "--out", out], P([text("x")]))
        self.assertEqual(seen, ["get_fee_status", ""]); self.assertEqual(os.environ.get("FAIL_TOOL", ""), "")

    def test_repeated_eval_runs_leave_no_handles_and_never_touch_app_db(self):
        p_app = os.environ["DB_PATH"]; memory.save("S101", "programme", "SENTINEL"); out = os.path.join(self.tmp, "r.md")
        handles = []
        for _ in range(3):
            self.run_main(["--yes", "--only", "small_talk,create_ticket,fee_status", "--out", out], FakeProvider([text("ok")]))
            os.environ["DB_PATH"] = p_app
            self.assertEqual(memory.relevant("S101", "programme"), {"programme": "SENTINEL"})
        evp = str(pathlib.Path(__file__).parent.parent / "eval" / ".eval_data" / "eval.db")
        h = open_handles(evp)
        if h is not None: self.assertEqual(h, 0)

    def test_missing_provider_config_is_clean(self):
        import agent.providers as P
        def bad(*a, **k): raise LLMError("config", "GROQ_API_KEY is not set (put it in .env)")
        from eval import run_eval
        old, oldargv, env = P.get_provider, sys.argv, dict(os.environ)
        P.get_provider = bad; sys.argv = ["run_eval", "--yes", "--only", "small_talk", "--out", os.path.join(self.tmp, "r.md")]
        try: self.assertEqual(run_eval.main(), 2)
        finally:
            P.get_provider, sys.argv = old, oldargv; os.environ.clear(); os.environ.update(env)
            import shutil; shutil.rmtree(pathlib.Path(run_eval.ROOT) / "eval" / ".eval_data", ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
