#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.npm-global/bin:$PATH"
exec node "$HOME/.npm-global/lib/node_modules/openclaw/openclaw.mjs" "$@"
