FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SERVICE_HOST=0.0.0.0 \
    SERVICE_PORT=8080

WORKDIR /app

RUN useradd --create-home --shell /bin/bash appuser

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser main.py ./main.py
COPY --chown=appuser:appuser ragflow_service ./ragflow_service
COPY --chown=appuser:appuser frontend ./frontend
COPY --chown=appuser:appuser .env.example ./.env.example
COPY --chown=appuser:appuser .env.docker.example ./.env.docker.example

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"SERVICE_PORT\", \"8080\")}/docs', timeout=5)"

CMD ["python", "main.py", "serve"]
