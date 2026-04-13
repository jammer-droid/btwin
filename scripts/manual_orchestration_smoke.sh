#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "$REPO_ROOT"

TEST_HOME="${TEST_HOME:-$(mktemp -d)}"
PROJECT_DIR="${PROJECT_DIR:-$(mktemp -d)}"
KEEP_TEST_HOME="${KEEP_TEST_HOME:-0}"

cleanup() {
  if [[ "$KEEP_TEST_HOME" != "1" ]]; then
    rm -rf "$TEST_HOME"
    rm -rf "$PROJECT_DIR"
  fi
}
trap cleanup EXIT

mkdir -p "$TEST_HOME/.btwin"
cat > "$TEST_HOME/.btwin/config.yaml" <<'YAML'
runtime:
  mode: standalone
YAML

run_btwin() {
  (
    cd "$PROJECT_DIR"
    HOME="$TEST_HOME" uv run --project "$REPO_ROOT" btwin "$@"
  )
}

run_python() {
  HOME="$TEST_HOME" uv run --project "$REPO_ROOT" python "$@"
}

echo "Using isolated HOME: $TEST_HOME"
echo "Using isolated project dir: $PROJECT_DIR"
echo "Registering agent..."
run_btwin agent create alice --provider codex --role implementer --model gpt-5 >/dev/null

echo "Creating thread..."
THREAD_ID="$(
  run_btwin thread create \
    --topic "CLI smoke test" \
    --protocol debate \
    --participant alice \
    --participant bob \
    --json | run_python -c 'import json,sys; print(json.load(sys.stdin)["thread_id"])'
)"

echo "Sending direct message to alice..."
run_btwin thread send-message \
  --thread "$THREAD_ID" \
  --from bob \
  --content "Please review this thread." \
  --tldr "review request" \
  --delivery-mode direct \
  --target alice >/dev/null

echo "Checking agent inbox..."
AGENT_INBOX_JSON="$(run_btwin agent inbox alice --json)"
THREAD_INBOX_JSON="$(run_btwin thread inbox --thread "$THREAD_ID" --agent alice --json)"

MESSAGE_ID="$(
  printf '%s' "$THREAD_INBOX_JSON" \
    | run_python -c 'import json,sys; print(json.load(sys.stdin)["messages"][0]["message_id"])'
)"

echo "Acknowledging message..."
run_btwin thread ack-message --thread "$THREAD_ID" --message "$MESSAGE_ID" --agent alice >/dev/null

echo "Closing thread..."
CLOSE_JSON="$(
  run_btwin thread close \
    --thread "$THREAD_ID" \
    --summary "smoke test completed" \
    --decision "keep going" \
    --json
)"

COMPLETED_JSON="$(run_btwin thread list --status completed --json)"

run_python - <<'PY' "$THREAD_ID" "$AGENT_INBOX_JSON" "$THREAD_INBOX_JSON" "$CLOSE_JSON" "$COMPLETED_JSON"
import json
import sys

thread_id = sys.argv[1]
agent_inbox = json.loads(sys.argv[2])
thread_inbox = json.loads(sys.argv[3])
close_payload = json.loads(sys.argv[4])
completed = json.loads(sys.argv[5])

print("")
print("Smoke test passed")
print(f"- thread_id: {thread_id}")
print(f"- agent queue_count: {agent_inbox['queue_count']}")
print(f"- agent pending_message_count: {agent_inbox['pending_message_count']}")
print(f"- initial thread inbox count: {thread_inbox['pending_count']}")
print(f"- close status: {close_payload['status']}")
print(f"- completed thread ids: {[item['thread_id'] for item in completed]}")
if 'result_record_id' in close_payload:
    print(f"- result_record_id: {close_payload['result_record_id']}")
PY
