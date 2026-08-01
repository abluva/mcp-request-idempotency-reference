"""
Reference implementation sketch for the proposed MCP request-idempotency
mechanism: an optional `idempotencyKey` argument on tools/call, with
server-side deduplication and explicit conflict-fingerprint semantics.

This is a minimal demonstration server, not a production implementation.
It exists to show the mechanism working end to end, and to make the
before/after failure mode concrete for reviewers.
"""
import hashlib
import json
import time
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("idempotency-demo")

# Server-side dedup store: idempotency key -> (result, argument fingerprint, timestamp)
# A real implementation would bound this with the retention window discussed
# in the SEP rather than an in-memory dict with no eviction.
_dedup_store: dict[str, dict] = {}
RETENTION_SECONDS = 300

# The "external system" this tool mutates -- stands in for something like
# a payment charge, a file write, or a GitHub merge. Every UNGUARDED call
# increments it, simulating a real side effect happening again.
_ledger = {"balance": 0}


def _fingerprint(arguments: dict) -> str:
    """Stable hash of the call arguments, used to detect key reuse with
    different parameters -- the conflict-semantics question raised in the
    SEP's open design questions."""
    canonical = json.dumps(arguments, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


@mcp.tool()
def charge_unguarded(amount: int) -> str:
    """A side-effecting tool with NO idempotency support -- demonstrates
    the failure mode. Every call, including a retry of an already-executed
    request, increments the ledger again."""
    _ledger["balance"] += amount
    return f"Charged {amount}. New balance: {_ledger['balance']}"


@mcp.tool()
def charge_guarded(amount: int, idempotencyKey: str | None = None) -> str:
    """Same tool, WITH the proposed idempotencyKey mechanism. A retried
    call bearing a previously-seen key and matching arguments returns the
    cached result without re-executing. A retried call bearing a
    previously-seen key with DIFFERENT arguments is rejected, per the
    conflict-semantics design question in the SEP."""
    if idempotencyKey is None:
        # Backward-compatible path: no key supplied, no dedup guarantee.
        _ledger["balance"] += amount
        return f"Charged {amount} (no idempotency key supplied). New balance: {_ledger['balance']}"

    fp = _fingerprint({"amount": amount})
    now = time.time()

    cached = _dedup_store.get(idempotencyKey)
    if cached is not None and now - cached["ts"] < RETENTION_SECONDS:
        if cached["fingerprint"] != fp:
            return (
                f"ERROR: idempotencyKey '{idempotencyKey}' was already used "
                f"with different arguments. Refusing to execute or replay."
            )
        return f"[DEDUPED, not re-executed] {cached['result']}"

    _ledger["balance"] += amount
    result = f"Charged {amount}. New balance: {_ledger['balance']}"
    _dedup_store[idempotencyKey] = {"result": result, "fingerprint": fp, "ts": now}
    return result


@mcp.tool()
def reset_ledger() -> str:
    """Test helper: reset state between demo runs."""
    _ledger["balance"] = 0
    _dedup_store.clear()
    return "Ledger reset."


if __name__ == "__main__":
    mcp.run(transport="stdio")
