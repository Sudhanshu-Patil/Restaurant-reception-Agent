# Restaurant Reception Agent — submission

| Folder | What |
|---|---|
| [`restaurant-agent/`](restaurant-agent/) | **My work** — a hand-rolled AI reception agent (multi-turn booking, ordering, menu Q&A, customer memory). Full docs: **[restaurant-agent/README.md](restaurant-agent/README.md)**, [ARCHITECTURE](restaurant-agent/docs/ARCHITECTURE.md), [DECISIONS](restaurant-agent/docs/DECISIONS.md). |
| [`restaurant-api/`](restaurant-api/) | The **provided** backend (FastAPI + Postgres, Dockerised). Unmodified — byte-for-byte the original zip. Treated as a black box over HTTP. |

## Run everything (one command)

```bash
export GROQ_API_KEY=...            # or put it in a .env file next to this README
docker compose up -d --build
```

- **Agent** — chat UI + REST + SSE: http://localhost:8100 (API docs at `/docs`)
- **Backend** — http://localhost:8000

The agent auto-migrates its SQLite store; the backend seeds itself. Both come up healthy.

## Run the agent locally (Python 3.10+)

```bash
cd restaurant-api && docker compose up -d --build && cd ..     # provided backend only
cd restaurant-agent
python -m venv .venv && source .venv/Scripts/activate          # or .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                                           # set GROQ_API_KEY

make run                              # uvicorn :8100
make cli PHONE="+91-9876543210"       # interactive CLI
make check                            # ruff + mypy --strict + pytest (coverage gate)
```

## At a glance

- **Harness, not framework** — a small streamed tool-calling loop behind a
  provider-agnostic `LLMClient`; decorator tool registry; deterministic
  confirmation / booking guardrails in code; typed `ToolResult` + `ErrorCode`.
- **Production concerns** — SQLite session persistence, message idempotency keys,
  three independent loop budgets, ownership checks, structured JSON logging with
  PII redaction, security headers, SSE streaming, `/health` + `/ready`.
- **Tested** — 109 tests (~1s, no network), `mypy --strict` and `ruff` clean,
  ~90% coverage gate, CI on Python 3.10 & 3.12.

## Requirements

- Docker + Docker Compose
- Python 3.10+ (for the local flow)
- A Groq API key
