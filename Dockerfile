# The API and the worker in one image (plan.md §1 Deploy: Fly.io or Railway, Supabase DB).
#   API:    the default command, on port 8080
#   worker: vitrina-worker
# Settings come from the environment, by the names in .env.example. With none set it starts
# in replay mode on the local WhatsApp mock: no keys, no database (AS-7.3).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY . .
# Editable installs keep the repository layout, which settings use for fixtures and cassettes.
RUN uv sync --locked --no-dev \
    && useradd --create-home --uid 10001 vitrina \
    && mkdir -p tmp \
    && chown -R vitrina /app
USER vitrina

EXPOSE 8080
CMD ["uvicorn", "vitrina_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
