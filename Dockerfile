FROM python:3.11-slim

# Playwright deps for stealth browser (Catawiki / future sources).
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget gnupg ca-certificates fonts-liberation libnss3 libxss1 \
        libasound2 libatk-bridge2.0-0 libgtk-3-0 libgbm1 libxshmfence1 \
        libdrm2 libxcomposite1 libxdamage1 libxrandr2 libxkbcommon0 \
        libpango-1.0-0 libcairo2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY config ./config

RUN pip install --no-cache-dir . \
    && patchright install --with-deps chromium

RUN mkdir -p /app/data
VOLUME ["/app/data", "/app/config"]

ENV PYTHONUNBUFFERED=1 \
    DB_PATH=/app/data/scanner.db \
    PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright

EXPOSE 8765

CMD ["treasure-scanner"]
