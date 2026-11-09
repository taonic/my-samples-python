import uuid

import pytest
from temporalio.client import WorkflowFailureError, WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError, CancelledError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer

from progressive_data_resolution.workflow import ProgressiveDataResolutionWorkflow, RetryActionInput

from .conftest import FakeChecks, requirement_statuses, start_app, wait_for_calls, worker

APPLICANT = {"applicant_name": "Ada Lovelace", "consent_to_checks": True}
SELF_EMPLOYED = {
    **APPLICANT,
    "employment_type": "self_employed",
    "annual_income": 90000,
    "business_registration": "BR-123456",
    "business_operating_years": 4,
}


@pytest.fixture
def task_queue() -> str:
    return f"pdr-test-{uuid.uuid4()}"


@pytest.fixture
def checks() -> FakeChecks:
    return FakeChecks()


async def test_startup_publishes_initial_requirements(env: WorkflowEnvironment, task_queue, checks):
    async with worker(env.client, task_queue, checks):
        app = await start_app(env.client, task_queue)
        state = await app.wait_for(lambda s: bool(s.requirements))
        statuses = requirement_statuses(state)
        assert statuses["employment_type"] == "missing"
        assert statuses["business_registration"] == "blocked"
        assert state.status == "waiting_for_input"
        assert state.progress == "waiting_for_human"
        assert state.schema and state.schema.version == "1"
        assert "collect:applicant_name" in {a.id for a in state.actions}
        # Declaration order survives the round trip through Temporal's converter.
        assert [r.id for r in state.requirements][:3] == [
            "applicant_name",
            "consent_to_checks",
            "employment_type",
        ]


async def test_update_reveals_conditional_requirements(
    env: WorkflowEnvironment, task_queue, checks
):
    async with worker(env.client, task_queue, checks):
        app = await start_app(env.client, task_queue)
        result = await app.submit(employment_type="self_employed")
        assert result.revision == 1 and result.changed_fields == ["employment_type"]
        statuses = requirement_statuses(await app.state())
        assert statuses["business_registration"] == "missing"
        assert statuses["employer_name"] == "not_applicable"


async def test_self_employed_path_completes(env: WorkflowEnvironment, task_queue, checks):
    async with worker(env.client, task_queue, checks):
        app = await start_app(env.client, task_queue)
        await app.submit(**APPLICANT)
        await app.submit(employment_type="self_employed", annual_income=90000)
        await app.submit(business_registration="BR-123456", business_operating_years=4)
        state = await app.approve()
        assert state.phases[0].outcome == "approved"
        application = state.context["application"]
        assert application["business_registration"] == "BR-123456"
        assert application["credit_check"]["band"] == "good"
        assert checks.calls == {
            "verify_identity": 1,
            "assess_affordability": 1,
            "fetch_credit_score": 1,
        }


async def test_employed_path_differs_without_code_change(
    env: WorkflowEnvironment, task_queue, checks
):
    async with worker(env.client, task_queue, checks):
        app = await start_app(env.client, task_queue)
        await app.submit(**APPLICANT, employment_type="employed", annual_income=20000)
        state = await app.state()
        assert requirement_statuses(state)["employer_name"] == "missing"
        await app.submit(employer_name="Acme")
        await app.confirm()
        # Unaffordable on 20k: declined, which branches to the affordability review.
        state = await app.wait_for(lambda s: s.schema_id == "affordability_review")
        assert state.phases[0].outcome == "declined"
        assert "business_registration" not in state.context["application"]


async def test_independent_activities_run_concurrently(
    env: WorkflowEnvironment, task_queue, checks
):
    checks.held = {"verify_identity", "assess_affordability"}
    async with worker(env.client, task_queue, checks):
        app = await start_app(env.client, task_queue)
        await app.submit(**APPLICANT, employment_type="employed", annual_income=60000)
        # Both checks are in flight at once, while a human field is still missing.
        await wait_for_calls(checks, "verify_identity", 1)
        await wait_for_calls(checks, "assess_affordability", 1)
        state = await app.state()
        assert state.status == "running"
        assert requirement_statuses(state)["employer_name"] == "missing"
        for name in checks.held:
            for _, released in checks.waiting[name]:
                released.set()
        await app.submit(employer_name="Acme")
        assert (await app.approve()).phases[0].outcome == "approved"


async def test_correction_reconciles_and_stale_result_is_ignored(
    env: WorkflowEnvironment, task_queue, checks
):
    checks.held = {"assess_affordability"}
    async with worker(env.client, task_queue, checks):
        app = await start_app(env.client, task_queue)
        await app.submit(**SELF_EMPLOYED)
        await wait_for_calls(checks, "assess_affordability", 1)

        # Switch to employed while the self-employed assessment is still running.
        await app.submit(employment_type="employed", employer_name="Acme")
        await wait_for_calls(checks, "assess_affordability", 2)
        state = await app.state()
        statuses = requirement_statuses(state)
        assert statuses["business_registration"] == "not_applicable"
        assert set(state.retained) == {"business_registration", "business_operating_years"}
        assert "business_registration" not in state.data

        # The first (stale) result arrives first and must not satisfy anything.
        old_inputs, old_release = checks.waiting["assess_affordability"][0]
        assert old_inputs["employment_type"] == "self_employed"
        old_release.set()
        state = await app.wait_for(
            lambda s: any(a.stale and a.status == "completed" for a in s.actions)
        )
        assert requirement_statuses(state)["affordability_assessment"] == "in_progress"
        assert state.status == "running"

        new_inputs, new_release = checks.waiting["assess_affordability"][1]
        assert new_inputs == {"employment_type": "employed", "annual_income": 90000}
        new_release.set()
        state = await app.wait_for(
            lambda s: requirement_statuses(s).get("final_confirmation") == "missing"
        )
        stale = [a for a in state.actions if a.stale]
        assert len(stale) == 1
        # Both results are kept for audit; only the fresh one satisfies the requirement.
        assert len(state.results["affordability_assessment"]) == 2
        afford = next(r for r in state.requirements if r.id == "affordability_assessment")
        assert afford.status == "satisfied" and stale[0].id not in afford.reason


async def test_duplicate_submissions_apply_once(env: WorkflowEnvironment, task_queue, checks):
    async with worker(env.client, task_queue, checks):
        app = await start_app(env.client, task_queue)
        first = await app.submit(submission_id="sub-1", applicant_name="Ada")
        # Same update id: deduplicated by Temporal itself.
        again = await app.submit(submission_id="sub-1", applicant_name="Ada")
        # Same submission id under a different update id: deduplicated by the workflow.
        other = await app.submit(submission_id="sub-1", update_id="retry-2", applicant_name="Ada")
        assert first.revision == again.revision == other.revision == 1
        assert other.duplicate
        assert (await app.state()).revision == 1


async def test_invalid_and_stale_submissions_are_rejected(
    env: WorkflowEnvironment, task_queue, checks
):
    async with worker(env.client, task_queue, checks):
        app = await start_app(env.client, task_queue)
        with pytest.raises(WorkflowUpdateFailedError) as err:
            await app.submit(annual_income="lots", business_registration="nope")
        assert isinstance(err.value.cause, ApplicationError)
        assert err.value.cause.type == "InvalidSubmission"
        assert set(err.value.cause.details[0]["errors"]) == {
            "annual_income",
            "business_registration",
        }

        await app.submit(base_revision=0, employment_type="self_employed")
        # A second tab still at revision 0 tries to change the same field: conflict.
        with pytest.raises(WorkflowUpdateFailedError) as err:
            await app.submit(base_revision=0, employment_type="employed")
        assert err.value.cause.type == "StaleSubmission"
        assert err.value.cause.details[0]["revision"] == 1
        # ...but an untouched field from the same old revision is fine.
        assert (await app.submit(base_revision=0, annual_income=1000)).revision == 2


async def test_consent_refusal_is_detected_as_impossible(
    env: WorkflowEnvironment, task_queue, checks
):
    async with worker(env.client, task_queue, checks):
        app = await start_app(env.client, task_queue)
        await app.submit(**{**APPLICANT, "consent_to_checks": False})
        state = await app.state()
        assert state.progress == "impossible"
        assert state.status == "waiting_for_input"
        assert checks.calls["verify_identity"] == 0
        # Correcting the answer unblocks the check without any other intervention.
        await app.submit(consent_to_checks=True)
        await app.wait_for(
            lambda s: requirement_statuses(s)["identity_verification"] == "satisfied"
        )


async def test_exhausted_activity_fails_visibly_then_retries(
    env: WorkflowEnvironment, task_queue, checks
):
    checks.failing = {"verify_identity"}
    async with worker(env.client, task_queue, checks):
        app = await start_app(env.client, task_queue)
        await app.submit(
            **APPLICANT, employment_type="employed", annual_income=60000, employer_name="Acme"
        )
        state = await app.wait_for(lambda s: s.progress == "action_failed", timeout=20)
        failed = [a for a in state.actions if a.status == "failed"]
        assert len(failed) == 1 and "unavailable" in (failed[0].error or "")
        assert requirement_statuses(state)["identity_verification"] == "failed"
        assert checks.calls["verify_identity"] == 3  # the retry policy's maximum

        checks.failing.clear()
        await app.handle.execute_update(
            ProgressiveDataResolutionWorkflow.retry_action, RetryActionInput(failed[0].id)
        )
        assert (await app.approve()).phases[0].outcome == "approved"


async def test_worker_restart_recovers_without_duplicate_work(
    env: WorkflowEnvironment, task_queue, checks
):
    async with worker(env.client, task_queue, checks):
        app = await start_app(env.client, task_queue)
        await app.submit(**APPLICANT)
        await app.wait_for(
            lambda s: requirement_statuses(s)["identity_verification"] == "satisfied"
        )
        before = await app.state()

    # A brand-new worker has an empty cache, so it must rebuild state by replay.
    async with worker(env.client, task_queue, checks):
        after = await app.state()
        assert after.data == before.data and after.revision == before.revision
        await app.submit(employment_type="employed", annual_income=60000, employer_name="Acme")
        state = await app.approve()

    assert state.phases[0].outcome == "approved"
    assert state.context["application"]["applicant_name"] == "Ada Lovelace"
    assert checks.calls == {
        "verify_identity": 1,
        "assess_affordability": 1,
        "fetch_credit_score": 1,
    }

    # And the application run's history replays cleanly against the workflow code.
    first_run = env.client.get_workflow_handle(app.handle.id, run_id=app.first_run_id)
    history = await first_run.fetch_history()
    await Replayer(workflows=[ProgressiveDataResolutionWorkflow]).replay_workflow(history)


async def test_inactivity_timeout_cancels(env: WorkflowEnvironment, task_queue, checks):
    async with worker(env.client, task_queue, checks):
        app = await start_app(env.client, task_queue, inactivity_timeout_seconds=1)
        result = await app.handle.result()
        assert result.status == "cancelled" and result.reason == "inactivity_timeout"


async def test_cancellation_and_unknown_schema(env: WorkflowEnvironment, task_queue, checks):
    async with worker(env.client, task_queue, checks):
        app = await start_app(env.client, task_queue)
        await app.wait_for(lambda s: bool(s.requirements))
        await app.handle.cancel()
        with pytest.raises(WorkflowFailureError) as err:
            await app.handle.result()
        assert isinstance(err.value.cause, CancelledError)

        missing = await start_app(env.client, task_queue, version="9")
        with pytest.raises(WorkflowFailureError) as err:
            await missing.handle.result()
        assert "schema could not be loaded" in str(err.value.cause)
