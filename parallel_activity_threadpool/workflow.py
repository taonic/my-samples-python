import asyncio
from datetime import timedelta
from typing import List

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from .activities import fetch_item


@workflow.defn
class ParallelActivityWorkflow:
    @workflow.run
    def run(self, items: List[str]) -> List[str]:
        # Each item is dispatched as a separate activity. The activities run
        # in parallel inside the worker's thread pool; the workflow simply
        # awaits all of their handles together.
        #
        # Note: `asyncio.gather` here only awaits the activity *handles* — the
        # actual parallelism happens in the worker's ThreadPoolExecutor, not on
        # the workflow's event loop. The workflow code itself stays
        # deterministic and single-threaded.
        #]
        # results = await asyncio.gather(*handles)
        return list()
