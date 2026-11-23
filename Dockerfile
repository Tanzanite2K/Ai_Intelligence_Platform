FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements-dev.txt
# Chromium for the optional Playwright renderer (JS-heavy pages that permit automation)
ARG INSTALL_BROWSER=false
RUN if [ "$INSTALL_BROWSER" = "true" ]; then playwright install --with-deps chromium; fi
COPY . .
RUN useradd -m app && mkdir -p data/exports && chown -R app /app
USER app
EXPOSE 8000
HEALTHCHECK CMD curl -fsS http://localhost:8000/health || exit 1
CMD ["python", "-m", "src.main", "serve"]
