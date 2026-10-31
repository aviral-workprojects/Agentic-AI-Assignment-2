import os, unittest
from types import SimpleNamespace as N
try:
    from google.genai import errors, types
    HAVE = True
except ImportError:
    HAVE = False
from agent.providers import gemini
from agent.providers.base import LLMError, ToolCall
from agent import tools

def resp(parts, usage=None): return N(candidates=[N(content=N(parts=parts))], usage_metadata=usage)

@unittest.skipUnless(HAVE, "google-genai not installed")
class TestGemini(unittest.TestCase):
    def test_parse_text_and_usage(self):
        r = gemini.parse_response(resp([N(text="hi", thought=False, function_call=None)], N(prompt_token_count=12, candidates_token_count=3, thoughts_token_count=7)))
        self.assertEqual((r.text, r.in_tok, r.out_tok), ("hi", 12, 10))
    def test_parse_usage_missing(self):
        r = gemini.parse_response(resp([N(text="hi", function_call=None)], None)); self.assertIsNone(r.in_tok); self.assertIsNone(r.out_tok)
    def test_parse_tool_call_variants(self):
        r = gemini.parse_response(resp([N(function_call=N(name="get_fee_status", args={"a": "b"}, id=None), text=None)]))
        self.assertEqual((r.tool_calls[0].name, r.tool_calls[0].args, r.tool_calls[0].id), ("get_fee_status", {"a": "b"}, "call_0"))
        bad = gemini.parse_response(resp([N(function_call=N(name="x", args="not json{", id="i"), text=None)])).tool_calls[0]
        self.assertFalse(bad.args_valid); self.assertEqual(bad.args, {})
        s = gemini.parse_response(resp([N(function_call=N(name="x", args='{"q":"1"}', id="i"), text=None)])).tool_calls[0]
        self.assertTrue(s.args_valid); lst = gemini.parse_response(resp([N(function_call=N(name="x", args=[1], id="i"), text=None)])).tool_calls[0]
        self.assertFalse(lst.args_valid)
    def test_thought_text_skipped_and_empty_raises(self):
        r = gemini.parse_response(resp([N(text="thinking", thought=True, function_call=None), N(text="answer", thought=False, function_call=None)]))
        self.assertEqual(r.text, "answer")
        with self.assertRaises(LLMError) as c: gemini.parse_response(N(candidates=[], usage_metadata=None))
        self.assertEqual(c.exception.kind, "empty")
    def test_error_mapping(self):
        def k(code, status, msg): return gemini.map_error(errors.APIError(code, {"error": {"code": code, "status": status, "message": msg}})).kind
        self.assertEqual(k(429, "RESOURCE_EXHAUSTED", "quota"), "quota")
        self.assertEqual(k(403, "PERMISSION_DENIED", "Your project has been denied access."), "permission")
        self.assertEqual(k(404, "NOT_FOUND", "model not found"), "model_unavailable")
        self.assertEqual(k(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key."), "auth")
        self.assertEqual(k(400, "FAILED_PRECONDITION", "Free tier is not available in your country; enable billing"), "billing")
        self.assertEqual(k(503, "UNAVAILABLE", "overloaded"), "server")
        self.assertEqual(gemini.map_error(ConnectionError("x")).kind, "network")
        self.assertEqual(gemini.map_error(ValueError("boom")).kind, "unknown")
    def test_error_detail_never_contains_key(self):
        e = gemini.map_error(errors.APIError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "bad key AIzaSyA1234567890abcdefghijklmnop"}}))
        self.assertNotIn("AIzaSyA1234567890", e.detail)
    def test_missing_key_is_config_error(self):
        saved = {k: os.environ.pop(k, None) for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY")}
        try:
            with self.assertRaises(LLMError) as c: gemini.GeminiProvider()
            self.assertEqual(c.exception.kind, "config")
        finally:
            for k, v in saved.items():
                if v: os.environ[k] = v
    def test_declarations_and_contents(self):
        d = gemini.GeminiProvider.declarations(tools.SCHEMAS)
        self.assertEqual(len(d), len(tools.SCHEMAS)); self.assertIsNone([x for x in d if x.name == "get_my_requests"][0].parameters_json_schema)  # no-arg tool
        self.assertIsNotNone([x for x in d if x.name == "search_policy"][0].parameters_json_schema)
        c = gemini.GeminiProvider.contents([
            {"role": "user", "text": "q"},
            {"role": "assistant", "text": "", "tool_calls": [ToolCall("c1", "get_fee_status", {})]},
            {"role": "tool", "results": [{"id": "c1", "name": "get_fee_status", "content": '{"balance": 1}'}]}])
        self.assertEqual([x.role for x in c], ["user", "model", "user"])
        self.assertEqual(c[1].parts[0].function_call.name, "get_fee_status"); self.assertEqual(c[2].parts[0].function_response.response, {"output": {"balance": 1}})

class TestAnthropicBlocks(unittest.TestCase):
    def test_blocks(self):
        try: from agent.providers.anthropic_p import AnthropicProvider
        except ImportError: self.skipTest("anthropic missing")
        b = AnthropicProvider.blocks([{"role": "assistant", "text": "t", "tool_calls": [ToolCall("i", "n", {"a": "b"})]},
                                      {"role": "tool", "results": [{"id": "i", "name": "n", "content": "{}"}]}])
        self.assertEqual(b[0]["content"][1]["type"], "tool_use"); self.assertEqual(b[1]["content"][0]["tool_use_id"], "i")

if __name__ == "__main__":
    unittest.main()
