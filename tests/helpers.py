import os, tempfile, unittest, pathlib, shutil
from agent.providers.base import LLMError, LLMResponse, ToolCall

class FakeProvider:
    name, model = "fake", "fake-1"
    def __init__(self, script):
        self.script, self.calls = list(script), []
    def generate(self, system, messages, tools):
        self.calls.append((system, [dict(m) for m in messages]))
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception): raise item
        return item

def text(t, i=10, o=5): return LLMResponse(t, [], i, o)
def call(name, args=None, i=10, o=5, valid=True, id="c1"): return LLMResponse("", [ToolCall(id, name, args or {}, valid)], i, o)

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["DB_PATH"] = os.path.join(self.tmp, "t.db")
        os.environ["TRACE_PATH"] = os.path.join(self.tmp, "trace.jsonl")
        os.environ.pop("FAIL_TOOL", None)
        from agent import db; db.reset()
    def tearDown(self): shutil.rmtree(self.tmp, ignore_errors=True)
    def trace_text(self):
        p = pathlib.Path(os.environ["TRACE_PATH"]); return p.read_text() if p.exists() else ""

def P(user_id):
    """Test principal for a seeded demo user (trusted helper; no password)."""
    from agent import auth
    return auth.principal_for(user_id)
