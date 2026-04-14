---
name: bt:scenario-smoke
description: Use when a btwin CLI feature or helper workflow changed and needs real user-scenario validation through CLI commands before being called complete
---

# Smoke A B-TWIN User Scenario

## Overview

Use this skill after a meaningful `btwin` CLI change when tests pass but you still need proof that a real helper-first workflow works from the CLI surface.

The goal is not “one more unit test.” The goal is to run a short user scenario end to end and verify the JSON outputs that a real operator or agent would see.

## When to Use

Use `bt:scenario-smoke` when:

- a helper-first CLI command was added or changed
- a runtime/helper flow was hardened and needs CLI-level confirmation
- attached/shared API behavior matters, not just standalone storage behavior
- the user asks for “real scenario”, “user flow”, “smoke”, or “end-to-end-ish” validation

Do not use this as a replacement for normal tests. Run the repo test suite first.

## Default Environment

Prefer the isolated attached environment so the smoke does not touch the primary `~/.btwin`.

Default bootstrap:

```bash
scripts/bootstrap_isolated_attached_env.sh start --root "$TMP_ROOT" --project-root "$TMP_PROJECT" --project btwin-scenario --port "$PORT"
source "$TMP_ROOT/env.sh"
```

Run all `btwin` commands from that sourced shell so `BTWIN_CONFIG_PATH`, `BTWIN_DATA_DIR`, and `BTWIN_API_URL` stay aligned.

## Scenario Pattern

Choose the smallest scenario that exercises the changed surface, but prefer real command sequences over one-off probes.

Common sequence:

1. create agents if the flow needs participants
2. create a thread with a real protocol
3. send at least one message
4. inspect `thread inbox` or `agent inbox`
5. if runtime helpers changed, run `runtime bind/current/clear`
6. if protocol helpers changed, run `protocol next` and `protocol apply-next`
7. verify final thread/runtime state with JSON output

## Good Scenario Families

- Thread lifecycle: `thread create -> show/list -> close`
- Inbox flow: `thread send-message -> thread inbox -> agent inbox -> ack-message`
- Runtime helper flow: `runtime bind -> runtime current -> protocol next/apply-next -> runtime clear`

If attached direct delivery is not the behavior under test, prefer `--delivery-mode broadcast`.
In attached mode, direct delivery can require the target agent to already be active in that thread runtime.

## Verification Rules

- Prefer `--json` and assert on returned fields, not just exit code
- Verify the command that reflects the final user-visible state, not only intermediate commands
- Record any product constraint separately from real failures
- Report the exact commands used and the scenario outcome

## Result Format

After the smoke, report:

- scenario name in one line
- commands exercised
- key JSON facts verified
- any known constraints discovered

Example:

```text
Scenario smoke passed: attached review handoff flow
- Commands: agent create, thread create, thread send-message, agent inbox, runtime bind/current, protocol next/apply-next, runtime clear
- Verified: pending_message_count=1, current_phase=discussion, runtime binding cleared
- Constraint: attached direct delivery requires active target runtime in the thread
```
