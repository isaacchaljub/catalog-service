FROM python:3.12-slim

# uv resolves from the committed lockfile, so the image gets byte-identical
# dependencies to the machine the service was tested on.
COPY --from=ghcr.io/astral-sh/uv:0.9.27 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PORT=8080

WORKDIR /srv

# Dependencies first: this layer is cached until the lockfile actually changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app/ ./app/
COPY data/ ./data/

ENV PATH="/srv/.venv/bin:$PATH"

# Cloud Run injects $PORT and routes to it. Binding 127.0.0.1 would leave the
# container unreachable, so bind 0.0.0.0 explicitly.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
