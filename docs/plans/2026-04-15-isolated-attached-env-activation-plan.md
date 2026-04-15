# Isolated Attached Env Activation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the generated isolated attached `env.sh` act as a shell-local activation entrypoint so operators can `source env.sh` and then run `btwin hud` and related commands against the isolated test environment without manual re-export.

**Architecture:** Keep runtime selection explicit and environment-driven. Limit changes to the isolated bootstrap helper, its tests, and the operator docs so the global `~/.btwin` flow and HUD semantics remain unchanged. Validate the result with both unit-level script assertions and one real isolated attached CLI smoke.

**Tech Stack:** Bash, Python pytest, repo-local `btwin` CLI, isolated attached test environment

---

### Task 1: Strengthen generated `env.sh` activation semantics

**Files:**
- Modify: `scripts/bootstrap_isolated_attached_env.sh`
- Test: `tests/test_bootstrap_isolated_attached_env.py`

**Step 1: Write the failing test**

Extend `tests/test_bootstrap_isolated_attached_env.py` with a second assertion block that checks the generated `env.sh` for the new activation behavior:

```python
    repo_venv_bin = repo_root / ".venv" / "bin"
    assert 'export BTWIN_CONFIG_PATH="' in env_text
    assert 'export BTWIN_DATA_DIR="' in env_text
    assert 'export BTWIN_API_URL="' in env_text
    assert f'if [[ -d "{repo_venv_bin}" ]]; then' in env_text
    assert f'export PATH="{repo_venv_bin}:$PATH"' in env_text
```

Also cover the fallback behavior so the script does not hard-fail if `.venv/bin` is missing by asserting the `if [[ -d ... ]]` guard exists rather than a raw unconditional `PATH` export.

**Step 2: Run test to verify it fails**

Run:

```bash
uv run python -m pytest tests/test_bootstrap_isolated_attached_env.py -q
```

Expected: FAIL because the current `env.sh` only exports `BTWIN_CONFIG_PATH`, `BTWIN_DATA_DIR`, and `BTWIN_API_URL`.

**Step 3: Write minimal implementation**

Update `write_env_file()` in `scripts/bootstrap_isolated_attached_env.sh` so the generated `env.sh`:

```bash
export BTWIN_CONFIG_PATH="..."
export BTWIN_DATA_DIR="..."
export BTWIN_API_URL="..."
if [[ -d "/abs/path/to/repo/.venv/bin" ]]; then
  export PATH="/abs/path/to/repo/.venv/bin:$PATH"
fi
```

Implementation rules:

- Use the repo that generated the bootstrap script as the source for `.venv/bin`.
- Keep activation shell-local only; do not edit shell rc files.
- Do not mutate global service or `~/.btwin`.
- Do not change core runtime resolution logic in Python.

**Step 4: Run test to verify it passes**

Run:

```bash
uv run python -m pytest tests/test_bootstrap_isolated_attached_env.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_bootstrap_isolated_attached_env.py scripts/bootstrap_isolated_attached_env.sh
git commit -m "feat: strengthen isolated env activation"
```

### Task 2: Document the operator workflow clearly

**Files:**
- Modify: `README.md`
- Modify: `docs/local/2026-04-15-workflow-constraints-operator-model.ko.md`

**Step 1: Write the failing doc expectation**

Before editing, define the required doc updates explicitly:

- README isolated testing section must show `source <env.sh>` followed by plain `btwin hud` / `btwin runtime current --json`.
- Operator note must explain that `source env.sh` affects only the current shell and leaves the global runtime untouched.

There is no automated doc test here, so the “failing test” is a checklist:

```text
- docs do not yet say that env.sh is the shell-local activation boundary
- docs still imply more manual environment handling than needed
```

**Step 2: Confirm the gap**

Run:

```bash
rg -n "source .*env.sh|btwin hud|shell-local|global" README.md docs/local/2026-04-15-workflow-constraints-operator-model.ko.md
```

Expected: the current text does not yet fully describe the new activation semantics.

**Step 3: Write minimal documentation changes**

Update README and the operator note so they say:

- `source env.sh` is the activation step
- after that, plain `btwin` commands use the isolated environment
- only the current shell is affected
- shells that do not source `env.sh` still use the global default environment

Keep the docs aligned with the current runtime model; do not claim auto-detection or HUD spec changes.

**Step 4: Verify the wording**

Run:

```bash
rg -n "current shell|global|source .*env.sh|btwin hud|runtime current --json" README.md docs/local/2026-04-15-workflow-constraints-operator-model.ko.md
```

Expected: both docs now describe the activation model and example commands.

**Step 5: Commit**

```bash
git add README.md docs/local/2026-04-15-workflow-constraints-operator-model.ko.md
git commit -m "docs: clarify isolated env activation flow"
```

### Task 3: Run a real isolated attached scenario smoke

**Files:**
- Verify only: `scripts/bootstrap_isolated_attached_env.sh`
- Verify only: generated `<bootstrap-root>/env.sh`

**Step 1: Prepare the isolated environment**

Run:

```bash
TMP_ROOT=/tmp/btwin-activation-smoke
TMP_PROJECT=/tmp/btwin-activation-project
rm -rf "$TMP_ROOT" "$TMP_PROJECT"
BTWIN_BIN="$PWD/.venv/bin/btwin" \
scripts/bootstrap_isolated_attached_env.sh start --skip-server \
  --root "$TMP_ROOT" \
  --project-root "$TMP_PROJECT" \
  --project btwin-activation-smoke \
  --port 8791
```

Expected: bootstrap completes and writes `$TMP_ROOT/env.sh`.

**Step 2: Verify shell-local activation**

Run in a fresh shell:

```bash
source "$TMP_ROOT/env.sh"
command -v btwin
printf '%s\n' "$BTWIN_CONFIG_PATH"
printf '%s\n' "$BTWIN_DATA_DIR"
printf '%s\n' "$BTWIN_API_URL"
```

Expected:

- `command -v btwin` resolves to the worktree `.venv/bin/btwin` when that directory exists
- `BTWIN_*` points at the isolated root

**Step 3: Start the attached API and run plain `btwin` commands**

Run:

```bash
source "$TMP_ROOT/env.sh"
btwin serve-api --port 8791
```

Then in a second shell:

```bash
source "$TMP_ROOT/env.sh"
cd "$TMP_PROJECT"
btwin hud
btwin runtime current --json
```

Expected:

- commands run without manual `BTWIN_*=` prefixes
- they resolve against the isolated attached environment, not the global default runtime

**Step 4: Record the smoke result**

Report:

```text
Scenario smoke passed: isolated env activation
- Commands: bootstrap_isolated_attached_env.sh start --skip-server, source env.sh, btwin serve-api, btwin hud, btwin runtime current --json
- Verified: plain btwin commands used the isolated environment after shell-local activation
- Constraint: activation remains shell-local; unsourced shells continue to use global defaults
```

**Step 5: Commit**

If code or docs changed during smoke fixes:

```bash
git add <touched-files>
git commit -m "test: verify isolated env activation smoke"
```

If no files changed, skip this commit.
