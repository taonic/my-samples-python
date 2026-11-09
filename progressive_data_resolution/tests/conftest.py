import asyncio
import shutil
import uuid
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

import pytest
from temporalio import activity
from temporalio.client import Client, WorkflowHandle
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from progressive_data_resolution.activities import load_schema
from progressive_data_resolution.core.model import ResolutionState
from progressive_data_resolution.workflow import (
    ProgressiveDataResolutionWorkflow,
    Submission,
    SubmissionResult,
    WorkflowInput,
)


@pytest.fixture(scope="session")
async def env() -> AsyncIterator[WorkflowEnvironment]:
    # Use an installed Temporal CLI if there is one, otherwise download it.
    env = await WorkflowEnvironment.start_local(dev_server_existing_path=shutil.which("temporal"))
    yield env
    await env.shutdown()


class FakeChecks:
    """Stand-ins for the mock activities that tests can count, hold and break."""

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()
        self.held: set[str] = set()
        self.waiting: dict[str, list[tuple[dict[str, Any], asyncio.Event]]] = defaultdict(list)
        self.failing: set[str] = set()

    async def _run(
        self, name: str, inputs: dict[str, Any], value: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls[name] += 1
        if name in self.held:
            released = asyncio.Event()
            self.waiting[name].append((inputs, released))
            await released.wait()
        if name in self.failing:
            raise ApplicationError(f"{name} unavailable")
        return value

    def activities(self) -> list[Callable]:
        @activity.defn(name="verify_identity")
        async def verify_identity(inputs: dict[str, Any]) -> dict[str, Any]:
            fraud = "fraud" in inputs["applicant_name"].lower()
            value = {"status": "failed" if fraud else "passed"}
            return await self._run("verify_identity", inputs, value)

        @activity.defn(name="assess_affordability")
        async def assess_affordability(inputs: dict[str, Any]) -> dict[str, Any]:
            ok = inputs["annual_income"] >= 30000
            value = {
                "status": "affordable" if ok else "unaffordable",
                "income": inputs["annual_income"],
                "max_loan": inputs["annual_income"] * 4 if ok else 0,
            }
            return await self._run("assess_affordability", inputs, value)

        @activity.defn(name="fetch_credit_score")
        async def fetch_credit_score(inputs: dict[str, Any]) -> dict[str, Any]:
            return await self._run("fetch_credit_score", inputs, {"score": 700, "band": "good"})

        @activity.defn(name="prepare_offer")
        async def prepare_offer(inputs: dict[str, Any]) -> dict[str, Any]:
            value = {"apr": 6.65, "monthly_payment": inputs["loan_amount"] / 30}
            return await self._run("prepare_offer", inputs, value)

        @activity.defn(name="assess_household_affordability")
        async def assess_household_affordability(inputs: dict[str, Any]) -> dict[str, Any]:
            total = (
                inputs["application.annual_income"]
                + inputs["other_income"]
                + inputs.get("co_applicant_income", 0)
            )
            value = {"status": "affordable" if total >= 40000 else "unaffordable", "total": total}
            return await self._run("assess_household_affordability", inputs, value)

        @activity.defn(name="schedule_disbursement")
        async def schedule_disbursement(inputs: dict[str, Any]) -> dict[str, Any]:
            account = inputs.get("payout_account") or inputs["offer.repayment_account"]
            value = {
                "reference": "DSB-1",
                "amount": inputs["offer.loan_amount"],
                "account": account,
            }
            return await self._run("schedule_disbursement", inputs, value)

        return [
            load_schema,
            verify_identity,
            assess_affordability,
            fetch_credit_score,
            prepare_offer,
            assess_household_affordability,
            schedule_disbursement,
        ]


class App:
    """Thin test client around one workflow execution."""

    def __init__(self, handle: WorkflowHandle, first_run_id: str | None = None) -> None:
        self.handle = handle
        self.first_run_id = first_run_id

    async def state(self) -> ResolutionState:
        return await self.handle.query(ProgressiveDataResolutionWorkflow.get_state)

    async def submit(
        self, base_revision: int | None = None, submission_id: str | None = None,
        update_id: str | None = None, **values: Any,
    ) -> SubmissionResult:  # fmt: skip
        if base_revision is None:
            base_revision = (await self.state()).revision
        submission_id = submission_id or str(uuid.uuid4())
        return await self.handle.execute_update(
            ProgressiveDataResolutionWorkflow.submit,
            Submission(submission_id=submission_id, base_revision=base_revision, values=values),
            id=update_id or submission_id,
        )

    async def confirm(self) -> None:
        """Tick the final confirmation, which appears once the checks are back."""
        await self.wait_for(
            lambda s: requirement_statuses(s).get("final_confirmation") == "missing"
        )
        await self.submit(final_confirmation=True)

    async def approve(self) -> ResolutionState:
        """Confirm, then follow the approved application into the loan offer form."""
        await self.confirm()
        return await self.wait_for(lambda s: s.schema_id == "loan_offer" and bool(s.requirements))

    async def wait_for(
        self, predicate: Callable[[ResolutionState], bool], timeout: float = 15
    ) -> ResolutionState:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            state = await self.state()
            if predicate(state):
                return state
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f"condition not met; last state: {state}")
            await asyncio.sleep(0.1)


@asynccontextmanager
async def worker(client: Client, task_queue: str, checks: FakeChecks) -> AsyncIterator[Worker]:
    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[ProgressiveDataResolutionWorkflow],
        activities=checks.activities(),
    ) as w:
        yield w


async def start_app(client: Client, task_queue: str, version: str = "1", **kwargs: Any) -> App:
    handle = await client.start_workflow(
        ProgressiveDataResolutionWorkflow.run,
        WorkflowInput(schema_id="loan_application", schema_version=version, **kwargs),
        id=f"test-{uuid.uuid4()}",
        task_queue=task_queue,
    )
    # Address the workflow by id alone so queries and updates reach the latest
    # run, following any continue-as-new into a follow-up form.
    return App(
        client.get_workflow_handle_for(ProgressiveDataResolutionWorkflow.run, handle.id),
        first_run_id=handle.first_execution_run_id,
    )


async def wait_for_calls(checks: FakeChecks, name: str, count: int, timeout: float = 10) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while len(checks.waiting[name]) < count:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(
                f"{name}: expected {count} held calls, got {len(checks.waiting[name])}"
            )
        await asyncio.sleep(0.05)


def requirement_statuses(state: ResolutionState) -> dict[str, str]:
    return {r.id: r.status for r in state.requirements}
