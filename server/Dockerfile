# Quill server image for StartOS (linux/amd64 and linux/arm64).
# Runs as root on purpose: StartOS mounts /data and /models owned by root and the
# package performs no chown step.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    QUILL_DATA_DIR=/data \
    QUILL_MODELS_DIR=/models \
    QUILL_PORT=8080 \
    QUILL_WEB_DIR=/app/web

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so code changes do not reinstall them.
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Chromium for the Jitsi bot (--with-deps installs its system libraries).
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY quill_server ./quill_server
RUN pip install --no-deps .
COPY web ./web

EXPOSE 8080
CMD ["python", "-m", "quill_server"]
