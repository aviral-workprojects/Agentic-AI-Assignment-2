import json, unittest
from agent import auth, loop, memory, tools, retrieval, redact
from agent.providers.base import LLMError, USER_MESSAGES
from tests.helpers import P, Base, FakeProvider, text, call

class TestLoop(Base):
    def test_tool_then_answer(self):
        p = FakeProvider([call("get_fee_status"), text("Balance is 45000")]); h = []
        ans, tr = loop.chat(p, P("S101"), h, "What is my fee status?")
        self.assertEqual(ans, "Balance is 45000")
        self.assertEqual([t["name"] for t in tr["tools"]], ["get_fee_status"]); self.assertTrue(tr["tools"][0]["ok"])
        self.assertEqual((tr["in_tok"], tr["out_tok"], tr["iters"]), (20, 10, 2))
        tool_msg = p.calls[1][1][-1]; self.assertEqual(tool_msg["role"], "tool"); self.assertIn("45000", tool_msg["results"][0]["content"])
        self.assertEqual(len(h), 2); self.assertIn("get_fee_status", self.trace_text())

    def test_model_cannot_override_identity(self):
        p = FakeProvider([call("get_fee_status", {"student_id": "S102"}), text("denied")])
        _, tr = loop.chat(p, P("S101"), [], "fees of S102")
        self.assertFalse(tr["tools"][0]["ok"]); self.assertEqual(tr["tools"][0]["authz"], "denied")
        content = p.calls[1][1][-1]["results"][0]["content"]
        self.assertIn("access denied", content); self.assertNotIn("Paid", content); self.assertNotIn("120000", content)

    def test_invalid_args_flag(self):
        p = FakeProvider([call("get_fee_status", valid=False), text("ok")])
        _, tr = loop.chat(p, P("S101"), [], "x"); self.assertFalse(tr["tools"][0]["ok"])

    def test_provider_error_keeps_history_and_memory(self):
        h = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        ans, tr = loop.chat(FakeProvider([LLMError("quota", "raw secret detail", 429)]), P("S101"), h, "hi")
        self.assertEqual(ans, USER_MESSAGES["quota"]); self.assertEqual(len(h), 2)
        self.assertEqual(tr["error"], {"kind": "quota", "code": 429}); self.assertNotIn("raw secret detail", self.trace_text())
        self.assertEqual(memory.relevant("S101", "programme"), {})

    def test_iteration_cap(self):
        ans, tr = loop.chat(FakeProvider([call("get_fee_status")]), P("S101"), [], "loop")
        self.assertEqual(tr["iters"], loop.MAX_ITERS); self.assertIn("couldn't complete", ans)

    def test_usage_unavailable_not_invented(self):
        _, tr = loop.chat(FakeProvider([text("hi", None, None)]), P("S101"), [], "hello")
        self.assertIsNone(tr["in_tok"]); self.assertFalse(tr["usage_available"])

    def test_missing_info_prompt_and_direct_question(self):
        p = FakeProvider([text("What is the subject of the ticket?")])
        ans, tr = loop.chat(p, P("S101"), [], "Please raise a ticket."); self.assertEqual(tr["tools"], [])
        self.assertIn("ask one short question", p.calls[0][0]); self.assertIn("?", ans)

    def test_context_is_bounded(self):
        h = [{"role": r, "content": f"m{i}"} for i in range(20) for r in ("user", "assistant")]
        p = FakeProvider([text("ok")]); loop.chat(p, P("S101"), h, "q")
        self.assertEqual(len(p.calls[0][1]), 9)  # last 4 turns + question

class TestRedaction(Base):
    SECRETS = ["482913", "hunter2secret", "AIzaSyA1234567890abcdefghijklmnop", "4111 1111 1111 1111", "9876543210", "me@x.com", "sk-abcdefghijklmnopqrstu"]
    def test_secrets_never_reach_model_history_or_trace(self):
        q = ("My OTP is 482913, password is hunter2secret, key AIzaSyA1234567890abcdefghijklmnop, card 4111 1111 1111 1111, "
             "call 9876543210 mail me@x.com token sk-abcdefghijklmnopqrstu")
        p = FakeProvider([text("Please don't share secrets. " + q)]); h = []   # even if the model echoes them
        ans, tr = loop.chat(p, P("S101"), h, q)
        blob = json.dumps(p.calls) + json.dumps(h) + self.trace_text()
        for s in self.SECRETS: self.assertNotIn(s, blob, s)
    def test_tool_args_redacted_in_trace(self):
        p = FakeProvider([call("create_support_ticket", {"subject": "otp 482913 failed", "details": "password: hunter2secret"}), text("ok")])
        loop.chat(p, P("S101"), [], "ticket"); t = self.trace_text()
        self.assertNotIn("482913", t); self.assertNotIn("hunter2secret", t)
    def test_not_overredacting_normal_text(self):
        for s in ["batch 2025-27", "my fee is 45000", "week 5", "PGDM Big Data Analytics"]: self.assertEqual(redact.redact(s), s)

class TestTools(Base):
    def test_validation_and_scoping(self):
        self.assertIn("error", tools.run("nope", {}, P("S101")))
        self.assertIn("error", tools.run("search_policy", {"query": "x", "student_id": "S102"}, P("S101")))
        self.assertIn("error", tools.run("search_policy", {}, P("S101")))
        self.assertIn("error", tools.run("search_policy", {"query": 5}, P("S101")))
        self.assertIn("error", tools.run("search_policy", "notadict", P("S101")))
        self.assertIn("error", tools.run("search_policy", {"query": "x" * 600}, P("S101")))
        self.assertEqual(tools.run("get_fee_status", {}, P("S102"))["status"], "Paid")
        self.assertEqual(tools.run("get_fee_status", {}, P("S101"))["balance"], 45000)
    def test_parameterized_sql(self):
        r = tools.run("create_support_ticket", {"subject": "x'); DROP TABLE students;--", "details": "d"}, P("S101"))
        self.assertIn("ticket_id", r); self.assertEqual(tools.run("get_student_record", {}, P("S101"))["id"], "S101")
    def test_exception_is_not_leaked(self):
        def boom(sid): raise RuntimeError("/secret/path sqlite3 internals")
        old = tools.FUNCS["get_fee_status"]; tools.FUNCS["get_fee_status"] = boom
        try: r = tools.run("get_fee_status", {}, P("S101"))
        finally: tools.FUNCS["get_fee_status"] = old
        self.assertNotIn("secret", json.dumps(r)); self.assertNotIn("sqlite", json.dumps(r))
    def test_unknown_student(self):
        self.assertIn("error", tools.run("get_student_record", {}, auth.Principal("S999", "student", "S999")))
    def test_policy_not_found(self):
        self.assertEqual(tools.run("search_policy", {"query": "hostel security deposit refund"}, P("S101"))["chunks"], [])

class TestMemory(Base):
    def test_whitelist_and_persistence(self):
        self.assertIn("saved", memory.save("S101", "programme", "PGDM BDA"))
        self.assertIn("saved", memory.save("S101", "batch", "2025-27"))
        self.assertIn("rejected", memory.save("S101", "favourite_food", "pizza"))
        self.assertEqual(memory.relevant("S101", "which programme am I in")["programme"], "PGDM BDA")  # new session = new call
        self.assertEqual(memory.relevant("S102", "programme"), {})
    def test_sensitive_rejected(self):
        for v in ["my otp is 482913", "password hunter2", "AIzaSyA1234567890abcdefghijklmnop", "4111 1111 1111 1111", "cvv 123", "verification code 5521", "bearer abcdefghijklmnop123", "pin 4821"]:
            self.assertIn("rejected", memory.save("S101", "preferred_contact", v), v)
        self.assertEqual(memory.relevant("S101", "contact prefer"), {})
    def test_not_auto_saved(self):
        loop.chat(FakeProvider([text("ok")]), P("S101"), [], "I'm in Pune this week and my programme is BDA")
        self.assertEqual(memory.relevant("S101", "programme"), {})

class TestRetrieval(unittest.TestCase):
    def test_calibration_set(self):
        from eval.calibrate_retrieval import RELEVANT, IRRELEVANT
        for q, exp in RELEVANT: self.assertEqual(retrieval.search(q)[0]["id"], exp, q)
        fp = [q for q in IRRELEVANT if retrieval.search(q)]
        self.assertLessEqual(len(fp), 1, fp)   # known: 'placement statistics for last year' leaks via the word 'year'
    def test_no_irrelevant_second_chunk(self):
        self.assertEqual([c["id"] for c in retrieval.search("minimum attendance for exams")], ["attendance"])
    def test_chunks_have_stable_ids(self):
        self.assertIn("payment-portal-failure", [c["id"] for c in retrieval.CHUNKS])

class TestEvalCheck(Base):
    def test_check_logic(self):
        from eval.run_eval import check
        tr = {"tools": [{"name": "get_fee_status"}], "memories": []}
        case = {"student": "S101", "must_tools": ["get_fee_status"], "contains": [["45000"]], "must_not_tools": ["save_memory"]}
        self.assertTrue(check(case, "You owe 45,000", [tr], 0)[0])
        self.assertFalse(check(case, "unknown", [tr], 0)[0])
        self.assertFalse(check({**case, "must_tools": ["search_policy"]}, "45000", [tr], 0)[0])
        memory.save("S101", "programme", "Pune campus")
        self.assertFalse(check({"student": "S101", "mem_forbid": ["pune"]}, "x", [tr], 0)[0])
        self.assertTrue(check({"student": "S101", "mem_has": {"programme": "pune"}}, "x", [tr], 0)[0])

if __name__ == "__main__":
    unittest.main()
