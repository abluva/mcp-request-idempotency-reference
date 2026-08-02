# Request Idempotency — Reference Implementation

Minimal, runnable demonstration of the protocol-level mechanism proposed in
[SEP-3182: Request Idempotency](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/3182):
an `idempotencyKey` field on `tools/call` params (a sibling of `arguments`,
not nested inside it), with deduplication performed in server-side dispatch
before any tool handler runs, and explicit conflict semantics for a reused
key presented with different arguments.

This is a demonstration server, not a production implementation — the
dedup store is an in-memory dict with no eviction. It exists to make the
before/after failure mode concrete for reviewers, per the SEP process's
prototype requirement ("a standalone proof-of-concept demonstrating the
key mechanics").

## Setup

```bash
pip install -r requirements.txt
```

**Note on the version pin:** this demo uses `FastMCP` from
`mcp.server.fastmcp`. As of the `mcp` SDK's `2.0.0` release, that class
was renamed and moved to `MCPServer` in `mcp.server.mcpserver`. Installing
a plain `pip install mcp` today will pull `2.0.0` and fail on import. The
pin above (`mcp>=1.9.0,<2.0.0`) avoids that; if you'd rather run against
the current SDK, swap the import for `mcp.server.mcpserver.MCPServer`
(same constructor/decorator surface for the pieces this demo uses).

## Run

```bash
python3 client_demo.py
```

This spawns `server.py` as a stdio subprocess and runs three scenarios:

1. **No idempotency support** — a lost-response retry double-charges.
2. **With `idempotencyKey`** — the same retry is deduplicated; the
   cached result is returned, no re-execution.
3. **Conflict semantics** — the same key reused with different
   arguments is rejected outright, not replayed or silently executed.

Verified working end-to-end against `mcp==1.9.4` on 2026-08-01.

**Note on the 2026-07-28 stateless spec:** this demo's dedup logic
(`_dedup_store` in `server.py`) is keyed entirely by the client-supplied
`idempotencyKey` and never depends on protocol-level session state, so
nothing here needed to change when MCP went stateless. The one thing
worth calling out for anyone adapting this into a real server: in a
horizontally-scaled, stateless deployment, that store needs to be
shared across instances (a cache or database), not a per-process dict
like this demo uses — see the SEP's "Relationship to the 2026-07-28
stateless core and MRTR" section for why.

## Files

- `server.py` — the guarded (`charge_guarded`) and unguarded
  (`charge_unguarded`) tool implementations, plus a `reset_ledger` test
  helper.
- `client_demo.py` — drives all three scenarios against `server.py`.
