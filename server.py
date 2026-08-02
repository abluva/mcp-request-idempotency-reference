"""
Reference implementation for the proposed MCP request-idempotency mechanism
(SEP-3182): an optional idempotencyKey field on tools/call params (sibling
of `arguments`, per the SEP's request format), with deduplication performed
in server-side dispatch -- before any tool handler runs -- and explicit
conflict semantics for a key reused with different arguments.

This is a minimal demonstration server, not a production implementation.
It exists to show the mechanism working end to end, and to make the
before/after failure mode concrete for reviewers.
"""
import hashlib
import json
import time

import mcp.types as types
from mcp.server.fastmcp import FastMCP
from mcp.shared.exceptions import McpError

mcp = FastMCP("idempotency-demo")

# Server-side dedup store: idempotency key -> (result, request fingerprint, timestamp)
# A real implementation would bound this with the retention window discussed
# in the SEP rather than an in-memory dict with no eviction.
_dedup_store: dict[str, dict] = {}
_in_progress: set[str] = set()
RETENTION_SECONDS = 300

# The "external system" this tool mutates -- stands in for something like
# a payment charge, a file write, or a GitHub merge. Every UNGUARDED call
# increments it, simulating a real side effect happening again.
_ledger = {"balance": 0}


def _fingerprint(name: str, arguments: dict) -> str:
    """Stable hash of tool name + arguments, used to detect key reuse with
    different parameters -- the conflict-semantics case in the SEP."""
    canonical = json.dumps({"name": name, "arguments": arguments}, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


@mcp.tool()
def charge_unguarded(amount: int) -> str:
    """A side-effecting tool with NO idempotency support -- demonstrates
    the failure mode. Every call, including a retry of an already-executed
    request, increments the ledger again."""
    _ledger["balance"] += amount
    return f"Charged {amount}. New balance: {_ledger['balance']}"


@mcp.tool()
def charge_guarded(amount: int) -> str:
    """Same tool, but with NO knowledge of idempotency at all. Deduplication
    happens in server-side dispatch (see _idempotency_dispatch, below),
    before this function is ever called -- this function only sees genuinely
    new requests."""
    _ledger["balance"] += amount
    return f"Charged {amount}. New balance: {_ledger['balance']}"


@mcp.tool()
def reset_ledger() -> str:
    """Test helper: reset state between demo runs."""
    _ledger["balance"] = 0
    _dedup_store.clear()
    _in_progress.clear()
    return "Ledger reset."


# --- Protocol-level idempotency dispatch (SEP-3182) ---
#
# FastMCP's own tools/call handler only ever sees (tool_name, arguments); it
# has no visibility into sibling params like idempotencyKey. To implement
# the SEP as a dispatch-layer mechanism rather than a per-tool convention,
# we wrap the low-level handler FastMCP already registered, so the check
# happens generically for every tool call before the underlying tool runs.
_original_call_tool_handler = mcp._mcp_server.request_handlers[types.CallToolRequest]


async def _idempotency_dispatch(req: types.CallToolRequest):
    params = req.params
    # idempotencyKey is a sibling of `name`/`arguments` in params, per the
    # SEP's request format -- NOT nested inside `arguments`. pydantic's
    # `extra: allow` config on CallToolRequestParams preserves it here.
    idempotency_key = (params.model_extra or {}).get("idempotencyKey")

    if idempotency_key is None:
        # Backward-compatible path: no key supplied, no dedup guarantee.
        return await _original_call_tool_handler(req)

    if idempotency_key in _in_progress:
        raise McpError(types.ErrorData(
            code=types.INVALID_PARAMS,
            message=f"A request with idempotencyKey '{idempotency_key}' is already in progress",
            data={"type": "idempotency_key_in_progress", "idempotencyKey": idempotency_key},
        ))

    fp = _fingerprint(params.name, params.arguments or {})
    now = time.time()
    cached = _dedup_store.get(idempotency_key)

    if cached is not None and now - cached["ts"] < RETENTION_SECONDS:
        if cached["fingerprint"] != fp:
            raise McpError(types.ErrorData(
                code=types.INVALID_PARAMS,
                message=f"idempotencyKey '{idempotency_key}' was already used with different arguments",
                data={"type": "idempotency_key_conflict", "idempotencyKey": idempotency_key},
            ))
        return cached["result"]  # replay -- tool handler is NOT invoked again

    _in_progress.add(idempotency_key)
    try:
        result = await _original_call_tool_handler(req)
    finally:
        _in_progress.discard(idempotency_key)

    _dedup_store[idempotency_key] = {"result": result, "fingerprint": fp, "ts": now}
    return result


mcp._mcp_server.request_handlers[types.CallToolRequest] = _idempotency_dispatch


# --- Capability advertisement (server/discover) ---
#
# NOTE: this SDK version's FastMCP does not yet expose a public API for
# custom server/discover capability entries; if/when SEP-2575 lands in the
# SDK, this should register `{"tools": {"idempotency": {}}}` there. Until
# then this dict is what the demo client checks directly (see client_demo.py)
# to keep the discovery step observable rather than silently assumed.
SUPPORTED_CAPABILITIES = {"tools": {"idempotency": {}}}


if __name__ == "__main__":
    mcp.run(transport="stdio")
