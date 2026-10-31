import os, pathlib, sys, unittest
ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import generate_a2_samples as G

class TestSamples(unittest.TestCase):
    def test_generator_covers_scenarios_and_leaks_nothing(self):
        before = dict(os.environ); md, tr = G.generate()
        self.assertEqual(dict(os.environ), before)                                  # no env/DB side effects
        for marker in ("A - own fee", "B - cross-student", "C - own elective", "D - approver rejects", "E2 - approver approves", "F - OTP"):
            self.assertIn(marker, md)
        for need in ("read_scholarship_status | denied", "elective_change_request | allowed | 1 | pending", "rejected_no_execution", "elective_change_execute | allowed | 1 | approved | registrar | executed", "ignored_already_approved"):
            self.assertIn(need, md)
        for s in ("482913", "hunter2secret"): self.assertNotIn(s, md + tr)
        self.assertIn("[REDACTED_OTP]", tr); self.assertIn("MOCKED", md)
    def test_committed_samples_are_clean_and_labelled(self):
        for name in ("a2_offline_audit.md", "a2_offline_trace.jsonl"):
            t = (ROOT / "eval" / "samples" / name).read_text(encoding="utf-8")
            for s in ("482913", "hunter2secret", "Demo-S101-pw", "gsk_"): self.assertNotIn(s, t, name)
        self.assertIn("NOT live results", (ROOT / "eval" / "samples" / "a2_offline_audit.md").read_text(encoding="utf-8"))

if __name__ == "__main__":
    unittest.main()
