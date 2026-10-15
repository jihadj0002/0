"""
Process-wide async runtime for the AI pipeline.

Themes
------
* All *long-wait* work (LLM calls, ERP HTTP, platform sends, media) is
  I/O-bound.  A single asyncio event loop services tens of thousands of
  concurrent conversations per process — far beyond what a fixed thread
  pool can provide — because each await releases the loop while the
  network I/O is in flight.

* Django ORM calls stay *synchronous* and are dispatched through a small
  bounded thread pool (``sync_pool``).  Only external HTTP is awaited
  in-loop, which avoids the classic "one Postgres connection per
  coroutine" problem.

* A second small thread pool exists for the *fast persist path* in
  webhooks (creating Message + MessageBatch rows).  That step must not
  block the HTTP response and is pure DB work (~50ms), so threads are
  perfect there.

Public API
----------
* ``start_event_loop()``  — idempotent; called from ApiConfig.ready().
* ``submit_async(coro)``  — schedule an async task from any thread.
* ``call_later(seconds, coro_factory)`` — debounce / delay, thread-safe.
* ``schedule_repeating(interval, coro_factory)`` — periodic task.
* ``submit_sync(fn, *args)`` — run fast sync work on the persist pool.
* ``run_in_thread(fn, *args)`` — run sync work on the persist pool from
  async code; returns an awaitable.
"""
import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# Small pool for the fast webhook persist step (~50ms DB work) and any
# short synchronous DB writes called from within async tasks.
SYNC_MAX_WORKERS = 50

_thread_pool: ThreadPoolExecutor | None = None
_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_loop_lock = threading.Lock()
_loop_started = threading.Event()
_repeating_tasks: list[asyncio.Task] = []


def _get_pool() -> ThreadPoolExecutor:
    global _thread_pool
    if _thread_pool is None:
        _thread_pool = ThreadPoolExecutor(
            max_workers=SYNC_MAX_WORKERS,
            thread_name_prefix="ai-sync",
        )
    return _thread_pool


def start_event_loop(timeout: float = 5.0) -> None:
    """Start the process-wide event loop in a daemon thread (idempotent)."""
    global _loop, _loop_thread
    with _loop_lock:
        if _loop is not None and _loop.is_running():
            return
        new_loop = asyncio.new_event_loop()
        thread = threading.Thread(
            target=_run_loop,
            args=(new_loop,),
            daemon=True,
            name="ai-event-loop",
        )
        _loop = new_loop
        _loop_thread = thread
        _loop_started.clear()
        thread.start()
    if not _loop_started.wait(timeout):
        logger.warning("Event loop did not signal ready within %.1fs", timeout)


def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    try:
        _loop_started.set()
        loop.run_forever()
    except Exception:
        logger.exception("Event loop crashed")
    finally:
        # Best-effort cancellation on shut down.
        try:
            pending = [
                t for t in asyncio.all_tasks(loop)
                if not t.done() and not t.cancelled()
            ]
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
        except Exception:
            pass


def _ensure_loop() -> asyncio.AbstractEventLoop:
    if _loop is None or not _loop.is_running():
        start_event_loop()
    return _loop


def submit_async(coro) -> asyncio.Future:
    """Schedule ``coro`` on the process event loop from any thread.

    Returns an asyncio.Future. Exceptions raised inside the coroutine are
    logged here so a failing task never goes silent.
    """
    loop = _ensure_loop()

    async def _wrapper():
        try:
            return await coro
        except Exception:
            logger.exception("async task failed")
            raise

    return asyncio.run_coroutine_threadsafe(_wrapper(), loop)


def call_later(seconds: float, coro_factory) -> None:
    """Schedule ``coro_factory()`` to run after ``seconds`` (thread-safe).

    ``coro_factory`` must be a zero-arg callable returning a coroutine.
    Used for the per-conversation batch debounce so a burst of messages
    collapses into one AI turn without blocking any request thread.
    """
    loop = _ensure_loop()

    def _scheduler():
        try:
            loop.call_later(
                seconds,
                lambda: asyncio.ensure_future(coro_factory()),
            )
        except Exception:
            logger.exception("call_later scheduling failed")

    loop.call_soon_threadsafe(_scheduler)


def schedule_repeating(interval: float, coro_factory) -> None:
    """Run ``coro_factory()`` every ``interval`` seconds on the loop."""
    loop = _ensure_loop()

    async def _runner():
        while True:
            try:
                await asyncio.sleep(interval)
                await coro_factory()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("repeating async task failed")

    def _spawn():
        _repeating_tasks.append(asyncio.create_task(_runner()))

    loop.call_soon_threadsafe(_spawn)


def submit_sync(fn, *args, **kwargs) -> "concurrent.futures.Future":
    """Run fast synchronous work on the persist thread pool (from any thread)."""
    return _get_pool().submit(fn, *args, **kwargs)


async def run_in_thread(fn, *args, **kwargs):
    """Run a small synchronous function inside the persist pool (from async code)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_get_pool(), lambda: fn(*args, **kwargs))