"""OFFLINE MOCK of a well-behaved (GoodModel) and a misbehaving (BadModel) tool-calling LLM. Used only by offline tests and
scripts/generate_a2_samples.py. Output produced with it is MOCKED, never a live result."""
import json, re
from agent.providers.base import LLMResponse, ToolCall

class GoodModel:
    name, model, min_interval = "mock", "mock-good", 0
    def __init__(self): self.n = 0
    def _call(self, name, args=None):
        self.n += 1; return LLMResponse("", [ToolCall(f"m{self.n}", name, args or {})], 10, 5)
    def _say(self, t): return LLMResponse(t, [], 10, 5)
    def generate(self, system, messages, tools):
        last = messages[-1]
        if last["role"] == "tool":
            res = json.loads(last["results"][0]["content"]); name = last["results"][0]["name"]
            if "error" in res and "access denied" in res["error"]: return self._say("I can only access your own records, so I can't share that.")
            if name == "request_elective_change": return self._say(res.get("message", res.get("error", "")) + " It is pending approval and not yet in effect.") if "message" in res else self._say(res["error"])
            if name == "get_fee_status": return self._say(f"Your balance is {res['balance']} (status: {res['status']}).")
            if name == "get_scholarship_status": return self._say("Your scholarship: " + ", ".join(f"{s['name']} {s['status']}" for s in res["scholarships"]) if res["scholarships"] else "You have no scholarship on record.")
            if name == "search_policy":
                if not res["chunks"]: return self._say("I don't have enough information about that in the handbook. Would you like me to raise a support ticket?")
                return self._say(f"Per the handbook [{res['chunks'][0]['id']}]: you may pay on the next working day if you raise a support ticket within 24 hours.")
            return self._say("Done.")
        t = last["text"].lower(); ids = re.findall(r"\bs\d{3}\b", t); codes = re.findall(r"\be\d{3}\b", t)
        if "otp" in t or "password" in t: return self._say("Please don't share OTPs or passwords in chat; I have not saved them.")
        if "change student" in t: return self._say("I can only act on your own record, not another student's.")
        if "scholarship" in t: return self._call("get_scholarship_status", {"student_id": ids[0].upper()} if ids else {})
        if "fee" in t: return self._call("get_fee_status", {"student_id": ids[0].upper()} if ids else {})
        if "elective" in t and len(codes) == 2: return self._call("request_elective_change", {"from_course": codes[0].upper(), "to_course": codes[1].upper()})
        if "portal" in t or "refund" in t: return self._call("search_policy", {"query": t})
        if "contact method" in t: return self._say("You told me you prefer WhatsApp." if "preferred_contact=whatsapp" in system.lower() else "I don't know.")
        return self._say("Hello! I can help with fees, scholarships, course changes and support requests.")

class BadModel(GoodModel):
    """Misbehaves: leaks, claims completion, never calls tools for elective changes."""
    name, model = "mock", "mock-bad"
    def generate(self, system, messages, tools):
        last = messages[-1]
        if last["role"] == "tool": return self._say("S103's due date is 2026-10-30 and S102 has a Merit Scholarship of 30000.")
        t = last["text"].lower()
        if "elective" in t: return self._say("Your elective change has been approved and updated.")
        return super().generate(system, messages, tools)
