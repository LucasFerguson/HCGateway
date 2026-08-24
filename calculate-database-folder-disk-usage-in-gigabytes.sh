#!/usr/bin/env bash

set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
database_directory="${script_directory}/db"

if [[ ! -d "${database_directory}" ]]; then
  echo "Database folder not found: ${database_directory}" >&2
  exit 1
fi

if database_bytes="$(du --summarize --block-size=1 -- "${database_directory}" 2>/dev/null | awk '{print $1}')"; then
  measurement_source="host filesystem"
elif command -v docker >/dev/null 2>&1 && docker compose --project-directory "${script_directory}" ps --quiet db >/dev/null 2>&1; then
  database_bytes="$(
    docker compose --project-directory "${script_directory}" exec --no-TTY --user root db \
      du --summarize --block-size=1 -- /data/db | awk '{print $1}'
  )"
  measurement_source="running database container"
else
  echo "Could not read ${database_directory}. Run with permission to read the database files or access the Docker daemon." >&2
  exit 1
fi

awk -v bytes="${database_bytes}" -v path="${database_directory}" -v source="${measurement_source}" 'BEGIN {
  printf "Database folder: %s\n", path
  printf "Measured through: %s\n", source
  printf "Size: %.3f GB (decimal)\n", bytes / 1000000000
  printf "Size: %.3f GiB (binary)\n", bytes / 1073741824
  printf "Bytes: %.0f\n", bytes
}'
