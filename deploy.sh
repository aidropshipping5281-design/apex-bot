#!/usr/bin/env bash
# One-command deploy on any Docker host (VPS). Run from the apex_bot folder.
set -e
[ -f .env ] || { cp .env.example .env; echo "Created .env — edit it, then re-run."; exit 1; }
docker compose up -d --build
echo "Apex is running. Logs:  docker compose logs -f apex"
