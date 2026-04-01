#!/bin/bash
# Stop gate (COMMAND, BLOCKING)
# Rule: Tests must pass before Claude can stop.

INPUT=$(cat)

# Prevent infinite loop: if stop hook already ran, allow stop
STOP_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false')
if [ "$STOP_ACTIVE" = "true" ]; then
  exit 0
fi

PROJECT_DIR="$CLAUDE_PROJECT_DIR"

RESULT=$(cd "$PROJECT_DIR" && .venv/bin/python -m pytest tests/ -q -m 'not live' 2>&1)
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
  echo "Tests are failing. Fix these before stopping:" >&2
  echo "" >&2
  echo "$RESULT" | tail -20 >&2
  exit 2
fi

exit 0
