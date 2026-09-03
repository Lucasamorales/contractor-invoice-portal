# Cloud Run image (R-ENV-05).
#
# Two stages: dependencies resolve once, and the runtime image carries no build
# tooling. The runtime needs more than Python — WeasyPrint renders through
# Pango and Cairo, and the period rules need a timezone database.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, so a code change does not re-resolve them.
# --frozen fails rather than resolving: uv.lock is committed (R-SEC-12) and the
# image must contain what CI tested.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY app ./app
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.12-slim-bookworm AS runtime

# libpango / libcairo / libgdk-pixbuf: WeasyPrint's rendering (R-PDF-01).
# fonts-crosextra-carlito: metric-compatible with the Calibri that Robin's
#   template is set in, so the PDF matches without shipping a licensed font.
# tzdata: Europe/London, which the period window is evaluated in (R-PER-01).
# ca-certificates: sslmode=verify-full needs a trust store (R-SEC-07).
RUN apt-get update && apt-get install --no-install-recommends -y \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libffi8 \
        fonts-crosextra-carlito \
        tzdata \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Not root. Cloud Run does not require it, which is not a reason to run as root.
RUN useradd --create-home --uid 10001 portal
WORKDIR /app

COPY --from=build --chown=portal:portal /app/.venv /app/.venv
COPY --from=build --chown=portal:portal /app/app /app/app
COPY --from=build --chown=portal:portal /app/alembic.ini /app/alembic.ini

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC

USER portal

# Cloud Run sets PORT. One worker: sixteen people, and a second worker would
# double the database connections for no benefit.
ENV PORT=8080
EXPOSE 8080

# No shell form: exec form means uvicorn is PID 1 and receives SIGTERM, so
# Cloud Run's shutdown is graceful rather than a kill.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
