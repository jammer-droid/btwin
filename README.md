# btwin-runtime

Runtime workspace for the packaged B-TWIN stack.

This repository keeps the runtime-facing packages together in one place:

- `packages/btwin-core`
- `packages/btwin-cli`

`btwin-core` owns the domain/runtime implementation.
`btwin-cli` provides the CLI, HTTP API, and MCP proxy surface on top of it.

The Codex provider implementation currently stays inside `btwin-core`, so there
is no separate provider package in this split.
