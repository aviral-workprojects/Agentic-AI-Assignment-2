# Student Support Agent – Assignment 2: privacy, authorization, approval, audit

The same agent as Assignment 1 (chat UI, tool-calling loop, persistent selective memory, handbook retrieval, context selection, tracing, evaluation), now with **sign-in, server-side authorization, human approval for a sensitive change, and a persistent audit log**. All data is **synthetic**; nothing here is production-grade security (see Limitations).

## Architecture
```
app.py (Streamlit)    sign-in form → Principal in server-side session_state
   ├─ student: chat ──► agent/loop.py ──► tools.run(name, args, PRINCIPAL) ──► agent/services.py ─► authz → SQLite + audit
   └─ approver: queue ─► agent/approvals.decide(PRINCIPAL, id, approve|reject) (same service layer, no LLM involved)
agent/auth.py      WHO is signed in (salted scrypt hashes, lockout)       agent/principal.py  immutable identity object
agent/authz.py     WHAT they may do (pure functions, independently tested) agent/audit.py      append-only audit log
agent/approvals.py pending-approval lifecycle (one transaction per decision)
agent/db.py        SQLite, always-closed connections, versioned additive migration (user_version 1 → 2)
unchanged from A1: context.py, memory.py, retrieval.py, redact.py, trace.py, providers/ (Groq default; Gemini, Anthropic optional)
```
**The four concerns are separate:** *Authentication* (auth.py) proves who is signed in. *Authorization* (authz.py, called by services.py) decides what that principal may read/do, immediately before every private read or write and again at execution time. *Approval* (approvals.py) records a human decision for sensitive changes. *Audit* (audit.py) records what was attempted, the decision and the result. A correct login never grants access to another student's data.

## Demo accounts (fictional, local only)
| user id | role | password |
|---|---|---|
| `S101`, `S102`, `S103` | student (own record only) | `Demo-S101-pw`, `Demo-S102-pw`, `Demo-S103-pw` |
| `registrar` | approver (no student record, no chat) | `Demo-Registrar-pw` |

Only salted scrypt hashes are stored (`users` table, separate from student/fee tables); the plaintext demo passwords exist only in `agent/auth.py` to seed the demo database and in this README. Change them before any real use. After 5 failed attempts an account locks for 60 s. Invalid credentials always show the same generic message.

## Privacy and approval rules (short explanation)
1. **Identity comes only from sign-in.** The Principal (`user_id`, `role`, `student_id`) is created by `auth.authenticate()` and held in server-side session state. Chat text, a UI field, or an LLM tool argument is never treated as identity. There is no student selector; switching identity requires Sign out → sign in again.
2. **Authorization is enforced in code, not by the prompt.** Every private read goes through `services.py`, which compares the target with the principal. A model-supplied `student_id` is only a *claim*: if it differs from the signed-in student, the call is denied in code, returns a generic "access denied" (no record contents, no hint whether the id exists), and is audited. Students can read/modify only their own record; the approver role has no access to student private data via the chat tools.
3. **Sensitive action = elective change.** `request_elective_change` validates the request against the student's own enrollment and seats, then creates a **pending** approval row. The enrollment is **not** changed. The LLM has no approve/decide tool and its text cannot change state.
4. **Human decision.** Only a signed-in `approver` can decide (never on their own request). In one `BEGIN IMMEDIATE` transaction the code re-checks permission, the request status, expiry, that the target is the requester's own record, and the change itself against *current* data, then either executes once or records rejection with no execution. Repeated approve/reject clicks are no-ops (`ignored_already_*`). Rejected, expired, failed or already-executed requests can never execute.
5. **Audit.** State change and its audit rows commit or roll back together. Audit rows hold no passwords, OTPs, keys, tool arguments or raw chat text.
6. **Secrets in chat** (OTP/password/keys/cards) are redacted before display, model prompts, history, traces, audit and memory; the memory filter independently rejects them.

## Approval lifecycle
`student request → authz (own record) → validation → PENDING (audit: elective_change_request) → approver decision → reject: status rejected, no execution | approve: revalidate → execute once → status approved (audit: approval_decision + elective_change_execute)`. Statuses: `pending, approved, rejected, failed (revalidation/integrity failed), expired (TTL, default 24 h, `APPROVAL_TTL_S`)`.

## Audit schema (`audit_log`, append-only: UPDATE/DELETE are blocked by triggers)
`id, ts, actor_id, actor_role, student_id (affected), action, resource, decision (allowed|denied|n/a), approval_required, approval_status, approver_id, outcome, request_id (links request→decision→execution), correlation_id (links to the trace line of the chat turn), detail (redacted, ≤200 chars)`. Events: login/logout, protected reads (allowed/denied), tool-level denials, elective change request, approval decision, execution, ignored repeats, failed revalidation. Students see only their own rows ("My audit trail"); the approver sees all. Traces (`traces/trace.jsonl`) are separate and add `actor, role, corr`, per-tool `authz`, `approval_request_id/status`.

## Tools (9): the six from Assignment 1 plus three
`get_student_record, get_fee_status, get_scholarship_status, create_support_ticket, search_policy, save_memory` (A1) · `get_course_info` (own enrollments + electives with seats), `request_elective_change` (creates a PENDING request), `get_my_requests` (own approval status). Retrieval, memory, context selection, tool-error handling and tracing work as in Assignment 1.

## Assignment 1 features (unchanged)
Retrieval: `data/handbook.md` split by heading → BM25, ≤2 chunks above a calibrated threshold, answers cite chunk ids, abstain when nothing relevant (`no_policy` case). Memory: SQLite, 4 whitelisted keys, secret filter, persists across sessions. Context: system prompt + relevant memories + last 4 turns + question; handbook only on demand. Tool errors/missing info: safe messages, the agent asks or offers a ticket. Tracing: one JSON line per turn (tools, chunk ids, memory keys, latency, provider-reported tokens). A1 evaluation: `eval/run_eval.py`, `eval/cases_a1.json`.

## Database and migration
`db.ensure()` runs at app start: creates a fresh DB, or upgrades an existing Assignment 1 DB **additively** (`PRAGMA user_version` 1→2, `CREATE … IF NOT EXISTS`, `INSERT OR IGNORE` seeds only for students that exist). It never drops or resets data; idempotent; approval and audit rows survive reruns. `db.reset()` (tests/eval only) refuses to touch `data/students.db`. Evaluation uses isolated files under `eval/.eval_data/` (`eval.db` for A1, `eval_a2.db` for A2).

## Setup (Windows)
PowerShell:
```powershell
cd C:\Users\Aviral\Downloads\student-support-agent-a1-final\student-support-agent
.\.venv\Scripts\Activate.ps1          # create first if needed: python -m venv .venv
pip install -r requirements.txt
Copy-Item .env.example .env           # skip if .env exists; set GROQ_API_KEY locally, never commit it
```
CMD: `.venv\Scripts\activate.bat`, `pip install -r requirements.txt`, `copy .env.example .env`.
```env
LLM_PROVIDER=groq
GROQ_API_KEY=your_key_here
MODEL=openai/gpt-oss-20b
```
## Run
```
python -m compileall -q .
python -m unittest discover -s tests -t .                 offline (no key, no network)
streamlit run app.py                                      sign in with a demo account
python scripts\smoke_groq.py --with-tool                  live, ONE request (optional)
python eval\run_eval_a2.py                                dry run: prints expected API-call count
python eval\run_eval_a2.py --yes                          live Assignment 2 evaluation (12 turns, ~28 calls typical, max 60) → eval\results_a2.md
python eval\run_eval_a2.py --yes --only elective_reject   single case → eval\results_a2_partial.md
python eval\run_eval.py --yes                             (A1 suite, unchanged; now runs as an authenticated principal)
python scripts\generate_a2_samples.py                     regenerate offline (MOCKED) sample audit/trace records
```
Free-tier notes: Groq limits are account-specific; calls are spaced (`LLM_MIN_INTERVAL_S`, default 2 s); bounded retries; no automatic fallback; **do not upgrade or add payment details**.

## Evaluation (Assignment 2 vs Assignment 1)
* **Preserved A1 baseline** (live, Groq `openai/gpt-oss-20b`, rule-based, synthetic): 13/13 passed, tool selection 11/11, mean latency 4.9 s, p95 8.1 s, 11,842 input / 1,534 output tokens. Authoritative per-case table: `eval/results_a1.md` (never overwritten; the A2 run copies it to `eval/results_a1_baseline.md` if absent). Aggregates are also in `eval/baseline_a1_summary.json`.
* **A2 suite** (`eval/cases_a2.json`, `eval/run_eval_a2.py`): 6 *comparable* A1 cases re-run under an authenticated principal (fee, scholarship, portal policy, memory recall, unsupported policy, OTP) + 6 *new* cases: 3 privacy (cross-student fee, scholarship, modification), 3 approval (pending, reject, approve). Checks inspect **database state and audit rows**, not just wording (record unchanged before approval, rejected ⇒ unchanged, approved ⇒ executed exactly once, every denied tool call has a linked audit row, no secret persisted). The approver step is performed by the harness through the same server-side function the approver UI uses.
* **Report** `eval/results_a2.md` (table per case + task success, privacy tests, unsafe-action tests, latency, tokens, comparison table with A1, like-for-like on comparable cases when the A1 per-case file is present) is **written by the live run**. **It has not been generated in this package: no live A2 run was executed here.** The offline suite validates the harness with a mock model (results labelled mocked, never presented as live).
* **Sample records:** `eval/samples/a2_offline_audit.md` / `a2_offline_trace.jsonl` (MOCKED model, real application code path, synthetic) are included; `eval/samples/a2_live_*` are written by the live run.

## Video checklist (Assignment 2)
1. Architecture + the four concerns (auth / authz / approval / audit); schema upgrade is additive.
2. Sign in as `S101`: identity shown in sidebar; no student selector; wrong password → generic error.
3. Own record: "What is my fee status?" → allowed; show trace (`authz: allowed`) and "My audit trail".
4. Blocked access: "What scholarship does S102 have?" → denied; show audit row (`denied`) and that no data was returned; mention code-level enforcement (`services._guard`).
5. Sensitive action: "Change my elective E101 to E102" → **pending**, record unchanged (`get_course_info`), pending row in "My approval requests".
6. Sign out, sign in as `registrar`: review queue → **Reject** → nothing executed; show audit (`rejected_no_execution`). Repeat approve attempt is ignored.
7. Student requests E101→E104 → registrar **Approves** → executed once; show audit chain request → decision → execute (same `request_id`); a second click does nothing.
8. Sensitive input: type "My OTP is 482913" → shown as `[REDACTED_OTP]`; not in memory, trace or audit.
9. Evaluation: run/show `results_a2.md`: success, privacy, unsafe-action, latency, tokens, A1 comparison; show `eval/samples`.

## Limitations
* **Not production security.** Local coursework demo: no MFA/OTP login, password reset, HTTPS/cookie hardening, per-IP rate limiting (lockout is per account), or encryption at rest; demo passwords are public in this repo. Streamlit session state is the only session mechanism. SQLite triggers make the audit log append-only for the application, but anyone with file access can alter the database.
* Redaction is pattern-based (best effort); the browser input box and local server necessarily see typed text.
* Cross-student denial relies on the model optionally passing `student_id`; if it simply refuses, no audit row exists for that turn (the code path is still enforced and tested).
* Retrieval (6 BM25 chunks) has one known false positive (“placement statistics for last year”); the prompt tells the model to abstain.
* One sensitive action type (elective change) is implemented; the handbook's two-week window is not enforced in code (the approver is the control).
* Rule-based eval on a small synthetic sample; latency includes call spacing.
