# tac-api: the platform API (FastAPI + in-process worker), SQLite + media on the /data volume.
# Renders never run here: TAC_RENDERER=fly-machine starts one throwaway Machine per job in tac-render.
# Build context = platform/ (see .dockerignore; data/ and .venv/ never ship). `fly deploy` from platform/.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.3 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

# deps first (cached layer), then the project itself; --frozen = exactly uv.lock, no resolution
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY themes.json ./
RUN uv sync --frozen --no-dev

ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 TAC_THEMES_FILE=/app/themes.json
EXPOSE 8790
CMD ["tac-platform"]
