FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY config ./config

RUN pip install --no-cache-dir .

RUN mkdir -p /app/data
VOLUME ["/app/data", "/app/config"]

ENV PYTHONUNBUFFERED=1 \
    DB_PATH=/app/data/scanner.db

CMD ["treasure-scanner"]
