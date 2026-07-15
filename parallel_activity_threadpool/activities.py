import time

from temporalio import activity

# These activities are intentionally *synchronous* (plain `def`, not `async def`).
# Synchronous activities are executed by the worker in a thread pool
# (a `concurrent.futures.ThreadPoolExecutor`) rather than on the asyncio event
# loop. This is the right choice for blocking, CPU-light-but-IO-bound work such
# as blocking network/database clients that have no async equivalent.


@activity.defn
def fetch_item(item: str) -> str:
    """Pretend to fetch a single item using a blocking client.

    `time.sleep` blocks the calling thread. Because each activity runs in its own
    thread from the worker's executor, many of these can run truly in parallel.
    """
    activity.logger.info("Fetching %s", item)
    time.sleep(1)  # blocking IO simulated with a blocking sleep
    return f"fetched:{item}"
