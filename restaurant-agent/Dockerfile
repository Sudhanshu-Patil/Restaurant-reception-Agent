FROM python:3.12-slim

WORKDIR /app

# deps first for layer caching
COPY pyproject.toml README.md ./
COPY agent ./agent
COPY scripts ./scripts
RUN pip install --no-cache-dir .

ENV AGENT_DB_PATH=/data/agent.db \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8100

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8100/health').status==200 else 1)"

CMD ["uvicorn", "agent.api:app", "--host", "0.0.0.0", "--port", "8100"]
