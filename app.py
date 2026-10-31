import time
import streamlit as st
import agent  # noqa: F401  (loads .env on import)
from agent import approvals, audit, auth, authz, db, loop, redact, trace
from agent.ui import md_table
from agent.providers import get_provider
from agent.providers.base import LLMError

st.set_page_config(page_title="Student Support Agent")
db.ensure()   # creates the DB on first run / additive upgrade; never wipes data

@st.cache_resource
def provider():
    return get_provider()

def sign_out():
    auth.sign_out(st.session_state.get("principal"))
    for k in list(st.session_state.keys()):
        del st.session_state[k]       # principal, chat history, display history, panel messages

def ts(x): return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(x)) if x else ""

# ---------------------------------------------------------------- sign-in (authentication)
principal = st.session_state.get("principal")
st.title("Student Support Agent")
if principal is None:
    st.subheader("Sign in")
    with st.form("login", clear_on_submit=True):
        uid = st.text_input("User ID"); pw = st.text_input("Password", type="password")
        go = st.form_submit_button("Sign in")
    if go:
        p = auth.authenticate(uid, pw)
        if p is None: st.error("Invalid credentials.")
        else:
            st.session_state.clear(); st.session_state.principal = p; st.rerun()
    st.caption("Local coursework demo with fictional accounts (see README). Not production authentication.")
    st.stop()

st.sidebar.markdown(f"**Signed in as:** `{principal.user_id}`  \n**Role:** {principal.role}")
st.sidebar.button("Sign out", on_click=sign_out)
st.sidebar.caption("Switching identity requires signing out and signing in again.")

# ---------------------------------------------------------------- approver console (human approval)
if principal.role == "approver":
    st.subheader("Approval queue")
    if msg := st.session_state.pop("decision_msg", None): st.info(msg)
    def decide(rid, decision):
        try:
            r = approvals.decide(principal, rid, decision, st.session_state.get(f"note_{rid}", ""))
            st.session_state.decision_msg = r.get("message") or r.get("error")
        except authz.Denied as e:
            st.session_state.decision_msg = f"Not permitted: {e}"
    pending = approvals.list_requests(principal, "pending")
    if not pending: st.write("No pending requests.")
    for r in pending:
        with st.expander(f"Request #{r['id']} · {r['requester']} · {r['summary']}", expanded=True):
            st.write(f"Action: {r['action']}  \nTarget record: {r['student_id']} (requester's own)  \nCreated: {ts(r['created_ts'])}  \nExpires: {ts(r['expires_ts'])}")
            st.text_input("Decision note (optional)", key=f"note_{r['id']}")
            a, b = st.columns(2)
            a.button("Approve", key=f"ap_{r['id']}", on_click=decide, args=(r["id"], "approve"))
            b.button("Reject", key=f"rj_{r['id']}", on_click=decide, args=(r["id"], "reject"))
    st.subheader("Recent decisions"); st.markdown(md_table([x for x in approvals.list_requests(principal) if x["status"] != "pending"], ["id", "requester", "summary", "status", "decided_by"]))
    st.subheader("Audit log (latest 50)")
    st.markdown(md_table([e | {"ts": ts(e["ts"])} for e in audit.query(principal, limit=50)], ["ts", "actor_id", "actor_role", "student_id", "action", "decision", "approval_status", "approver_id", "outcome", "request_id"]))
    st.stop()

# ---------------------------------------------------------------- student chat (authorization happens in the service layer)
try:
    prov = provider()
except LLMError as e:   # config problems only; the message is ours and contains no secrets
    st.error("Setup problem: " + (e.detail or "invalid configuration"))
    st.markdown("Fix `.env` (see README), then restart. Example: `LLM_PROVIDER=groq`, `GROQ_API_KEY=your_key_here`, `MODEL=openai/gpt-oss-20b`")
    st.stop()
st.sidebar.caption(f"Provider: {prov.name} · Model: {prov.model}")
st.session_state.setdefault("history", []); st.session_state.setdefault("view", [])
if st.sidebar.button("New chat (memory persists)"):
    st.session_state.history, st.session_state.view = [], []

for role, text, tr in st.session_state.view:
    with st.chat_message(role):
        st.write(text)
        if tr:
            with st.expander("trace"): st.json(tr)

if q := st.chat_input("Ask about fees, scholarships, courses..."):
    shown = redact.redact(q)   # raw text is never displayed, kept in session history, or passed on (loop.chat redacts again as a 2nd safeguard)
    del q
    with st.chat_message("user"): st.write(shown)
    try:
        ans, tr = loop.chat(prov, principal, st.session_state.history, shown)
    except Exception:
        ans, tr = "Something went wrong. Please try again.", None
    ans, tr = redact.redact(ans), (trace.clean(tr) if tr else None)
    with st.chat_message("assistant"):
        st.write(ans)
        if tr:
            with st.expander("trace"): st.json(tr)
    st.session_state.view += [("user", shown, None), ("assistant", ans, tr)]

with st.expander("My approval requests"):
    st.markdown(md_table(approvals.list_requests(principal), ["id", "summary", "status"]))
with st.expander("My audit trail"):
    st.markdown(md_table([e | {"ts": ts(e["ts"])} for e in audit.query(principal, limit=20)], ["ts", "action", "decision", "approval_status", "outcome", "request_id"]))
