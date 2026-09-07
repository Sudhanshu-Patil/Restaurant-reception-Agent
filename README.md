# Restaurant Reception Agent — submission

Two projects:

| Folder | What |
|---|---|
| [`restaurant-api/`](restaurant-api/) | The **provided** backend (FastAPI + Postgres, Dockerised). Unmodified — byte-for-byte the original zip. |
| [`restaurant-agent/`](restaurant-agent/) | **My work** — the conversational AI agent built on top of it. See [`restaurant-agent/README.md`](restaurant-agent/README.md) for architecture, decisions and assumptions. |

## Run it (same as the development setup)

### 1. Start the backend

```bash
cd restaurant-api
docker compose up -d --build
curl http://localhost:8000/health          # {"status":"ok"}
```

### 2. Set up the agent

```bash
cd ../restaurant-agent
python -m venv .venv
source .venv/Scripts/activate               # Windows: .venv\Scripts\activate  |  macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# then edit .env and set GROQ_API_KEY (a working key ships in the exercise's
# original restaurant-api/.env; any Groq key works — model is openai/gpt-oss-20b)
```

### 3. Talk to it

```bash
# CLI
python -m agent.cli --phone "+91-9876543210" --verbose

# or browser UI + REST
uvicorn agent.api:app --port 8100     # then open http://localhost:8100
```

### Tests (no backend, no LLM, no network)

```bash
cd restaurant-agent
pytest            # 26 tests
```

## Requirements

- Docker + Docker Compose
- Python 3.10+
- A Groq API key
