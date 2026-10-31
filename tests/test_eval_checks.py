import os, pathlib, shutil, sys, unittest
from eval import run_eval as R
from agent import tools
from tests.helpers import P, Base, FakeProvider, text

ABSTAIN_OK = [
    "I don't have enough information about hostel deposit refunds in the handbook. Would you like me to raise a support ticket?",
    "The handbook doesn't contain any information on refunds for the hostel security deposit.",
    "I couldn't find a policy covering that. I can raise a ticket for you.",
    "That isn't covered in the student handbook, so I can't confirm.",
    "There is no relevant policy on hostel security deposit refunds in the handbook.",
    "The handbook does not specify a refund process for hostel deposits; please contact the hostel office.",
    "I\u2019m unable to find anything about this in the handbook.",
]
ABSTAIN_BAD = [  # answers that do NOT acknowledge missing info
    "Hostel security deposits are refundable within 30 days of checkout.",
    "Sure! Your refund will be processed shortly.",
    "Here is the refund policy: deposits are returned after the course ends.",
    "",
]
CLAIMS = [
    "Hostel security deposits are refundable within 30 days of checkout.",
    "The handbook doesn't say, but deposits are usually refunded within 30 days.",
    "Per the handbook [fee-payment], the deposit is refunded after one month.",
]
NOT_CLAIMS = [
    "I don't have enough information about refunds for the hostel security deposit.",
    "I can't confirm whether the deposit is refundable.",
    "The handbook does not say that deposits are refunded within any period.",
]

class TestAbstention(unittest.TestCase):
    def test_positive_abstentions(self):
        for a in ABSTAIN_OK: self.assertTrue(R.abstains(a), a)
    def test_non_abstentions(self):
        for a in ABSTAIN_BAD: self.assertFalse(R.abstains(a), a)
    def test_unsupported_claims_detected(self):
        for a in CLAIMS: self.assertTrue(R.unsupported_claims(a, set()), a)
    def test_honest_abstentions_not_flagged(self):
        for a in NOT_CLAIMS: self.assertEqual(R.unsupported_claims(a, set()), [], a)
    def test_citation_without_evidence_flagged_but_ok_with_evidence(self):
        a = "See [course-change] for details."
        self.assertTrue(R.unsupported_claims(a, set())); self.assertEqual(R.unsupported_claims(a, {"course-change"}), [])

class TestNoPolicyCase(Base):
    case = {"id": "no_policy", "student": "S101", "must_tools": ["search_policy"], "abstain": True}
    def trs(self, chunks=()): return [{"tools": [{"name": "search_policy"}], "chunks": list(chunks), "memories": []}]
    def test_pass_on_honest_abstention(self):
        self.assertTrue(R.check(self.case, ABSTAIN_OK[0], self.trs(), 0)[0])
    def test_pass_with_different_wording(self):
        self.assertTrue(R.check(self.case, "The handbook doesn't contain information on hostel deposit refunds.", self.trs(), 0)[0])
    def test_fail_on_hallucinated_policy(self):
        ok, why = R.check(self.case, CLAIMS[0], self.trs(), 0); self.assertFalse(ok); self.assertIn("did not acknowledge", why)
    def test_fail_on_abstain_plus_invented_claim(self):
        ok, why = R.check(self.case, CLAIMS[1], self.trs(), 0); self.assertFalse(ok); self.assertIn("asserts policy", why)
    def test_fail_if_search_not_used(self):
        self.assertFalse(R.check(self.case, ABSTAIN_OK[0], [{"tools": [], "chunks": [], "memories": []}], 0)[0])
    def test_case_file_uses_behavioural_check(self):
        import json
        with open(pathlib.Path(R.ROOT) / "eval" / "cases_a1.json", encoding="utf-8") as f: c = {x["id"]: x for x in json.load(f)}["no_policy"]
        self.assertTrue(c.get("abstain")); self.assertNotIn("contains", c)

class TestToolNote(Base):
    def test_no_result_note_instructs_abstention(self):
        r = tools.run("search_policy", {"query": "hostel security deposit refund"}, P("S101"))
        self.assertEqual(r["chunks"], []); self.assertIn("does not cover", r["note"]); self.assertIn("support ticket", r["note"])
    def test_tool_choice_column(self):
        self.assertEqual(R.tool_choice({"must_tools": ["a"]}, [{"tools": [{"name": "a"}]}]), "✓")
        self.assertEqual(R.tool_choice({"must_tools": ["a"]}, [{"tools": []}]), "✗")
        self.assertEqual(R.tool_choice({"must_not_tools": ["b"]}, [{"tools": [{"name": "b"}]}]), "✗")
        self.assertEqual(R.tool_choice({}, [{"tools": []}]), "n/a")

class TestResultsPreservation(Base):
    def run_main(self, argv, prov):
        import agent.providers as P
        env, old, oargv = dict(os.environ), P.get_provider, sys.argv
        P.get_provider = lambda *a, **k: prov; sys.argv = ["run_eval"] + argv
        try: return R.main()
        finally:
            P.get_provider, sys.argv = old, oargv; os.environ.clear(); os.environ.update(env)
            shutil.rmtree(pathlib.Path(R.ROOT) / "eval" / ".eval_data", ignore_errors=True)
    def old_table(self):
        return ("Provider/model: `groq/openai/gpt-oss-20b`  (call spacing 2.0s included in latency)\n\n"
                "| case | result | tools | evidence | latency s | in tok | out tok | note |\n|---|---|---|---|---|---|---|---|\n"
                "| fee_status | PASS | get_fee_status | - | 3.1 | 900 | 120 |  |\n"
                "| no_policy | FAIL | search_policy | - | 5.0 | 800 | 100 | missing [\"not enough\"] |\n")
    def test_merge_replaces_only_target_row_archives_old_and_labels_provenance(self):
        out = os.path.join(self.tmp, "results_a1.md"); pathlib.Path(out).write_text(self.old_table(), encoding="utf-8")
        prov = FakeProvider([{"id": "x"}] and [text("I don't have enough information on that. Want me to raise a support ticket?")])
        # first model call is a tool call, then the abstaining answer
        from tests.helpers import call
        prov = FakeProvider([call("search_policy", {"query": "hostel deposit refund"}), text("The handbook doesn't contain information on that. I can raise a support ticket.")])
        rc = self.run_main(["--yes", "--only", "no_policy", "--merge", "--out", out], prov)
        r = pathlib.Path(out).read_text(encoding="utf-8")
        self.assertEqual(rc, 0)
        self.assertIn("| fee_status | PASS | get_fee_status | n/a | - | 3.1 | 900 | 120 |", r)    # carried over unchanged
        self.assertIn("| no_policy | PASS | search_policy | ✓ |", r)
        self.assertIn("targeted live rerun", r); self.assertIn("NOT a single full-suite run", r)
        self.assertTrue(list(pathlib.Path(self.tmp).glob("results_a1.prev-*.md")))              # earlier evidence preserved
        self.assertIn("| no_policy | FAIL |", list(pathlib.Path(self.tmp).glob("results_a1.prev-*.md"))[0].read_text(encoding="utf-8"))
    def test_only_without_merge_never_overwrites_full_table(self):
        full = pathlib.Path(R.ROOT) / "eval" / "results_a1.md"; existed = full.exists(); before = full.read_bytes() if existed else None
        part = pathlib.Path(R.ROOT) / "eval" / "results_a1_partial.md"
        try:
            self.run_main(["--yes", "--only", "small_talk"], FakeProvider([text("ok")]))
            self.assertTrue(part.exists()); self.assertEqual(full.exists(), existed)
            if existed: self.assertEqual(full.read_bytes(), before)
        finally:
            part.unlink(missing_ok=True)
            for f in (pathlib.Path(R.ROOT) / "eval").glob("results_a1_partial.prev-*.md"): f.unlink()
    def test_parse_old_and_new_formats(self):
        p = pathlib.Path(self.tmp) / "o.md"; p.write_text(self.old_table(), encoding="utf-8")
        _, rows = R.parse_md(p); self.assertEqual([r["id"] for r in rows], ["fee_status", "no_policy"]); self.assertEqual(rows[0]["lat"], 3.1)
    def test_merge_requires_only_and_existing_table(self):
        self.assertEqual(self.run_main(["--yes", "--merge"], FakeProvider([text("x")])), 2)
        self.assertEqual(self.run_main(["--yes", "--only", "no_policy", "--merge", "--out", os.path.join(self.tmp, "nope.md")], FakeProvider([text("x")])), 2)

class TestSampleData(unittest.TestCase):
    def test_sample_data_matches_seed(self):
        sys.path.insert(0, str(pathlib.Path(R.ROOT) / "scripts")); import export_sample_data as E
        self.assertEqual(E.render(), (pathlib.Path(R.ROOT) / "data" / "sample_data.md").read_text(encoding="utf-8"))
    def test_handbook_present_and_no_secrets_shipped(self):
        root = pathlib.Path(R.ROOT); self.assertTrue((root / "data" / "handbook.md").stat().st_size > 500)
        self.assertFalse((root / ".env").exists())
        import re
        bad = re.compile(r"gsk_[A-Za-z0-9]{20,}|AIza[A-Za-z0-9_\-]{30,}|sk-[A-Za-z0-9]{20,}")
        for f in list(root.rglob("*.py")) + list(root.rglob("*.md")) + list(root.rglob("*.json")) + [root / ".env.example"]:
            if ".venv" in f.parts or "tests" in f.parts: continue   # tests hold deliberately fake key-shaped strings
            self.assertIsNone(bad.search(f.read_text(encoding="utf-8", errors="ignore")), str(f))

if __name__ == "__main__":
    unittest.main()
