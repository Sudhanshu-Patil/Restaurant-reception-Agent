# Restaurant Reception Agent

[![CI](https://github.com/Sudhanshu-Patil/restaurant-reception-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Sudhanshu-Patil/restaurant-reception-agent/actions/workflows/ci.yml)
![coverage](https://img.shields.io/badge/coverage-90%25-brightgreen)
![python](https://img.shields.io/badge/python-3.10%20%7C%203.12-blue)
[![checked: mypy strict](https://img.shields.io/badge/mypy-strict-2a6db2)](https://mypy-lang.org/)
[![lint: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

A multi-turn AI reception agent that books tables, manages orders, answers menu
questions, and remembers returning customers. Built **on top of** the provided
`restaurant-api`, which it only ever reaches over HTTP - never imports, never
modifies.

It is a **hand-rolled agent harness**, not a framework wrapper: a small,
inspectable tool-calling loop behind a provider-agnostic LLM seam, with a
deterministic guardrail layer, typed tool results, session persistence, an
idempotency ledger, structured logging, and Server-Sent-Events streaming.

---

## Run it

### One command (Docker)

```bash
export GROQ_API_KEY=...            # or put it in a .env file at the repo root
docker compose up -d --build       # from the repository root - starts backend + agent
```

- Chat UI: **http://localhost:8100** · API docs: http://localhost:8100/docs
- Backend: http://localhost:8000

### Local (Python 3.10+)

```bash
cd restaurant-api && docker compose up -d --build && cd ..      # provided backend
cd restaurant-agent
python -m venv .venv && source .venv/Scripts/activate           # or .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                                            # set GROQ_API_KEY

make run          # uvicorn on :8100  (chat UI + REST + SSE)
make cli PHONE="+91-9876543210"
make check        # ruff + mypy --strict + pytest with coverage gate
```

`.env`

```
GROQ_API_KEY=<your key>        # the exercise's restaurant-api/.env ships a working one
MODEL=openai/gpt-oss-20b
RESTAURANT_API_URL=http://localhost:8000
```

### Interfaces

| | |
|---|---|
| **Browser** | `http://localhost:8100` - streams each tool call as a live status pill |
| **CLI** | `python -m agent.cli --phone "+91-9876543210" --verbose` |
| **REST** | `POST /sessions` · `POST /sessions/{id}/messages` (send `client_message_id` for idempotency) |
| **SSE** | `POST /sessions/{id}/messages/stream` - `tool_call` / `tool_result` / `notice` / `message` / `done` |
| **Ops** | `GET /health` (liveness) · `GET /ready` (DB + upstream) |

```bash
SID=$(curl -s -XPOST localhost:8100/sessions -H 'content-type: application/json' \
      -d '{"phone":"+91-9876543210"}' | python -c 'import sys,json;print(json.load(sys.stdin)["session_id"])')
curl -s -XPOST "localhost:8100/sessions/$SID/messages" -H 'content-type: application/json' \
     -d '{"content":"Book a table for 3 tonight around 8","verbose":true}'
```

---

## Architecture

Full write-up with layer + sequence diagrams: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.
Design rationale (ADR-style): **[docs/DECISIONS.md](docs/DECISIONS.md)**.

```
agent/
  config.py              Settings (env -> frozen dataclass), incl. loop budgets
  service.py             AgentService - composition root; one-turn orchestration
  agent.py               ReceptionAgent / TurnRunner - the streamed loop + budgets
  policy.py              deterministic guardrails (confirmation, booking gate)
  events.py / trace.py   TurnEvent stream · auditable TurnTrace / TurnResult
  conversation.py        Session identity + Conversation transcript
  llm/                   LLMClient protocol · GroqClient (httpx) · ScriptedLLM
  backend/               BackendClient protocol · RestaurantClient · BackendError
  tools/
    registry.py          @register decorator · schema gen · safe dispatch
    result.py            ToolResult envelope + ErrorCode enum
    catalog.py           EVERY tool - one file to add a capability
    context.py           ToolContext(backend, session, memory)
  memory/                CustomerMemory - durable prefs in the backend blob
  store/                 SessionStore (SQLite) - sessions + idempotency ledger
  observability/         JSON logging · PII redaction · request-id context
  prompts/system.md      versioned system prompt
  api.py / cli.py        interfaces
  web/index.html         zero-dependency streaming chat UI
```

Each layer depends only on the one below and never skips it. Swapping the model
provider, the interface, or the backend transport is a one-file change behind a
`Protocol`.

### The agent loop (`agent.py`)

Streams `TurnEvent`s so the **same loop drives the blocking API and the SSE
endpoint**. Each round: call the model with the full tool catalog; a plain reply
ends the turn; otherwise, for every tool call - check budgets, run the policy
layer, dispatch, append the typed result, emit events. Three independent caps,
each with its own `stopped_reason`:

| cap | guards against |
|---|---|
| `max_agent_steps` (8) | infinite think-loops |
| `max_tool_calls` (12) | tool-call spam |
| `max_mutations` (6) | runaway writes (bookings, orders) |

A provider-rejected tool call (`MalformedToolCall`) is fed back to the model as a
correction and retried, counted against the step budget.

### Tool layer - defensive by construction

`ToolRegistry.dispatch` **never raises**. Every outcome is a `ToolResult(ok,
message, data, error_code, http_status)`:

| failure | `error_code` |
|---|---|
| hallucinated tool name | `unknown_tool` |
| arguments not valid JSON | `bad_json` |
| arguments wrong shape (Pydantic) | `bad_arguments` |
| unparseable date/time | `bad_datetime` |
| acting on another customer's reservation | `ownership_denied` |
| backend 4xx / 5xx | `upstream_error` / `upstream_unavailable` |
| anything unexpected in a tool | `internal_error` |

**Adding a tool is one function + one args model** in `catalog.py`; the JSON
schema, validation, error shaping, and the mutation/destructive classification
all follow from `@registry.register`. `customer_id` is always taken from the
session; every tool that takes a `reservation_id`/`order_id` re-verifies
ownership against the backend before it acts.

### Policy layer - deterministic guardrails (`policy.py`)

Pure functions of `(tool call, conversation, memory)`, unit-tested with no LLM:

- **Confirmation** - `cancel_reservation` / `remove_order_item` are blocked until
  the customer explicitly asks or confirms. The block is returned to the model as
  a tool result, so it produces a natural "are you sure?" turn; the customer's
  "yes" clears the gate next turn.
- **Booking precondition** - `book_table` is blocked until seating is resolved
  (an explicit choice in the conversation, or a stored preference). New guests
  are asked once; returning guests sail through.

### Memory - durable vs. in-conversation

`CustomerMemory` persists in the backend's open `customers.preferences` blob
(schema we own: `seating`, `dietary[]`, `allergies[]`, `notes[]`) and is kept
separate from the per-session `Conversation`. Written only via
`remember_preference` (the prompt reserves it for *lasting* preferences).
Allergies are always folded into the order guard, `avoid_tags` or not.

### Persistence & idempotency (`store/`)

SQLite (stdlib, WAL). Sessions and full transcripts survive a restart. A
`(session_id, client_message_id)` ledger makes a retried `POST` return the
stored reply instead of re-running mutations; a reused id with a different body
is a `409`.

### Smart booking

`book_table` picks the smallest available table that fits, honouring stored
seating, falling back to any area if the preferred one is full (and saying so).
`change_reservation` encodes the move sequence so the model can't fumble it -
release the old booking, find a table, re-book; if the cancel is refused or
nothing fits, the original is restored untouched (no duplicate reservations).

### Observability

Every request and turn logs one structured JSON line with a request-id (context
var, echoed as `X-Request-ID`); a redaction pass scrubs emails, phone numbers
and tokens from messages and exception strings. Security headers (CSP, nosniff,
frame-deny) on every response.

---

## Tests

```bash
pytest            # 109 tests, ~0.9s, no network, no Docker
```

- **tool layer** - dispatch safety (unknown tool / bad JSON / bad args), ownership,
  smart assignment, order guards, `change_reservation` restore path
- **policy** - confirmation + booking gates, pure
- **agent loop** - end-to-end with `ScriptedLLM` + `FakeBackend`: booking→ordering
  flow, memory in the prompt, all three budgets, malformed-call recovery, SSE events
- **service** - persistence, idempotent replay + conflict, memory refresh, streaming
- **store** - roundtrip, ledger, purge, cross-instance persistence
- **api** - `TestClient`: health/ready, security headers, full HTTP conversation, SSE
- **clients** - `GroqClient` (httpx MockTransport: parsing, 429 retry, tool_use_failed)
  and `RestaurantClient` (request shaping, error mapping)
- **observability** - redaction, structured records

`ScriptedLLM` and `FakeBackend` (which implements the `BackendClient` protocol)
mean the whole agent is exercised without a real model or a running backend.

---

## Decisions (short form - full version in [docs/DECISIONS.md](docs/DECISIONS.md))

| choice | why |
|---|---|
| **hand-rolled loop**, not LangGraph/LangChain | the harness is the thing under review; a framework would hide the decision logic, budgets and recovery |
| **Groq `openai/gpt-oss-20b`** via raw `httpx` | key provided; native tool calling; provider is a one-file swap behind `LLMClient` |
| **synchronous core** | readability; the workload is a short sequential HTTP chain, not high-concurrency fan-out |
| **memory in the `preferences` blob** | the intended store; survives restarts; no second datastore |
| **SQLite** for sessions | zero-ops for a local build; trivial schema. Production -> managed Postgres |
| **policy in code, not the prompt** | a cancellation guard shouldn't be probabilistic |
| **`ToolResult` + `ErrorCode`** | typed outcomes are testable and stable; `message` doubles as a model-facing summary |
| **idempotency keys** | a reception agent mutates state; a dropped response + retry must not double-book |

## Assumptions

- Naive local time throughout, matching the backend. "tonight"/"tomorrow" resolve
  against the server clock; a loose time is snapped to the nearest valid 30-minute
  slot (12:00-22:30) and the backend still has the final say.
- With no explicit area and no stored preference, `book_table` is gated once, then
  the smallest fitting table wins.
- Memory is written only on an explicit lasting-preference statement.
- "That reservation" = the one created or last acted on this conversation; if
  there is none, the agent asks.
- One customer per session; phone/email identifies them up front and is not
  re-checked. **A real deployment needs actual authentication** - the exercise
  identifies but does not authenticate.

## Known limitations

- Groq's free tier is ~8k tokens/min and the full tool catalog is sent every
  turn; long conversations can rate-limit. `GroqClient` retries with backoff
  (honouring `retry-after`); sustained use needs a paid tier or a trimmed toolset.
- Conversation history is unbounded - no summarisation / truncation yet.
- SQLite is single-writer; run one agent process. Production -> a managed
  Postgres-compatible store.
- Dish-name matching is exact -> substring -> token-overlap; fine for a dozen
  items, not a fuzzy search engine.
- The SSE endpoint intentionally skips the idempotency ledger (streaming and
  replay don't compose); use the blocking endpoint where exactly-once matters.
