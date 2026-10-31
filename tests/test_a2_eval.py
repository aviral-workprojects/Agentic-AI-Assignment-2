"""Offline validation of the A2 evaluation harness using the MOCK model (results here are mocked, never live)."""
import os, pathlib, shutil, sys, unittest
from eval import run_eval_a2 as E
from eval.mock_model import BadModel, GoodModel
from tests.helpers import Base

class TestA2Eval(Base):
    def run_main(self, argv, prov):
        import agent.providers as P
        env, old, oargv = dict(os.environ), P.get_provider, sys.argv
        P.get_provider = lambda *a, **k: prov; sys.argv = ["run_eval_a2"] + argv
        try: return E.main()
        finally:
            P.get_provider, sys.argv = old, oargv; os.environ.clear(); os.environ.update(env)
            shutil.rmtree(E.ROOT / "eval" / ".eval_data", ignore_errors=True)
            shutil.rmtree(E.ROOT / "eval" / "samples" / "_t", ignore_errors=True)

    def test_dry_run_costs_nothing_and_estimates_calls(self):
        import agent.providers as P
        called = []; old = P.get_provider; P.get_provider = lambda *a, **k: called.append(1)
        try:
            sys.argv = ["x"]; self.assertEqual(E.main(), 0)
        finally: P.get_provider = old
        self.assertEqual(called, [])
        self.assertEqual(sum(len(c["turns"]) for c in E.load_cases([])), 12)
    def test_good_model_passes_every_case_and_comparison_is_rendered(self):
        out = os.path.join(self.tmp, "r2.md"); app_db = os.environ["DB_PATH"]
        samples = E.ROOT / "eval" / "samples"; before = {p.name: p.read_bytes() for p in samples.glob("*")} if samples.exists() else {}
        try:
            self.assertEqual(self.run_main(["--yes", "--out", out], GoodModel()), 0)
            r = pathlib.Path(out).read_text(encoding="utf-8")
            self.assertIn("12/12", r); self.assertNotIn("| FAIL |", r); self.assertNotIn("ERROR", r)
            self.assertIn("Privacy tests", r); self.assertIn("3/3", r); self.assertIn("Comparison with Assignment 1", r); self.assertIn("13/13 (100%)", r)
            self.assertIn("elective_change_execute:executed", r); self.assertIn("read_fee_status:denied", r)
            self.assertIn("per-case A1 baseline not available", r.replace("Per-case", "per-case"))
        finally:                                         # the offline run must not leave mock samples masquerading as live ones
            for p in samples.glob("*"):
                if p.name in before: p.write_bytes(before[p.name])
                else: p.unlink()
    def test_bad_model_is_caught_by_the_checks(self):
        out = os.path.join(self.tmp, "r2.md")
        try: self.run_main(["--yes", "--out", out, "--only", "priv_cross_fee,elective_pending"], BadModel())
        finally: pass
        r = pathlib.Path(out).read_text(encoding="utf-8")
        self.assertEqual(r.count("| FAIL |"), 2); self.assertIn("forbidden text", r); self.assertIn("claims completion before approval", r)
    def test_baseline_per_case_comparison_when_a1_table_present(self):
        out = os.path.join(self.tmp, "r2.md"); base = E.ROOT / "eval" / "results_a1_baseline.md"; assert not base.exists()
        rows = "\n".join(f"| {i} | PASS | x | - | 4.0 | 900 | 100 |  |" for i in E.load_cases([]) and ["fee_status", "scholarship", "portal_failed", "memory_recall_new_session", "no_policy", "sensitive_otp"])
        base.write_text("Provider/model: `groq/x`\n\n| case | result | tools | evidence | latency s | in tok | out tok | note |\n|---|---|---|---|---|---|---|---|\n" + rows + "\n", encoding="utf-8")
        try: self.run_main(["--yes", "--out", out, "--only", "fee_status,scholarship,portal_failed,memory_recall_new_session,no_policy,sensitive_otp"], GoodModel())
        finally: base.unlink()
        self.assertIn("Like-for-like", pathlib.Path(out).read_text(encoding="utf-8"))
    def test_eval_never_touches_application_db_or_a1_results(self):
        from agent import memory
        memory.save("S101", "programme", "SENTINEL"); app_db = os.environ["DB_PATH"]; a1 = E.ROOT / "eval" / "results_a1.md"; existed = a1.exists()
        self.run_main(["--yes", "--out", os.path.join(self.tmp, "r2.md"), "--only", "fee_status"], GoodModel()); os.environ["DB_PATH"] = app_db
        self.assertEqual(memory.relevant("S101", "programme"), {"programme": "SENTINEL"}); self.assertEqual(a1.exists(), existed)

if __name__ == "__main__":
    unittest.main()
