import json, os, pathlib, shutil, sqlite3, sys, unittest
from types import SimpleNamespace as N
import httpx, groq
from agent import config, redact, loop, memory, auth
from agent.providers import get_provider, groq_p
from agent.providers.base import LLMError, ToolCall
from agent import tools
from tests.helpers import P, Base, FakeProvider, text

def msg(content=None, calls=None): return N(choices=[N(message=N(content=content, tool_calls=calls))], usage=N(prompt_tokens=11, completion_tokens=4))
def tc(name, args, id="t1"): return N(id=id, function=N(name=name, arguments=args))
REQ = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
def status_err(cls, code, body=None, headers=None):
    return cls("m", response=httpx.Response(code, headers=headers or {}, request=REQ), body=body)

class TestParse(unittest.TestCase):
    def test_text_and_usage(self):
        r = groq_p.parse_response(msg("hi")); self.assertEqual((r.text, r.in_tok, r.out_tok, r.tool_calls), ("hi", 11, 4, []))
    def test_usage_missing_not_invented(self):
        x = msg("hi"); x.usage = None; r = groq_p.parse_response(x); self.assertIsNone(r.in_tok); self.assertIsNone(r.out_tok)
    def test_tool_call_json_string(self):
        r = groq_p.parse_response(msg(None, [tc("search_policy", '{"query": "late fee"}')]))
        self.assertEqual((r.tool_calls[0].name, r.tool_calls[0].args, r.tool_calls[0].args_valid), ("search_policy", {"query": "late fee"}, True))
    def test_empty_args_ok_malformed_flagged(self):
        self.assertTrue(groq_p.parse_response(msg(None, [tc("get_fee_status", "")])).tool_calls[0].args_valid)
        for bad in ['{"query": ', "[1,2]", '"str"', 5]:
            c = groq_p.parse_response(msg(None, [tc("x", bad)])).tool_calls[0]; self.assertFalse(c.args_valid, bad); self.assertEqual(c.args, {})
    def test_missing_function_name_flagged(self):
        c = groq_p.parse_response(msg(None, [N(id="i", function=N(name="", arguments="{}"))])).tool_calls[0]
        self.assertFalse(c.args_valid); self.assertIn("error", tools.run(c.name, None, auth.Principal("S101", "student", "S101")))
    def test_empty_raises(self):
        with self.assertRaises(LLMError) as c: groq_p.parse_response(msg("  ", None))
        self.assertEqual(c.exception.kind, "empty")
        with self.assertRaises(LLMError): groq_p.parse_response(N(choices=[], usage=None))

class TestErrors(unittest.TestCase):
    def k(self, e): return groq_p.map_error(e)
    def test_mapping(self):
        e = self.k(status_err(groq.RateLimitError, 429, {"error": {"code": "rate_limit_exceeded", "message": "per day"}}, {"retry-after": "7"}))
        self.assertEqual((e.kind, e.code, e.retry_after), ("quota", 429, 7.0))
        self.assertEqual(self.k(status_err(groq.AuthenticationError, 401, {"error": {"message": "Invalid API Key"}})).kind, "auth")
        self.assertEqual(self.k(status_err(groq.PermissionDeniedError, 403)).kind, "permission")
        self.assertEqual(self.k(status_err(groq.NotFoundError, 404, {"error": {"code": "model_not_found", "message": "x"}})).kind, "model_unavailable")
        self.assertEqual(self.k(status_err(groq.BadRequestError, 400, {"error": {"code": "model_decommissioned", "message": "x"}})).kind, "model_unavailable")
        self.assertEqual(self.k(status_err(groq.BadRequestError, 400, {"error": {"code": "tool_use_failed", "message": "Failed to call a function"}})).kind, "tool_call_failed")
        self.assertEqual(self.k(status_err(groq.BadRequestError, 400, {"error": {"message": "bad"}})).kind, "bad_request")
        self.assertEqual(self.k(status_err(groq.InternalServerError, 503)).kind, "server")
        self.assertEqual(self.k(groq.APITimeoutError(request=REQ)).kind, "timeout")
        self.assertEqual(self.k(groq.APIConnectionError(request=REQ)).kind, "network")
        self.assertEqual(self.k(ValueError("boom")).kind, "unknown")
    def test_key_never_in_detail(self):
        e = self.k(status_err(groq.AuthenticationError, 401, {"error": {"message": "bad key gsk_abcdefghijklmnopqrstuvwxyz123456"}}))
        self.assertNotIn("gsk_abcdefgh", e.detail)
    def test_redact_groq_key(self): self.assertNotIn("gsk_abcdefghijklmnop12", redact.redact("key gsk_abcdefghijklmnop1234 here"))

class FakeClient:
    def __init__(self, outcomes): self.outcomes, self.calls = list(outcomes), []; self.chat = N(completions=N(create=self.create))
    def create(self, **kw):
        self.calls.append(kw); o = self.outcomes.pop(0)
        if isinstance(o, Exception): raise o
        return o

class TestProvider(Base):
    def mk(self, outcomes):
        os.environ["GROQ_API_KEY"] = "gsk_testtesttesttesttest"
        p = groq_p.GroqProvider(min_interval=0); p.client = FakeClient(outcomes); return p
    def test_defaults_and_missing_key(self):
        saved = os.environ.pop("GROQ_API_KEY", None); os.environ.pop("LLM_PROVIDER", None); os.environ.pop("MODEL", None)
        try:
            self.assertEqual((config.provider_name(), config.model_name()), ("groq", "openai/gpt-oss-20b"))
            with self.assertRaises(LLMError) as c: get_provider()
            self.assertEqual(c.exception.kind, "config")
        finally:
            if saved: os.environ["GROQ_API_KEY"] = saved
    def test_retries_bounded(self):
        os.environ["GROQ_MAX_RETRIES"] = "99"; self.assertEqual(config.groq_max_retries(), 3); os.environ.pop("GROQ_MAX_RETRIES")
        self.assertEqual(config.groq_max_retries(), 2)
    def test_request_shape_and_conversion(self):
        p = self.mk([msg("ok")])
        p.generate("SYS", [{"role": "user", "text": "q"},
                           {"role": "assistant", "text": "", "tool_calls": [ToolCall("c1", "get_fee_status", {})]},
                           {"role": "tool", "results": [{"id": "c1", "name": "get_fee_status", "content": '{"balance": 1}'}]}], tools.SCHEMAS)
        kw = p.client.calls[0]
        self.assertEqual([m["role"] for m in kw["messages"]], ["system", "user", "assistant", "tool"])
        self.assertEqual(kw["messages"][2]["tool_calls"][0]["function"]["arguments"], "{}"); self.assertEqual(kw["messages"][3]["tool_call_id"], "c1")
        self.assertEqual(kw["model"], "openai/gpt-oss-20b"); self.assertEqual(kw["tool_choice"], "auto")
        self.assertEqual({t["function"]["name"] for t in kw["tools"]}, {t["name"] for t in tools.SCHEMAS})
    def test_errors_are_mapped_without_chain(self):
        p = self.mk([status_err(groq.InternalServerError, 503)])
        with self.assertRaises(LLMError) as c: p.generate("s", [{"role": "user", "text": "q"}], [])
        self.assertEqual(c.exception.kind, "server"); self.assertIsNone(c.exception.__cause__)
    def test_loop_with_groq_adapter_end_to_end(self):
        p = self.mk([msg(None, [tc("get_fee_status", "{}")]), msg("Balance 45000")])
        ans, tr = loop.chat(p, P("S101"), [], "fee status?")
        self.assertEqual((ans, tr["in_tok"], tr["out_tok"], tr["provider"]), ("Balance 45000", 22, 8, "groq"))
    def test_loop_malformed_tool_call_and_identity_override(self):
        p = self.mk([msg(None, [tc("get_fee_status", '{"student_id": "S102"}')]), msg("sorry")])
        _, tr = loop.chat(p, P("S101"), [], "fees of S102"); self.assertFalse(tr["tools"][0]["ok"])
        p = self.mk([msg(None, [tc("get_fee_status", "{bad json")]), msg("sorry")])
        _, tr = loop.chat(p, P("S101"), [], "x"); self.assertFalse(tr["tools"][0]["ok"])
    def test_provider_error_safe_and_traced_without_secret(self):
        p = self.mk([status_err(groq.AuthenticationError, 401, {"error": {"message": "bad key gsk_abcdefghijklmnopqrstuvwxyz"}})])
        h = []; ans, tr = loop.chat(p, P("S101"), h, "hi")
        self.assertNotIn("gsk_", ans + self.trace_text()); self.assertEqual(h, []); self.assertEqual(tr["error"]["kind"], "auth")

class TestEvalIsolation(Base):
    def test_eval_never_touches_app_db_and_dry_run_is_free(self):
        from eval import run_eval
        from agent import db
        app_db = os.environ["DB_PATH"]; memory.save("S101", "programme", "SENTINEL")
        sys_argv, old_get = sys.argv, __import__("agent.providers", fromlist=["x"]).get_provider
        out = os.path.join(self.tmp, "r.md"); called = []
        import agent.providers as P
        P.get_provider = lambda *a, **k: called.append(1) or FakeProvider([text("hello")])
        env_before = dict(os.environ)
        try:
            sys.argv = ["run_eval", "--only", "small_talk", "--out", out]; run_eval.main()          # dry run
            self.assertEqual(called, [])
            sys.argv = ["run_eval", "--only", "small_talk", "--out", out, "--yes"]; run_eval.main()  # isolated run
            self.assertNotEqual(os.environ["DB_PATH"], app_db)
            os.environ["DB_PATH"] = app_db
            self.assertEqual(memory.relevant("S101", "programme"), {"programme": "SENTINEL"})
            self.assertIn("PASS", pathlib.Path(out).read_text())
        finally:
            sys.argv, P.get_provider = sys_argv, old_get
            os.environ.clear(); os.environ.update(env_before)
            shutil.rmtree(pathlib.Path(run_eval.ROOT) / "eval" / ".eval_data", ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
