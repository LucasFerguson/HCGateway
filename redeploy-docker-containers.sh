#!/usr/bin/env bash
#
# Rebuilds every locally-built HCGateway image from current source and
# recreates the containers that use it. Exists because `docker compose
# restart` reuses the already-running container's image and environment -
# it does NOT pick up a rebuilt image or an edited .env file. This project's
# own history has hit that mistake more than once: a code or .env change
# looked live because the container was healthy, but was actually still
# running stale code. `docker compose up -d --build` is the sequence that
# actually redeploys; this script exists so that sequence is one command
# instead of something to remember correctly under time pressure.
#
# Usage:
#   ./redeploy-docker-containers.sh              # rebuild + redeploy everything
#   ./redeploy-docker-containers.sh api graphql-api   # only these services
#
# What it does NOT do: touch ./db (the bind-mounted MongoDB data directory),
# run `docker compose down` (which would drop the network and all containers
# briefly - fine for most changes, but unnecessary for a plain rebuild), or
# delete any data. This is a rebuild-and-recreate, not a reset.

set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "${script_directory}"

services=("$@")

echo "==> Building: ${services[*]:-all services}"
docker compose build "${services[@]}"

echo "==> Recreating containers with the freshly built images"
docker compose up -d "${services[@]}"

echo "==> Waiting for health checks"
sleep 8

echo "==> Current status"
docker compose ps

echo ""
echo "==> Reminder: an .env edit for a service (e.g. graphql-api/.env) also"
echo "    requires this same 'up -d' recreate step - a plain 'restart' will"
echo "    NOT pick it up, only a fresh container will."
