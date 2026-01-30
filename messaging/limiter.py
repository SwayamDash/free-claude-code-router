"""
Global Rate Limiter for Messaging Platforms.

Centralizes outgoing message requests and ensures compliance with rate limits
using a leaky bucket algorithm (aiolimiter) and a task queue.
"""

import asyncio
import logging
import os
from typing import Awaitable, Callable, Any, Optional
from aiolimiter import AsyncLimiter

logger = logging.getLogger(__name__)


class GlobalRateLimiter:
    """
    A thread-safe global rate limiter for messaging.

    Uses an asyncio.Queue to accept tasks and a background worker to
    process them at a rate defined by MESSAGING_RATE_LIMIT and MESSAGING_RATE_WINDOW.
    """

    _instance: Optional["GlobalRateLimiter"] = None
    _lock = asyncio.Lock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            # Note: The actual initialization happens in __init__
            # but we use a singleton pattern for the global limiter.
            pass
        return super(GlobalRateLimiter, cls).__new__(cls)

    @classmethod
    async def get_instance(cls) -> "GlobalRateLimiter":
        """Get the singleton instance of the limiter."""
        async with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
                # Start the background worker
                asyncio.create_task(cls._instance._worker())
        return cls._instance

    def __init__(self):
        # Prevent double initialization in singleton
        if hasattr(self, "_initialized"):
            return

        rate_limit = int(os.getenv("MESSAGING_RATE_LIMIT", "1"))
        rate_window = float(os.getenv("MESSAGING_RATE_WINDOW", "2.0"))

        self.limiter = AsyncLimiter(rate_limit, rate_window)
        self.queue: asyncio.Queue[
            tuple[Callable[[], Awaitable[Any]], asyncio.Future]
        ] = asyncio.Queue()
        self._initialized = True
        self._paused_until = 0

        logger.info(
            f"GlobalRateLimiter initialized ({rate_limit} req / {rate_window}s)"
        )

    async def _worker(self):
        """Background worker that processes queued messaging tasks."""
        logger.info("GlobalRateLimiter worker started")
        while True:
            try:
                # Get a task from the queue
                func, future = await self.queue.get()

                # Check for manual pause (FloodWait)
                now = asyncio.get_event_loop().time()
                if self._paused_until > now:
                    wait_time = self._paused_until - now
                    logger.warning(
                        f"Limiter worker paused, waiting {wait_time:.1f}s more..."
                    )
                    await asyncio.sleep(wait_time)

                # Wait for rate limit capacity
                async with self.limiter:
                    try:
                        result = await func()
                        if not future.done():
                            future.set_result(result)
                    except Exception as e:
                        # Handle Telegram FloodWaitError specifically
                        error_msg = str(e).lower()
                        if "flood" in error_msg or "wait" in error_msg:
                            # Attempt to extract seconds if possible, else default to 30
                            seconds = 30
                            try:
                                # Telethon's FloodWaitError usually has .seconds
                                if hasattr(e, "seconds"):
                                    seconds = e.seconds
                            except:
                                pass

                            logger.error(f"FloodWait detected! Pausing for {seconds}s")
                            self._paused_until = (
                                asyncio.get_event_loop().time() + seconds
                            )

                            # Re-queue the task at the front if possible,
                            # but simple approach is to retry after pause
                            await asyncio.sleep(seconds)
                            # Simple retry: put it back
                            await self.queue.put((func, future))
                        else:
                            if not future.done():
                                future.set_exception(e)
                    finally:
                        self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in limiter worker: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def enqueue(self, func: Callable[[], Awaitable[Any]]) -> Any:
        """
        Enqueue a messaging task and return its future result.

        This makes the call non-blocking for the caller if they don't await the result,
        or they can await it to get the message_id/result.
        """
        future = asyncio.get_event_loop().create_future()
        await self.queue.put((func, future))
        return await future

    def fire_and_forget(self, func: Callable[[], Awaitable[Any]]):
        """Enqueue a task without waiting for the result."""
        future = asyncio.get_event_loop().create_future()
        # We don't await the put because we want it to be really fast
        # but asyncio.Queue.put is a coroutine.
        # However, in most cases it won't block unless queue is full.
        asyncio.create_task(self.queue.put((func, future)))
