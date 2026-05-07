# syntax=docker/dockerfile:1.7
# ─────────────────── builder ───────────────────
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install build deps + the project into an isolated venv we'll copy over.
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .


# ─────────────────── runtime ───────────────────
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/venv/bin:$PATH" \
    BRIDGE_OAUTH_HOST=0.0.0.0

# Non-root user for runtime; keep numeric uid stable for volume permissions.
RUN groupadd --system --gid 1100 bridge \
 && useradd --system --uid 1100 --gid bridge \
            --home-dir /app --no-create-home --shell /usr/sbin/nologin bridge \
 && mkdir -p /app /data /secrets \
 && chown bridge:bridge /app /data

COPY --from=builder /venv /venv
COPY --chown=bridge:bridge config.example.yaml /app/config.example.yaml

# Volumes for persistent state and OAuth client secrets (mount at runtime).
VOLUME ["/data", "/secrets"]

USER bridge
WORKDIR /app

# Default command: run the sync loop. Overridden for `bootstrap` etc.
ENTRYPOINT ["bridge", "-c", "/app/config.yaml"]
CMD ["run"]
