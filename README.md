# Restaurant Reception Agent

A conversational AI agent that takes reservations, manages orders, answers menu
questions, and remembers returning customers — built **on top of** the provided
`restaurant-api` backend, which it treats as a black box reached only over HTTP.

---

## Quick start

```bash
# 1. start the provided backend (from the sibling folder)
cd ../restaurant-api
docker compose up -d --build
curl http://localhost:8000/health          # {"status":"ok"}

# 2. set up this agent
cd ../restaurant-agent
python -m venv .venv && source .venv/Scripts/activate   # or .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                        # then fill in GROQ_API_KEY
```

`.env`:

```
GROQ_API_KEY=<your key>          # the provided restaurant-api/.env has a working one
MODEL=openai/gpt-oss-20b
RESTAURANT_API_URL=http://localhost:8000
```

### Talk to it (CLI)

```bash
python -m agent.cli --phone "+91-9876543210"       # returning customer (Priya)
python -m agent.cli --email new@example.com --name "New Guest"
python -m agent.cli --phone "+91-9876543210" --verbose   # show the reasoning trace
```

### Talk to it (browser)

```bash
uvicorn agent.api:app --port 8100
```

Open **http://localhost:8100** — a single-page chat UI (no build step, served by the
API itself). Identify by phone/email, then chat; tick *show reasoning trace* to see
which tools ran each turn. Swagger docs are at `/docs`.

### Talk to it (REST)

```bash
uvicorn agent.api:app --port 8100

curl -s -X POST localhost:8100/sessions \
  -H 'content-type: application/json' \
  -d '{"phone": "+91-9876543210"}'
# -> {"session_id": "...", "customer": {...}, "memory": "..."}

curl -s -X POST localhost:8100/sessions/<id>/messages \
  -H 'content-type: application/json' \
  -d '{"content": "Book a table for 3 tonight around 8", "verbose": true}'
```

### Run the tests

```bash
pytest            # 24 tests, no network, no Docker required
```

---

## Architecture

```
agent/
  config.py              Settings (env -> immutable dataclass)
  llm/
    base.py              LLMClient protocol, LLMMessage/ToolCall, error types
    groq.py              httpx client for Groq's OpenAI-compatible endpoint
    stub.py              ScriptedLLM — deterministic test double
  backend/
    client.py            RestaurantClient — one typed method per API endpoint
    errors.py            BackendError(status_code, detail)
  tools/
    registry.py          Tool, @register decorator, schema gen, safe dispatch()
    context.py           ToolContext(backend, session, memory)
    catalog.py           EVERY tool lives here — one file to add a capability
  memory/
    customer_memory.py   durable preferences (via API blob) + derived history
  conversation.py        Session identity + Conversation transcript
  agent.py               ReceptionAgent.run_turn() — the loop
  trace.py               TurnTrace — auditable per-turn record
  cli.py / api.py        thin interfaces
  web/index.html         zero-dependency browser chat UI served by api.py
```

**Layering.** Each layer depends only on the one below and never skips:
`cli/api → agent → {registry → catalog → backend}, memory, llm`. The agent loop
knows nothing about HTTP or Groq; the tool layer knows nothing about the LLM wire
format; the backend client knows nothing about tools. Swapping the LLM provider or
the interface is a one-file change.

### The agent loop (`agent.py`)

1. Build the system prompt: role + business rules + **memory block** (stored
   preferences, allergies, order history, upcoming reservation) + current time.
2. Replay `[system] + conversation transcript`.
3. Up to `max_agent_steps` (8) iterations:
   - call the LLM with the full tool catalog;
   - no tool calls → return the text, done;
   - otherwise dispatch each tool call, append the JSON result as a `tool`
     message, record it in the `TurnTrace`, loop.
4. Runaway loop → bail out with a safe message.

### Tool layer — defensive by construction

`ToolRegistry.dispatch()` is the trust boundary between the model and the backend.
It **never raises**; every failure becomes a JSON `{"error": ...}` the model reads
and reacts to:

| LLM misbehaviour | Result |
|---|---|
| Hallucinated tool name | `{"error": "Unknown tool 'x'. Available: ..."}` |
| Arguments aren't valid JSON / partial JSON | `{"error": "... not valid JSON ..."}` |
| Arguments valid JSON but wrong shape | Pydantic `ValidationError` → readable field list |
| Backend rejects it (409/422/…) | `{"error": <detail>, "status_code": <code>}` |
| Any other exception in the tool | `{"error": "<Type>: <msg>"}` |
| Provider (Groq) rejects the model's own tool call | `MalformedToolCall` → loop feeds the reason back and lets the model retry |

**Adding a tool is a one-file change:** write a Pydantic args model + a function
decorated with `@registry.register(name, description, ArgsModel)` in
`tools/catalog.py`. The JSON schema sent to the LLM is generated from the model;
dispatch, validation and error-shaping are automatic.

`customer_id` is always taken from the session, never a tool argument, so the agent
cannot act on behalf of another customer.

### Memory — durable vs. in-conversation

- **`Conversation`** (`conversation.py`): the message list for one chat. Ephemeral.
- **`CustomerMemory`** (`memory/customer_memory.py`): cross-session knowledge,
  persisted in the backend's open `customers.preferences` JSON blob. Schema we own:

  ```json
  { "seating": "outdoor",
    "dietary": ["vegetarian"],
    "allergies": ["nuts"],
    "notes": ["celebrates anniversary in March"] }
  ```

  Loaded once at session start (preferences + reservations + orders, joined with
  the menu for dish names). Written only via the `remember_preference` tool, which
  the prompt tells the model to use for *lasting* preferences, not one-off
  requests. Allergies are always folded into the order guard, whether or not the
  model passes `avoid_tags`.

### Smart table assignment

`book_table` checks availability, keeps tables that fit the party, honours the
customer's stored `seating` preference when they didn't specify one, falls back to
any area if the preferred one is full (and says so), then books the **smallest**
fitting table to maximise utilisation.

### Reasoning trace

Every turn produces a `TurnTrace` (LLM rounds, each tool call + arguments + result
+ ok/error, final reply). `--verbose` on the CLI and `"verbose": true` on the REST
endpoint print it, so a manager can audit a conversation after the fact.

---

## Decisions

| Choice | What | Why |
|---|---|---|
| **LLM** | Groq, `openai/gpt-oss-20b` | Key already provided; fast; native OpenAI-style tool calling means no client-side tool-call parsing. Provider is swappable behind `LLMClient`. |
| **LLM transport** | raw `httpx`, no SDK | One endpoint, one call shape. Avoids pulling the OpenAI/Groq SDK for ~30 lines of HTTP; keeps the dependency surface small. |
| **Tool schemas** | Pydantic → `model_json_schema()` | Single source of truth: the same model validates the model's arguments and generates its documentation. |
| **Memory storage** | the backend's `preferences` blob | The exercise's intended store; survives restarts; no extra datastore to run. Derived history is recomputed on load rather than cached. |
| **Interface** | CLI (primary) + REST + a static browser UI | CLI is the fastest demo; REST shows the interface layer is thin; `web/index.html` is a dependency-free chat page (vanilla JS, served by the API) for a nicer walkthrough. |
| **Conversation state** | in-memory | Single-process demo. A real deployment would back `Conversation` with Redis/DB; nothing else changes. |
| **Datetime parsing** | `get_current_datetime` tool + `dateutil` fallback | The model is told to resolve relative times itself; loose phrases ("tomorrow around 8") are still snapped to a valid 30-min slot defensively. The backend has the final say. |

## Assumptions

- **Naive local time.** All datetimes are the restaurant's wall-clock time, matching
  the backend. "tonight/tomorrow" resolve against the server clock.
- **Slot snapping.** A requested time is snapped to the nearest valid 30-minute
  slot (12:00–22:30). If that still violates a rule, the backend error is relayed
  to the customer.
- **Table choice.** With no explicit area and no stored preference, any location is
  fair game; the smallest fitting table wins.
- **Memory writes** happen only when the customer states a durable preference or
  allergy — not for one-off requests ("outside just this once").
- **"That reservation"** refers to the one created or last acted on in the current
  conversation (`Session.active_reservation_id`); if there is none, the agent asks.
- **New customers** are created on first contact from the CLI/REST identity step;
  a name is requested if the lookup misses.
- **One customer per session.** Phone/email identifies the customer up front and is
  not re-checked mid-conversation.

## Known limitations

- Groq's free tier is ~8k tokens/min; the full tool catalog is sent every turn, so
  a long conversation can hit a rate limit. The client retries with backoff
  (honouring Groq's `retry-after`), but sustained use needs a paid tier or a
  trimmed tool set.
- Conversation history is unbounded — no summarisation/truncation yet.
- Dish-name matching is exact → substring → token-overlap; good for 12 items, not a
  fuzzy search engine.
