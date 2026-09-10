# Design decisions

Short ADR-style notes on the choices that shaped this codebase.

## 1. Hand-rolled agent loop, not LangGraph / LangChain

**Decision.** Implement the tool-calling loop directly (`agent.py`, ~180 lines)
against a small `LLMClient` protocol.

**Why.** The brief grades *"the harness you build around the LLM"*. A framework
would hide exactly the part under review - the decision logic, the budgets, the
recovery from a bad tool call - inside its own abstractions. The loop here is one
readable function that streams typed events; you can see every branch. The cost
(reimplementing retries, tool dispatch, message bookkeeping) is a few dozen lines
and it's all tested.

**Trade-off.** No free graph visualisation, no built-in checkpointer. We add the
pieces we actually need (SQLite sessions, an idempotency ledger) and skip the
rest.

## 2. Groq (`openai/gpt-oss-20b`) over raw httpx

**Decision.** Talk to Groq's OpenAI-compatible endpoint with `httpx`, no SDK.

**Why.** One endpoint, one request shape, native tool calling. The provider is a
one-file swap behind `LLMClient`; a pinned SDK would be a heavier dependency for
~60 lines of HTTP. `MalformedToolCall` handling (Groq returns a 400 when the
model emits schema-invalid tool args) lives in this file and is fed back into the
loop.

## 3. Synchronous core

**Decision.** The agent core, tools and backend client are sync. FastAPI runs the
sync endpoints in its threadpool; SSE streams a sync generator.

**Why.** Readability for review, and the workload is a short chain of sequential
HTTP calls, not high-concurrency fan-out. An async rewrite would touch every file
for no user-visible gain at this scale. The seams (`LLMClient`, `BackendClient`)
are narrow enough that an async implementation could be dropped in later.

## 4. Memory in the backend's `preferences` blob

**Decision.** Durable customer memory (seating, dietary, allergies, notes) is
stored via `PATCH /customers/{id}/preferences`; order history is derived on load.

**Why.** It's the store the exercise intends, it survives restarts for free, and
it needs no second datastore. Schema is ours: `{seating, dietary[], allergies[],
notes[]}`. `remember_preference` is the only writer, and the prompt tells the
model to use it for *lasting* preferences, not one-off requests.

## 5. SQLite for session state, not Postgres

**Decision.** `store/session_store.py` uses stdlib `sqlite3` in WAL mode, one
short-lived connection per call.

**Why.** Zero-ops for a local/demo build; the schema (`sessions`,
`processed_messages`) is trivial and needs no migration tool. **For production**
this moves to a managed Postgres-compatible store and the process stops being
single-writer - noted in the README limitations.

## 6. Deterministic policy layer, not prompt-only guardrails

**Decision.** Confirmation-before-destructive and booking-precondition checks are
pure code in `policy.py`, applied before dispatch - not left to the prompt.

**Why.** Prompts are probabilistic; a cancellation guard shouldn't be. The checks
are unit-tested with no LLM, and they degrade gracefully: a block is returned to
the model as a tool result, so the model still phrases the "are you sure?"
question naturally.

## 7. `ToolResult` envelope + `ErrorCode` enum

**Decision.** Every tool returns `ToolResult(ok, message, data, error_code,
http_status)`; the loop, the API and the tests branch on `error_code`, never on
substring matches.

**Why.** Typed outcomes are testable and stable. The `message` field doubles as a
ready-made natural-language summary for the model.

## 8. Idempotency keys on messages

**Decision.** `POST .../messages` accepts an optional `client_message_id`; a
replay returns the stored response, a reused id with a new body is a 409.

**Why.** A reception agent performs mutations (bookings, orders). A dropped
response + client retry must not double-book. The SSE endpoint deliberately skips
this - streaming and idempotent replay don't compose cleanly - and says so.

## Assumptions (also in the README)

- Naive local time throughout, matching the backend. "tonight"/"tomorrow" resolve
  against the server clock; a loose time is snapped to the nearest valid slot and
  the backend still has the final say.
- With no explicit area and no stored preference, `book_table` is gated once, then
  the smallest fitting table wins.
- Memory is written only on an explicit lasting-preference statement.
- "That reservation" = the one created or last acted on this conversation
  (`Session.active_reservation_id`); if there is none the agent asks.
- One customer per session; phone/email identifies them up front and is not
  re-checked. Real deployment needs actual auth.
