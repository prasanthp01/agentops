# Official Python 3.12.15 / supported Alpine 3.24 index, resolved 2026-10-13.
FROM python:3.12.15-alpine3.24@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 AGENTOPS_DB=/data/agentops.db
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN apk upgrade --no-cache && \
    pip install --only-binary=:all: --require-hashes -r requirements.txt && \
    python -m pip uninstall -y pip && \
    rm -rf /usr/local/lib/python3.12/ensurepip && \
    addgroup -S -g 10001 agentops && adduser -S -D -H -u 10001 -G agentops agentops && \
    mkdir /data && chown agentops:agentops /data && chmod 0700 /data
COPY --chown=10001:10001 agentops /app/agentops
COPY --chmod=0555 operations/entrypoint.sh /usr/local/bin/agentops-entrypoint
USER 10001:10001
EXPOSE 8785
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8785/healthz', timeout=3)"
ENTRYPOINT ["agentops-entrypoint"]
CMD ["uvicorn", "agentops.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8785", "--no-access-log", "--no-proxy-headers"]
