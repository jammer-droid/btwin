# btwin-runtime

Packaged runtime workspace for B-TWIN.

This repository keeps the runtime-facing packages together in one place:

- `packages/btwin-core`
- `packages/btwin-cli`

`btwin-core` owns the domain/runtime implementation.
`btwin-cli` provides the CLI, HTTP API, and MCP proxy surface on top of it.

The Codex provider implementation currently stays inside `btwin-core`, so there
is no separate provider package in this split.

## Runtime Model

The default B-TWIN operating model is:

```text
LLM Client
    ↓ stdio
btwin mcp-proxy
    ↓ HTTP
btwin serve-api
    ↓
~/.btwin/
```

Default assumptions:

- data lives in the global `~/.btwin` directory
- `serve-api` is the shared backend and is expected to stay available
- `mcp-proxy` is the lightweight bridge that MCP clients connect to
- project-local `.btwin/` is an exception for isolated testing, not the default

## Prerequisites

- Python 3.11+
- `uv`
- `codex` CLI if you plan to run `btwin init` or use Codex MCP integration

Install `uv` if needed:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Minimal Local Setup

If you only want to try the runtime from this clone, start here:

```bash
git clone https://github.com/jammer-droid/btwin-runtime.git
cd btwin-runtime
uv sync
```

Check the CLI entrypoint:

```bash
uv run btwin --help
```

Start the shared API locally:

```bash
uv run btwin serve-api
```

In another terminal, start the MCP proxy:

```bash
cd btwin-runtime
uv run btwin mcp-proxy
```

For a quick API health check:

```bash
curl -s http://localhost:8787/api/sessions/status
```

This flow is enough for local CLI and runtime smoke tests from the repo clone.
It does not make `btwin` globally available to your shell or MCP client.

## Recommended Persistent Setup

If you want Codex to launch `btwin mcp-proxy` directly and keep using this
runtime outside the repo shell, install `btwin` as a normal CLI first:

```bash
cd btwin-runtime
uv tool install -e .
btwin --help
```

Then initialize the default Codex-based provider config:

```bash
btwin init
```

Today the init flow supports Codex only. It creates `~/.btwin/providers.json`
and writes a Codex MCP entry that launches:

```toml
[mcp_servers.btwin]
command = "btwin"
args = ["mcp-proxy"]
```

The normal persistent setup is:

1. install `btwin` so it is on your normal `PATH`
2. run `btwin init`
3. use the global `~/.btwin` data directory
4. start `serve-api`
5. connect clients through `mcp-proxy`

Start the shared API:

```bash
btwin serve-api
```

## macOS Background Service

On macOS, the normal pattern is to keep `serve-api` running as a LaunchAgent.

The CLI can install and manage the standard LaunchAgent for you:

```bash
btwin service install
btwin service status
btwin service restart
btwin service stop
```

`btwin service install` writes `~/.btwin/com.btwin.serve-api.plist`, ensures
`~/.btwin/logs/` exists, links the plist into `~/Library/LaunchAgents/`, and
bootstraps the service with the current `btwin` executable found on `PATH`.

If the active `btwin` executable changes later, run `btwin service install`
again to refresh the LaunchAgent target.

For a stable long-lived service, prefer running `btwin service install` after
you have installed `btwin` globally. If you run it from `uv run`, the LaunchAgent
may point at the repo-local `.venv/bin/btwin` path for that clone.

Manual `launchctl` flow is still available if you want it. If you already have
the standard plist at `~/.btwin/com.btwin.serve-api.plist`, you can load it
with:

```bash
mkdir -p ~/.btwin/logs
launchctl bootstrap gui/$(id -u) ~/.btwin/com.btwin.serve-api.plist
```

Useful service commands:

```bash
launchctl print gui/$(id -u)/com.btwin.serve-api
launchctl kickstart -k gui/$(id -u)/com.btwin.serve-api
launchctl bootout gui/$(id -u)/com.btwin.serve-api
tail -f ~/.btwin/logs/serve-api.stderr.log
```

Example plist target after a global install:

```xml
<array>
  <string>/Users/home/.local/bin/btwin</string>
  <string>serve-api</string>
</array>
```

## Codex / MCP Setup

This repository already contains the packaged runtime assets needed by:

- `btwin serve-api`
- `btwin mcp-proxy`
- bundled runtime docs
- bundled protocol definitions
- bundled skills

For local testing from this clone, prefer `uv run btwin ...` first.

If you have already installed `btwin` globally, your MCP client can run:

```text
command: btwin
args: ["mcp-proxy"]
```

For Codex, the equivalent config is:

```toml
[mcp_servers.btwin]
command = "btwin"
args = ["mcp-proxy"]
```

After global install, initialize the runtime first:

```bash
btwin init
btwin install-skills --platform codex
```

If you replaced an older global `btwin` install with this runtime split, restart
your Codex/MCP client session after `btwin init`. Existing MCP proxy processes
may keep using the older environment until the client reconnects.

## Isolated Testing Mode

Use a repo-local data directory only when you explicitly want isolation from the
normal global store:

```bash
export BTWIN_DATA_DIR="$(pwd)/.btwin"
mkdir -p "$BTWIN_DATA_DIR"
uv run btwin serve-api
```

This is useful for:

- split-repo smoke tests
- temporary sandbox runs
- experiments that should not touch `~/.btwin`

## Repository Layout

```text
packages/
  btwin-core/   Core runtime/domain implementation
  btwin-cli/    CLI, HTTP API, MCP proxy, bundled runtime docs/skills
```

## Current Scope

This repository is intended to become the standalone runtime source of truth.

What is ready here:

- packaged runtime code
- package-owned bundled runtime assets
- CLI / API / MCP proxy code
- split-repo packaging work
- default runtime behavior based on `~/.btwin`

What still needs validation:

- clean wheel/venv smoke tests after split
- one-command bootstrap parity with the older integrated install flow
- end-to-end first-user onboarding clarity across local vs global install paths
