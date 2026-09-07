#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

if [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
    PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
else
    echo "Error: Python 3 was not found. Create .venv or add python3 to PATH." >&2
    exit 1
fi

if ! NODE_BIN="$(command -v node 2>/dev/null)"; then
    echo "Error: Node.js was not found. Install Node.js and add node to PATH." >&2
    exit 1
fi

cd "$PROJECT_ROOT"

echo "Using Python: $PYTHON_BIN"
echo "Using Node.js: $NODE_BIN"

echo "[1/4] Python compile"
"$PYTHON_BIN" -m py_compile main.py

echo "[2/4] JavaScript syntax"
js_count=0
while IFS= read -r -d '' js_file; do
    case "$js_file" in
        */vendor/*|*/third-party/*|*/third_party/*|*.min.js)
            continue
            ;;
    esac
    "$NODE_BIN" --check "$js_file"
    js_count=$((js_count + 1))
done < <(find "$PROJECT_ROOT/static/js" -type f -name '*.js' -print0)

if [[ "$js_count" -eq 0 ]]; then
    echo "Error: No project JavaScript files were found under static/js." >&2
    exit 1
fi

echo "[3/4] Unit tests"
"$PYTHON_BIN" -m unittest discover -s tests

echo "[4/4] Git diff check"
git diff --check
git diff --cached --check

echo "All checks passed."
