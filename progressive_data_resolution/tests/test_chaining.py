"""A completed form continuing into a follow-up form shaped by its data."""

import copy
import json
import uuid

import pytest
from temporalio.client import WorkflowUpdateFailedError
from temporalio.testing import WorkflowEnvironment

from progressive_data_resolution.core.model import ResolutionState
from progressive_data_resolution.core.resolver import resolve_requirements, select_next
from progressive_data_resolution.core.schema import SchemaError, check_context, parse_schema
from progressive_data_resolution.core.submission import check_submission
from progressive_data_resolution.core.surface import render_surface

from .conftest import FakeChecks, requirement_statuses, start_app, worker
from .test_resolver_and_planner import EMPLOYED, complete_activities
from .test_rules_and_schema import ACTIVITIES, SCHEMAS

ALL_ACTIVITIES = ACTIVITIES | {"prepare_offer"}


def load(name: str) -> dict:
    return json.loads((SCHEMAS / f"{name}.json").read_text())


APPLICATION_SCHEMA = parse_schema(load("loan_application.v1"), ALL_ACTIVITIES)
OFFER = parse_schema(load("loan_offer.v1"), ALL_ACTIVITIES)

EMPLOYED_CONTEXT = {
    "application": {
        "employment_type": "employed",
        "annual_income": 60000,
        "affordability_assessment": {"status": "affordable", "max_loan": 240000},
    }
}
SELF_EMPLOYED_CONTEXT = {
    "application": {
        **EMPLOYED_CONTEXT["application"],
        "employment_type": "self_employed",
    }
}


class TestSelectNext:
    def complete_application(self, value=None):
        data = {**EMPLOYED, "final_confirmation": True}
        results = complete_activities(data, schema=APPLICATION_SCHEMA, value=value)
        res = resolve_requirements(APPLICATION_SCHEMA, data, results, {})
        assert res.complete
        return res

    def test_approved_application_continues_to_offer(self):
        nxt = select_next(APPLICATION_SCHEMA, self.complete_application())
        assert nxt is not None
        assert (nxt.schema_id, nxt.version, nxt.as_namespace) == ("loan_offer", "1", "application")

    def test_declined_application_ends(self):
        declined = self.complete_application(value={"status": "failed"})
        assert declined.outcome == "declined"
        assert select_next(APPLICATION_SCHEMA, declined) is None

    def test_incomplete_form_never_continues(self):
        assert (
            select_next(
                APPLICATION_SCHEMA, resolve_requirements(APPLICATION_SCHEMA, EMPLOYED, {}, {})
            )
            is None
        )


class TestFollowUpForm:
    def statuses(self, context, data=None):
        res = resolve_requirements(OFFER, data or {}, {}, {}, context)
        return {r.id: r.status for r in res.requirements}

    def test_fields_follow_application_employment_type(self):
        employed = self.statuses(EMPLOYED_CONTEXT)
        assert employed["hr_contact_email"] == "missing"
        assert employed["accountant_contact_email"] == "not_applicable"
        self_employed = self.statuses(SELF_EMPLOYED_CONTEXT)
        assert self_employed["hr_contact_email"] == "not_applicable"
        assert self_employed["accountant_contact_email"] == "missing"

    def test_loan_amount_is_capped_by_affordability(self):
        def check(amount):
            return check_submission(
                OFFER, {}, {}, {}, {}, 0, {"loan_amount": amount}, EMPLOYED_CONTEXT
            )

        assert check(240000).ok
        assert check(240001).invalid == {"loan_amount": "must be at most 240000"}
        assert check(500).invalid == {"loan_amount": "must be at least 1000"}

    def test_surface_shows_resolved_bound(self):
        res = resolve_requirements(OFFER, {}, {}, {}, EMPLOYED_CONTEXT)
        state = ResolutionState(
            schema_id="loan_offer",
            schema_version="1",
            status="waiting_for_input",
            revision=0,
            schema=OFFER,
            requirements=res.requirements,
            context=EMPLOYED_CONTEXT,
        )
        surface = render_surface(state)
        amount = next(c for c in surface.components if c.field == "loan_amount")
        assert amount.constraints == {"min": 1000, "max": 240000}
        assert surface.phase == "loan_offer v1" and surface.step == 1

    def test_missing_context_is_reported(self):
        assert check_context(OFFER, {}) == [
            "context 'application' was not provided",
            "fields.loan_amount: 'application.affordability_assessment.max_loan' "
            "is not a number in the context",
        ]
        assert check_context(OFFER, EMPLOYED_CONTEXT) == []


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (
            lambda s: s["fields"]["loan_amount"].update(max={"from": "loan_amount"}),
            "must start with a declared context namespace",
        ),
        (lambda s: s.update(context=["offer"]), "distinct from every field"),
        (
            lambda s: s["fields"]["hr_contact_email"].update(
                requiredWhen={"field": "application", "equals": 1}
            ),
            "must be 'application.field'",
        ),
        (
            lambda s: s.update(
                next=[
                    {
                        "schema": "x",
                        "version": "1",
                        "as": "a",
                        "when": {"field": "nope", "equals": 1},
                    }
                ]
            ),
            "unknown field 'nope'",
        ),
        (lambda s: s["fields"].update(outcome=s["fields"].pop("offer")), "'outcome' is reserved"),
    ],
)
def test_invalid_chaining_schemas_are_rejected(mutate, expected):
    raw = copy.deepcopy(load("loan_offer.v1"))
    mutate(raw)
    with pytest.raises(SchemaError) as err:
        parse_schema(raw, ALL_ACTIVITIES)
    assert any(expected in e for e in err.value.errors), err.value.errors


# ---- End to end ------------------------------------------------------------------

APPLICATION = {
    "applicant_name": "Ada Lovelace",
    "consent_to_checks": True,
    "employment_type": "employed",
    "annual_income": 60000,
    "employer_name": "Acme",
}


@pytest.fixture
def task_queue() -> str:
    return f"pdr-chain-{uuid.uuid4()}"


async def test_approved_application_continues_into_offer_form(env: WorkflowEnvironment, task_queue):
    checks = FakeChecks()
    async with worker(env.client, task_queue, checks):
        app = await start_app(env.client, task_queue)
        await app.submit(**APPLICATION)
        await app.wait_for(lambda s: requirement_statuses(s).get("final_confirmation") == "missing")
        await app.submit(final_confirmation=True)

        # Same workflow id, new run, new form, shaped by the application.
        state = await app.wait_for(lambda s: s.schema_id == "loan_offer" and bool(s.requirements))
        assert [(p.schema_id, p.schema_version, p.outcome) for p in state.phases] == [
            ("loan_application", "1", "approved")
        ]
        assert state.context["application"]["employer_name"] == "Acme"
        assert state.context["application"]["affordability_assessment"]["max_loan"] == 240000
        statuses = requirement_statuses(state)
        assert statuses["hr_contact_email"] == "missing"
        assert statuses["accountant_contact_email"] == "not_applicable"
        assert state.revision == 0

        with pytest.raises(WorkflowUpdateFailedError) as err:
            await app.submit(loan_amount=300000)
        assert err.value.cause.details[0]["errors"] == {"loan_amount": "must be at most 240000"}

        await app.submit(
            loan_amount=120000,
            term_months="36",
            repayment_account="12345678",
            hr_contact_email="hr@acme.example",
        )
        await app.wait_for(lambda s: requirement_statuses(s)["accept_offer"] == "missing")
        await app.submit(accept_offer=True)

        # Accepting branches on into disbursement, which sees both earlier forms.
        state = await app.wait_for(lambda s: s.schema_id == "disbursement" and bool(s.requirements))
        assert [p.outcome for p in state.phases] == ["approved", "accepted"]
        assert state.context["offer"]["loan_amount"] == 120000
        assert checks.calls["prepare_offer"] == 1


async def test_declined_application_does_not_continue(env: WorkflowEnvironment, task_queue):
    async with worker(env.client, task_queue, FakeChecks()):
        app = await start_app(env.client, task_queue)
        # Failed identity: declined, and no `next` entry matches.
        await app.submit(**{**APPLICATION, "applicant_name": "Some Fraud"})
        await app.wait_for(lambda s: requirement_statuses(s).get("final_confirmation") == "missing")
        await app.submit(final_confirmation=True)
        result = await app.handle.result()
        assert result.status == "completed" and result.outcome == "declined"
        assert result.phases == []
        assert (await app.state()).schema_id == "loan_application"
