import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock

# Add current directory to path
sys.path.append(os.getcwd())

from messaging.limiter import GlobalRateLimiter


async def test_rate_limiting():
    print("\n--- Testing Rate Limiting (1 req / 2 sec) ---")
    os.environ["MESSAGING_RATE_LIMIT"] = "1"
    os.environ["MESSAGING_RATE_WINDOW"] = "2.0"

    limiter = await GlobalRateLimiter.get_instance()

    start_time = time.time()
    results = []

    async def mock_task(i):
        ts = time.time() - start_time
        print(f"[{ts:.2f}s] Task {i} started")
        await asyncio.sleep(0.1)  # Simulate network latency
        print(f"[{time.time() - start_time:.2f}s] Task {i} completed")
        return i

    # Enqueue 5 tasks simultaneously
    tasks = [limiter.enqueue(lambda i=i: mock_task(i)) for i in range(5)]

    print("Tasks enqueued, waiting for results...")
    completed = await asyncio.gather(*tasks)

    total_time = time.time() - start_time
    print(f"All tasks completed in {total_time:.2f}s")
    print(f"Results: {completed}")

    # Check if timing is correct: 5 tasks at 1 req / 2 sec should take about 8 seconds
    # T0: Task 0
    # T2: Task 1
    # T4: Task 2
    # T6: Task 3
    # T8: Task 4
    if total_time >= 8.0:
        print("[SUCCESS] Rate limiting working as expected!")
    else:
        print(f"[FAILURE] Rate limiting failed! Took only {total_time:.2f}s")


async def test_flood_wait():
    print("\n--- Testing FloodWait Recovery ---")
    limiter = await GlobalRateLimiter.get_instance()

    # Reset limiter for fresh test
    limiter._paused_until = 0

    start_time = time.time()

    class FloodError(Exception):
        def __init__(self, seconds):
            self.seconds = seconds
            super().__init__(f"FloodWait for {seconds}s")

    attempt = 0

    async def task_that_fails_once():
        nonlocal attempt
        ts = time.time() - start_time
        attempt += 1
        if attempt == 1:
            print(f"[{ts:.1f}s] First attempt: Simulating FloodWait(5s)")
            raise FloodError(5)
        print(f"[{ts:.1f}s] Second attempt: Success!")
        return "success"

    result = await limiter.enqueue(task_that_fails_once)
    total_time = time.time() - start_time
    print(f"Task completed with result: {result} in {total_time:.1f}s")

    if total_time >= 5.0:
        print("[SUCCESS] FloodWait recovery working as expected!")
    else:
        print(f"[FAILURE] FloodWait recovery failed! Took only {total_time:.1f}s")


async def main():
    await test_rate_limiting()
    await test_flood_wait()
    print("\nVerification complete.")
    # Exit script
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
