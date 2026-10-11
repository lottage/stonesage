# StoneSage image: the agent platform and its web cockpit (no apps). Hearth adds itself on top: see hearth.Dockerfile.
#
# Shipped as `Dockerfile` in the stonesage repository (docker/entrypoint.py is the first-start helper):
#     docker build -t ghcr.io/lottage/stonesage:latest .
# Lodestone is not on PyPI: it is installed from its repository. Pin or point elsewhere with
#     --build-arg LODESTONE_PIP="lodestone @ git+https://github.com/lottage/lodestone@v0.1.0"
# Runs as an unprivileged user. State lives in two volumes: /config (config.json, written on the first start from the
# environment, see docker/entrypoint.py) and /data (souls, memories, traces). No secrets are baked in.
FROM python:3.12-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
ARG LODESTONE_PIP="lodestone @ git+https://github.com/lottage/lodestone@main"
COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt "${LODESTONE_PIP}"

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH=/opt/venv/bin:$PATH \
    STONESAGE_CONFIG_FILE=/config/config.json STONESAGE_DATA_DIR=/data
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin stonesage \
 && mkdir -p /config /data /app/StoneSage/uploads /app/StoneSage/vault_backup \
 && chown -R stonesage:stonesage /config /data /app/StoneSage
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY --chown=stonesage:stonesage StoneSage/backend/stonesage /app/StoneSage/backend/stonesage
COPY --chown=stonesage:stonesage StoneSage/backend/server.py /app/StoneSage/backend/server.py
COPY --chown=stonesage:stonesage StoneSage/frontend /app/StoneSage/frontend
COPY --chown=stonesage:stonesage harness /app/harness
COPY docker/entrypoint.py /usr/local/bin/stonesage-entrypoint.py
USER stonesage

VOLUME ["/config", "/data"]
EXPOSE 8888
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8888/api/health', timeout=3)"]
ENTRYPOINT ["python", "/usr/local/bin/stonesage-entrypoint.py"]
