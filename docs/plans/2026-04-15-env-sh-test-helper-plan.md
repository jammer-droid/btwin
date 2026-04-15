# env.sh Test Helper Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add shell-local test helper functions to the generated isolated attached `env.sh` so users can `source env.sh` and then run short commands like `btwin_test_up` and `btwin_test_hud` without touching the global runtime.

**Architecture:** Keep the existing explicit activation model. Extend only the generated `env.sh` payload and the bootstrap-script tests so helper behavior stays shell-local, API control stays scoped to the isolated `BTWIN_API_URL`, and the global `~/.btwin` environment is never mutated or stopped.

**Tech Stack:** Bash, Python pytest, repo-local `btwin` CLI, isolated attached test environment

---

### Task 1: Generate helper functions in `env.sh`

**Files:**
- Modify: `scripts/bootstrap_isolated_attached_env.sh`
- Test: `tests/test_bootstrap_isolated_attached_env.py`

**Step 1: Write the failing test**

Extend `tests/test_bootstrap_isolated_attached_env.py` so it expects the generated `env.sh` to contain helper functions:

```python
    assert "btwin_test_up()" in env_text
    assert "btwin_test_hud()" in env_text
    assert "btwin_test_status()" in env_text
    assert "btwin_test_down()" in env_text
```

Add a shell smoke that sources the generated `env.sh` and confirms the helpers exist:

```python
    smoke = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            'source "$1"; declare -F btwin_test_up; declare -F btwin_test_hud; declare -F btwin_test_status; declare -F btwin_test_down',
            "_",
            str(env_file),
        ],
        ...
    )
    assert smoke.returncode == 0
```

**Step 2: Run test to verify it fails**

Run:

```bash
uv run python -m pytest tests/test_bootstrap_isolated_attached_env.py -q
```

Expected: FAIL because the generated `env.sh` does not yet define the helper functions.

**Step 3: Write minimal implementation**

Update `write_env_file()` in `scripts/bootstrap_isolated_attached_env.sh` so the emitted `env.sh` includes:

- `btwin_test_up`
- `btwin_test_hud`
- `btwin_test_status`
- `btwin_test_down`

Implementation rules:

- helpers must stay shell-local
- helpers must use the current `BTWIN_API_URL`
- helpers must not stop or mutate the global runtime
- helpers must use the isolated env’s `serve-api.pid` / logs only

Keep the first version simple and explicit. No auto-detection, no extra wrapper files.

**Step 4: Run test to verify it passes**

Run:

```bash
uv run python -m pytest tests/test_bootstrap_isolated_attached_env.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add scripts/bootstrap_isolated_attached_env.sh tests/test_bootstrap_isolated_attached_env.py
git commit -m "feat: add env.sh test helpers"
```

### Task 2: Make helper behavior safe and predictable

**Files:**
- Modify: `scripts/bootstrap_isolated_attached_env.sh`
- Test: `tests/test_bootstrap_isolated_attached_env.py`

**Step 1: Write the failing test**

Add targeted shell-smoke assertions for helper behavior:

```python
    smoke = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            'source "$1"; btwin_test_status; btwin_test_up; btwin_test_status',
            "_",
            str(env_file),
        ],
        ...
    )
```

At minimum, the test should prove:

- `btwin_test_status` runs successfully in a sourced shell
- `btwin_test_up` uses the isolated env variables and PID path
- helper output references the isolated API URL, not a global default

If starting a real API inside the unit test is too heavy, assert on the generated function bodies and use a shell stub for `btwin`/`curl` through `PATH` to verify the helpers call the right commands.

**Step 2: Run test to verify it fails**

Run:

```bash
uv run python -m pytest tests/test_bootstrap_isolated_attached_env.py -q
```

Expected: FAIL because the helper functions do not yet satisfy the new safety/behavior assertions.

**Step 3: Write minimal implementation**

Refine the helper implementations so they:

- check health on `BTWIN_API_URL`
- reuse an already-running isolated API
- start an isolated `btwin serve-api --port ...` in the background when needed
- use the isolated PID file for `btwin_test_down`
- emit a clear error if the port is unavailable instead of killing unrelated processes

Do not add broader process discovery or global cleanup logic.

**Step 4: Run test to verify it passes**

Run:

```bash
uv run python -m pytest tests/test_bootstrap_isolated_attached_env.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add scripts/bootstrap_isolated_attached_env.sh tests/test_bootstrap_isolated_attached_env.py
git commit -m "fix: scope env.sh helpers to isolated api"
```

### Task 3: Update the operator docs for the shorter flow

**Files:**
- Modify: `README.md`

**Step 1: Write the failing doc expectation**

Define the doc gap explicitly:

```text
- README still teaches manual `btwin serve-api --port ...`
- README does not yet advertise `btwin_test_up` / `btwin_test_hud`
```

**Step 2: Confirm the gap**

Run:

```bash
rg -n "btwin_test_up|btwin_test_hud|btwin_test_status|btwin_test_down|serve-api --port" README.md
```

Expected: helper commands are absent or the shorter flow is not documented.

**Step 3: Write minimal documentation changes**

Update the isolated testing section so it shows the shorter operator path:

```bash
source .btwin-attached-test/env.sh
btwin_test_up
btwin_test_hud
```

Also mention:

- `btwin_test_status`
- `btwin_test_down`
- helpers only affect the sourced isolated env
- unsourced shells still use the global default runtime

**Step 4: Verify the wording**

Run:

```bash
rg -n "btwin_test_up|btwin_test_hud|btwin_test_status|btwin_test_down|global" README.md
```

Expected: README now documents the helper flow clearly and accurately.

**Step 5: Commit**

```bash
git add README.md
git commit -m "docs: add env.sh helper workflow"
```

### Task 4: Run a real isolated attached scenario smoke

**Files:**
- Verify only: `scripts/bootstrap_isolated_attached_env.sh`
- Verify only: generated `<bootstrap-root>/env.sh`

**Step 1: Prepare the isolated environment**

Run:

```bash
TMP_ROOT=/tmp/btwin-helper-smoke
TMP_PROJECT=/tmp/btwin-helper-project
rm -rf "$TMP_ROOT" "$TMP_PROJECT"
mkdir -p "$TMP_PROJECT"
BTWIN_BIN="$PWD/.venv/bin/btwin" \
scripts/bootstrap_isolated_attached_env.sh start --skip-server \
  --root "$TMP_ROOT" \
  --project-root "$TMP_PROJECT" \
  --project btwin-helper-smoke \
  --port 8792
```

Expected: bootstrap completes and writes `$TMP_ROOT/env.sh`.

**Step 2: Verify the short helper flow**

Run in a fresh shell:

```bash
source "$TMP_ROOT/env.sh"
btwin_test_status
btwin_test_up
btwin_test_hud
```

Expected:

- helpers are available immediately after sourcing
- `btwin_test_up` starts or reuses the isolated API only
- `btwin_test_hud` enters HUD without extra `BTWIN_*=` prefixes

**Step 3: Verify cleanup**

Run:

```bash
source "$TMP_ROOT/env.sh"
btwin_test_down
btwin_test_status
```

Expected:

- only the isolated env’s server is stopped
- global runtime remains untouched

**Step 4: Record the smoke result**

Report:

```text
Scenario smoke passed: env.sh test helpers
- Commands: bootstrap_isolated_attached_env.sh start --skip-server, source env.sh, btwin_test_status, btwin_test_up, btwin_test_hud, btwin_test_down
- Verified: sourced helper flow started/reused only the isolated API and let plain btwin HUD run against the isolated environment
- Constraint: helpers remain shell-local and are unavailable in unsourced shells
```

**Step 5: Commit**

If code or docs changed during smoke fixes:

```bash
git add <touched-files>
git commit -m "test: verify env.sh helper smoke"
```

If no files changed, skip this commit.
