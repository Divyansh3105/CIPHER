# CIPHER backend (Phase 8).
#
# Multi-stage, so the runtime image carries the installed packages but not
# the build toolchain. It matters more than it looks on a free tier: Render's
# free plan sleeps after inactivity and cold-starts from the image, so every
# unnecessary megabyte is added to every wake-up.
#
# Built from the repository root, not from this directory:
#     docker build -f services/backend/Dockerfile -t cipher-backend .
# The build context has to include the repo root because .env lives there
# (and is excluded by .dockerignore -- see the note on secrets below).

FROM python:3.13-slim AS builder

WORKDIR /build

# Dependencies before source: this layer is cached until requirements.txt
# actually changes, so an ordinary code edit does not reinstall everything.
COPY services/backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.13-slim AS runtime

# Never as root. A container that runs as root turns a container escape into
# a host compromise, and nothing here needs the privilege.
RUN useradd --create-home --uid 10001 cipher

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --chown=cipher:cipher services/backend/ /app/

USER cipher

# PYTHONUNBUFFERED so logs appear immediately rather than when a buffer
# fills -- on a platform that only shows you stdout, a buffered crash log is
# a crash with no explanation.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_PORT=8000

EXPOSE 8000

# Readiness, not liveness: the container is only healthy once it can reach
# the database, since every meaningful endpoint needs it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=4).status==200 else 1)"

# No --reload, and no shell form. Exec form means uvicorn is PID 1 and
# receives SIGTERM directly, so a deploy drains connections instead of
# having the process killed after a grace period.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
