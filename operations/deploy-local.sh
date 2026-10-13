#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
# Explicit local operator action. This script never logs in to a remote server or edits a proxy.
docker compose config --quiet
docker info >/dev/null
docker compose build --pull
docker compose up -d --no-deps agentops
docker compose ps
