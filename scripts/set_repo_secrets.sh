#!/usr/bin/env bash
# Copy NEBIUS_API_KEY and TAVILY_API_KEY from .env into this repository's GitHub Actions secrets, so the daily
# live-data job (.github/workflows/live.yml) also runs one real incident. Values go straight from .env to
# `gh secret set` on stdin; nothing is printed. Run it yourself:  bash scripts/set_repo_secrets.sh
set -euo pipefail
cd "$(dirname "$0")/.."
for key in NEBIUS_API_KEY TAVILY_API_KEY; do
  value=$(grep -E "^${key}=" .env | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//')
  if [ -z "$value" ]; then echo "missing $key in .env" >&2; exit 1; fi
  printf '%s' "$value" | gh secret set "$key"
  echo "set $key"
done
echo "Trigger a run now with: gh workflow run live.yml"
