# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir "uv>=0.8,<0.9"

WORKDIR /app

# Dependencies first for better layer caching (postgres extra so DATABASE_URL can switch).
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra postgres --no-install-project

COPY src ./src
COPY prompts ./prompts
COPY evals ./evals
COPY knowledge ./knowledge
RUN uv sync --frozen --no-dev --extra postgres

ENV PATH="/app/.venv/bin:$PATH" \
    DATA_DIR=/data \
    KNOWLEDGE_DIR=/app/knowledge \
    PROMPTS_DIR=/app/prompts \
    EMBEDDING_CACHE_DIR=/opt/models

# Bake the embedding model into the image so containers start without internet access.
ARG PRELOAD_EMBEDDINGS=1
ARG EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
RUN if [ "$PRELOAD_EMBEDDINGS" = "1" ]; then \
      python -c "from fastembed import TextEmbedding; TextEmbedding('${EMBEDDING_MODEL}', cache_dir='/opt/models')"; \
    fi

RUN useradd --create-home --uid 10001 ben \
    && mkdir -p /data /opt/models \
    && chown -R ben /data /opt/models
USER ben

VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1

CMD ["ben", "serve", "--host", "0.0.0.0", "--port", "8000"]
