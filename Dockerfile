# Headless web build — domain + engine + report + FastAPI. No Qt.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# git + a compiler for redeal's bundled DDS solver.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-web.txt .
RUN pip install -r requirements-web.txt

COPY bridge_mc ./bridge_mc
COPY web ./web

ENV PORT=8080 MAX_DEALS=8000 RUN_TIMEOUT=120
EXPOSE 8080
CMD ["sh", "-c", "uvicorn web.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
