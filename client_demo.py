"""
Demonstrates: (1) the failure mode with no idempotency support, (2) the
proposed protocol-level idempotencyKey mechanism -- including the
conflict-semantics case (same key, different arguments) -- with the key
sent as a sibling of `arguments` in `tools/call` params, exactly as
SEP-3182 specifies, and deduplication happening in server-side dispatch
before the tool handler runs (see server.py).

Simulates a lost-response retry the way a real client would experience
it: call the tool, then call it again with the same arguments (and, for
the guarded tool, the same key) because the client never saw a response
to the first attempt.
"""
import asyncio
import uuid

import mcp.types as types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError


async def call_with_idempotency_key(session: ClientSession, name: str, arguments: dict, key: str | None = None):
    """ClientSession.call_tool() has no parameter for a field sibling to
    `arguments` (only name/arguments/meta), so sending idempotencyKey per
    the SEP's wire format -- as params.idempotencyKey, not inside
    params.arguments -- requires building the request directly rather than
    going through the high-level call_tool() convenience method."""
    kwargs = {"name": name, "arguments": arguments}
    if key is not None:
        kwargs["idempotencyKey"] = key  # sibling of arguments, per SEP-3182
    params = types.CallToolRequestParams(**kwargs)
    request = types.ClientRequest(root=types.CallToolRequest(method="tools/call", params=params))
    return await session.send_request(request, types.CallToolResult)


async def main():
    params = StdioServerParameters(command="python3", args=["server.py"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print("=" * 70)
            print("DISCOVERY: checking for the tools.idempotency capability")
            print("=" * 70)
            # NOTE: this SDK version does not yet expose server/discover
            # (SEP-2575) as a client-callable method, so this demo checks the
            # server's advertised capability dict directly rather than over
            # the wire. A production client on an SDK with server/discover
            # support should call that instead before relying on this
            # guarantee -- see the SEP's "Capability declaration" section.
            print("(this SDK version has no server/discover client API yet;")
            print(" see server.py's SUPPORTED_CAPABILITIES for what a real")
            print(" server/discover response would need to advertise)\n")

            print("=" * 70)
            print("SCENARIO 1: No idempotency support (today's MCP)")
            print("=" * 70)
            await session.call_tool("reset_ledger", {})
            print("Client sends charge_unguarded(amount=100)...")
            r1 = await session.call_tool("charge_unguarded", {"amount": 100})
            print(" ->", r1.content[0].text)
            print("Response is lost in transit (simulated). Client never")
            print("sees it, assumes failure, and retries the SAME request:")
            r2 = await session.call_tool("charge_unguarded", {"amount": 100})
            print(" ->", r2.content[0].text)
            print()
            print("RESULT: charged twice. This is the duplicate-execution")
            print("failure mode the SEP is meant to prevent.\n")

            print("=" * 70)
            print("SCENARIO 2: With idempotencyKey (proposed mechanism)")
            print("=" * 70)
            await session.call_tool("reset_ledger", {})
            key = str(uuid.uuid4())
            print(f"Client generates idempotencyKey = {key}")
            print("Client sends tools/call with params.idempotencyKey (NOT")
            print("inside arguments) alongside charge_guarded(amount=100)...")
            r1 = await call_with_idempotency_key(session, "charge_guarded", {"amount": 100}, key)
            print(" ->", r1.content[0].text)
            print("Response is lost in transit (simulated). Client retries")
            print("with the SAME key and SAME arguments:")
            r2 = await call_with_idempotency_key(session, "charge_guarded", {"amount": 100}, key)
            print(" ->", r2.content[0].text)
            print()
            print("RESULT: charged once. The retry was deduplicated by the")
            print("server's dispatch layer -- charge_guarded's own function")
            print("body has no idempotency logic in it at all (see server.py).\n")

            print("=" * 70)
            print("SCENARIO 3: Conflict semantics (same key, different args)")
            print("=" * 70)
            print("A second logical operation reuses the same key by mistake")
            print("(client bug, or two independent retries colliding):")
            try:
                await call_with_idempotency_key(session, "charge_guarded", {"amount": 999}, key)
                print(" -> ERROR: expected a conflict, but the call succeeded")
            except McpError as e:
                print(" -> rejected:", e.error.message)
                print("    error.data:", e.error.data)
            print()
            print("RESULT: rejected before dispatch -- not silently replayed")
            print("and not silently executed -- matching the SEP's conflict")
            print("semantics ('Server behavior on a repeated key', case 3).")


if __name__ == "__main__":
    asyncio.run(main())
