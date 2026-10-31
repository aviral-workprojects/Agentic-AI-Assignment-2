import os, pathlib, shutil, subprocess, tempfile, unittest
from agent import redact
from tests.helpers import Base, FakeProvider, text

ROOT = pathlib.Path(__file__).parent.parent
OTP = "482913"

class TestUiRedaction(Base):
    def run_app(self, message, provider):
        import agent.providers as P
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        st.cache_resource.clear(); old = P.get_provider; P.get_provider = lambda *a, **k: provider
        try:
            at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
            at.text_input[0].set_value("S101"); at.text_input[1].set_value("Demo-S101-pw"); at.button[0].click().run()   # real sign-in form
            at.chat_input[0].set_value(message).run(); return at
        finally:
            P.get_provider = old; st.cache_resource.clear()

    def test_otp_never_displayed_or_stored_in_session(self):
        try: import streamlit  # noqa
        except ImportError: self.skipTest("streamlit not installed")
        prov = FakeProvider([text("Please never share OTPs or passwords in chat; I can only see redacted text.")])   # model never receives the raw secret
        at = self.run_app(f"My OTP is {OTP} and my password is hunter2secret, what is my fee?", prov)
        self.assertEqual([e.value for e in at.exception], [])
        shown = " ".join(m.markdown[0].value for m in at.chat_message if m.markdown)
        state = repr(at.session_state["view"]) + repr(at.session_state["history"])
        for blob in (shown, state, repr(at.json), self.trace_text(), repr(prov.calls)):
            self.assertNotIn(OTP, blob); self.assertNotIn("hunter2secret", blob)
        self.assertIn("[REDACTED_OTP]", shown)

    def test_normal_question_unchanged(self):
        try: import streamlit  # noqa
        except ImportError: self.skipTest("streamlit not installed")
        at = self.run_app("What is my current fee status?", FakeProvider([text("Balance is 45000")]))
        self.assertEqual([m.markdown[0].value for m in at.chat_message if m.markdown], ["What is my current fee status?", "Balance is 45000"])

    def test_redaction_idempotent(self):
        once = redact.redact(f"my otp is {OTP} password: abc12345"); self.assertEqual(redact.redact(once), once)

class TestConfigAndGitignore(unittest.TestCase):
    def test_groq_is_the_documented_default(self):
        env = (ROOT / ".env.example").read_text(encoding="utf-8")
        active = [l for l in env.splitlines() if l.strip() and not l.startswith("#")]
        self.assertEqual(active[:3], ["LLM_PROVIDER=groq", "GROQ_API_KEY=your_key_here", "MODEL=openai/gpt-oss-20b"])
        self.assertIn("groq", (ROOT / "requirements.txt").read_text().lower().split())
        for dep in ("rank_bm25", "streamlit", "python-dotenv"): self.assertIn(dep, (ROOT / "requirements.txt").read_text())
        self.assertNotIn("anthropic", (ROOT / "requirements.txt").read_text().lower())
        self.assertNotIn("gemini", (ROOT / "app.py").read_text().lower())
        self.assertIn("GROQ_API_KEY", (ROOT / "app.py").read_text())
        self.assertIn("LLM_PROVIDER=groq", (ROOT / "README.md").read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_gitignore_rules(self):
        d = tempfile.mkdtemp()
        try:
            run = lambda *a: subprocess.run(["git", *a], cwd=d, capture_output=True, text=True)
            run("init", "-q"); shutil.copy(ROOT / ".gitignore", d)
            ignored = [".env", ".env.local", "venv/x", ".venv/x", "agent/__pycache__/a.pyc", "data/students.db", "traces/trace.jsonl",
                       "eval/.eval_data/eval.db", "eval/.eval_data/trace.jsonl", "eval/results_a1_partial.md", "eval/results_a2_partial.md", "eval/results_a1.prev-20261028-120000.md", "eval/results_a2.prev-20261028-120000.md"]
            tracked = [".env.example", "eval/results_a1.md", "eval/results_a1_baseline.md", "eval/results_a2.md", "eval/samples/a2_live_audit.md", "eval/samples/a2_offline_audit.md", "data/handbook.md", "data/sample_data.md", "README.md"]
            for f in ignored: self.assertEqual(run("check-ignore", "-q", f).returncode, 0, f"should be ignored: {f}")
            for f in tracked: self.assertEqual(run("check-ignore", "-q", f).returncode, 1, f"must stay trackable: {f}")
        finally:
            shutil.rmtree(d, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
