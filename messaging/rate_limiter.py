import asyncio
import logging
import threading
import time
from typing import Callable, Any, TypeVar, Coroutine
from ratelimitqueue import RateLimitQueue

logger = logging.getLogger(__name__)

T = TypeVar("T")


class GlobalRateLimiter:
    """
    A global rate limiter that ensures operations are executed at a specific rate.
    Uses ratelimitqueue.RateLimitQueue for the heavy lifting.

    Since RateLimitQueue is thread-based/blocking, we run a worker in a separate thread
    to avoid blocking the asyncio event loop.
    """

    def __init__(self, calls: int = 1, period: float = 1.0):
        self._period = period
        self.queue = RateLimitQueue(calls=calls, per=period)
        self._stop_event = threading.Event()
        self._pause_until = 0.0
        self._lock = threading.Lock()
        self._worker_thread = threading.Thread(target=self._worker, daemon=True)
        self._worker_thread.start()
        logger.info(f"GlobalRateLimiter started with {calls} calls per {period}s")

    @property
    def period(self) -> float:
        """Return the rate limit period."""
        return self._period

    async def execute(self, func: Callable[[], Coroutine[Any, Any, T]]) -> T:
        """
        Schedule a function to be executed within the rate limit.
        Returns the result of the function.
        """
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.queue.put((func, future, loop))
        return await future

    def _worker(self):
        """Worker thread that pulls from the RateLimitQueue and executes tasks."""
        import concurrent.futures

        while not self._stop_event.is_set():
            try:
                # Check if paused
                with self._lock:
                    pause_wait = self._pause_until - time.time()

                if pause_wait > 0:
                    logger.warning(
                        f"GlobalRateLimiter paused, waiting {pause_wait:.1f}s..."
                    )
                    # Sleep in small increments to remain responsive to stop event
                    time.sleep(min(pause_wait, 1.0))
                    continue

                # get() blocks based on rate limit
                item = self.queue.get(block=True)
                task_fn, future, loop = item

                if future.done():
                    self.queue.task_done()
                    continue

                # Execute the task and WAIT for it to complete
                # This ensures we don't pull the next item until this one is done,
                # which is critical for detecting and honoring flood waits.
                async def run_task(f=task_fn):
                    return await f()

                task_future = asyncio.run_coroutine_threadsafe(run_task(), loop)
                try:
                    result = task_future.result()
                    if not future.done():
                        loop.call_soon_threadsafe(future.set_result, result)
                except (concurrent.futures.CancelledError, asyncio.CancelledError):
                    if not future.done():
                        loop.call_soon_threadsafe(future.cancel)
                except Exception as e:
                    # Detect flood wait (e.g. from Telethon)
                    wait_seconds = getattr(e, "seconds", None)
                    if wait_seconds:
                        logger.error(
                            f"GlobalRateLimiter detected flood wait: pausing for {wait_seconds}s"
                        )
                        with self._lock:
                            # Set pause if it's further in the future than current pause
                            self._pause_until = max(
                                self._pause_until, time.time() + wait_seconds
                            )

                    if not future.done():
                        loop.call_soon_threadsafe(future.set_exception, e)

                self.queue.task_done()
            except Exception as e:
                logger.error(f"Error in GlobalRateLimiter worker: {e}")
                time.sleep(0.1)

    def stop(self):
        """Stop the background worker."""
        self._stop_event.set()
        if self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.0)
