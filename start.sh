#!/usr/bin/env bash
set -e
cd -- "$(dirname -- "$(readlink -f -- "$0")")"
if [ -x .venv/bin/python ]; then
    exec .venv/bin/python app.py "$@"
fi
exec python3 app.py "$@"
