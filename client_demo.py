"""
Demonstrates: (1) the failure mode with no idempotency support, and
(2) the fix with the proposed idempotencyKey mechanism -- including the
conflict-semantics case (same key, different arguments).

Simulates a lost-response retry the way a real client would experience
it: call the tool, then call it again with the same arguments (and, for
the guarded tool, the same key) because the client never saw a response
to the first attempt.
"""
import asyncio
import uuid
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(command="python3", args=["server.py"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

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
            print("Client sends charge_guarded(amount=100, idempotencyKey=key)...")
            r1 = await session.call_tool(
                "charge_guarded", {"amount": 100, "idempotencyKey": key}
            )
            print(" ->", r1.content[0].text)
            print("Response is lost in transit (simulated). Client retries")
            print("with the SAME key and SAME arguments:")
            r2 = await session.call_tool(
                "charge_guarded", {"amount": 100, "idempotencyKey": key}
            )
            print(" ->", r2.content[0].text)
            print()
            print("RESULT: charged once. The retry was deduplicated.\n")

            print("=" * 70)
            print("SCENARIO 3: Conflict semantics (same key, different args)")
            print("=" * 70)
            print("A second logical operation reuses the same key by mistake")
            print("(client bug, or two independent retries colliding):")
            r3 = await session.call_tool(
                "charge_guarded", {"amount": 999, "idempotencyKey": key}
            )
            print(" ->", r3.content[0].text)
            print()
            print("RESULT: rejected rather than silently replayed or silently")
            print("executed -- the conflict-semantics answer from the SEP's")
            print("open design questions, made concrete.")


asyncio.run(main())
