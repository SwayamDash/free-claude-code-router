import asyncio
import time
import os
from messaging.limiter import GlobalRateLimiter

# Mocking logging for test
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_compaction():
    # Set small rate for testing
    os.environ["MESSAGING_RATE_LIMIT"] = "1"
    os.environ["MESSAGING_RATE_WINDOW"] = "1.0"

    limiter = await GlobalRateLimiter.get_instance()

    call_counts = {}

    async def mock_edit(msg_id, content):
        call_counts[msg_id] = call_counts.get(msg_id, 0) + 1
        logger.info(f"Executing edit for {msg_id}: {content}")
        return f"done_{content}"

    print("\n--- Starting Compaction Test ---")

    # Spam 5 edits for the same message
    start_time = time.time()
    futures = []
    for i in range(5):
        # We use fire_and_forget to fill the queue fast
        limiter.fire_and_forget(
            lambda i=i: mock_edit("msg1", f"update_{i}"), dedup_key="edit:msg1"
        )
        print(f"Queued update_{i}")

    # Wait for processing (should take ~2 seconds total for 1-2 actual calls)
    # The first one might go through immediately if limiter has capacity
    # The next 4 should be compacted into 1 if they arrive before the first one finishes?
    # Actually, the FIRST one is already "popped" by the worker while the loop is still queueing.
    # So we expect roughly 2 calls: the 1st one, and the LAST one (all intermediate ones compacted).

    await asyncio.sleep(2.5)

    print(f"\nTotal calls to Telegram: {sum(call_counts.values())}")
    print(f"Call breakdown: {call_counts}")

    assert call_counts["msg1"] <= 2, (
        f"Should have at most 2 calls (first and last), but got {call_counts['msg1']}"
    )
    print("✅ Compaction test passed!")


if __name__ == "__main__":
    asyncio.run(test_compaction())
