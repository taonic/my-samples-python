"""Follow-up forms that branch on the outcome and data of the form before them.

loan_application ── approved ──────────────────────────► loan_offer
                 │                                        ├─ accepted ───────► disbursement
                 │                                        └─ offer_declined ─► offer_feedback
                 └─ declined, affordability the only issue ─► affordability_review
"""

import copy
import json
import uuid
from typing import Any, Optional

import pytest
from temporalio.testing import WorkflowEnvironment

from progressive_data_resolution.core.model import ActivityResult
from progressive_data_resolution.core.resolver import resolve_requirements, select_next
from progressive_data_resolution.core.schema import SchemaError, parse_schema

from .conftest import App, FakeChecks, requirement_statuses, start_app, worker
from .test_rules_and_schema import SCHEMAS

ACTIVITIES = {
    "verify_identity",
    "assess_affordability",
    "fetch_credit_score",
    "prepare_offer",
    "assess_household_affordability",
    "schedule_disbursement",
}


def load(name: str) -> dict:
    return json.loads((SCHEMAS / f"{name}.v1.json").read_text())


APPLICATION = parse_schema(load("loan_application"), ACTIVITIES)
OFFER = parse_schema(load("loan_offer"), ACTIVITIES)
REVIEW = parse_schema(load("affordability_review"), ACTIVITIES)
DISBURSEMENT = parse_schema(load("disbursement"), ACTIVITIES)
FEEDBACK = parse_schema(load("offer_feedback"), ACTIVITIES)


def complete(schema, data: dict[str, Any], context=None, results_by_field=None):
    """Resolve with every runnable activity returning the given (or a default) result."""
    results: dict[str, dict[str, ActivityResult]] = {}
    for _ in range(len(schema.fields)):
        res = resolve_requirements(schema, data, results, {}, context)
        ready = [r for r in res.requirements if r.kind == "activity" and r.status == "missing"]
        if not ready:
            return res
        for r in ready:
            value = (results_by_field or {}).get(r.id, {"status": "passed", "band": "good"})
            results.setdefault(r.id, {})[r.fingerprint] = ActivityResult(
                r.fingerprint, f"x:{r.fingerprint}", 1, value
            )
    return resolve_requirements(schema, data, results, {}, context)


APPLICANT = {
    "applicant_name": "Ada Lovelace",
    "consent_to_checks": True,
    "employment_type": "employed",
    "annual_income": 20000,
    "employer_name": "Acme",
    "final_confirmation": True,
}
CONTEXT = {
    "application": {
        "employment_type": "employed",
        "annual_income": 60000,
        "affordability_assessment": {"status": "affordable", "max_loan": 240000},
    }
}


def next_form(schema, resolution, context=None) -> Optional[str]:
    nxt = select_next(schema, resolution, context)
    return nxt.schema_id if nxt else None


class TestBranchSelection:
    def test_application_branches_on_outcome_and_reason(self):
        def outcome_of(identity="passed", afford="affordable", band="good", confirm=True):
            return complete(
                APPLICATION,
                {**APPLICANT, "final_confirmation": confirm},
                results_by_field={
                    "identity_verification": {"status": identity},
                    "affordability_assessment": {"status": afford},
                    "credit_check": {"band": band},
                },
            )

        assert next_form(APPLICATION, outcome_of()) == "loan_offer"
        # Declined only for affordability: offered a review.
        res = outcome_of(afford="unaffordable")
        assert res.outcome == "declined"
        assert next_form(APPLICATION, res) == "affordability_review"
        # Declined for identity or credit, or withdrawn: nothing follows.
        assert next_form(APPLICATION, outcome_of(identity="failed", afford="unaffordable")) is None
        assert next_form(APPLICATION, outcome_of(band="poor", afford="unaffordable")) is None
        assert next_form(APPLICATION, outcome_of(confirm=False)) is None

    def test_offer_branches_on_acceptance(self):
        data = {"loan_amount": 5000, "term_months": "12", "repayment_account": "12345678",
                "hr_contact_email": "hr@x.example"}  # fmt: skip
        accepted = complete(OFFER, {**data, "accept_offer": True}, CONTEXT)
        declined = complete(OFFER, {**data, "accept_offer": False}, CONTEXT)
        assert (accepted.outcome, next_form(OFFER, accepted, CONTEXT)) == (
            "accepted",
            "disbursement",
        )
        assert (declined.outcome, next_form(OFFER, declined, CONTEXT)) == (
            "offer_declined",
            "offer_feedback",
        )


class TestBranchForms:
    def statuses(self, schema, data, context):
        res = resolve_requirements(schema, data, {}, {}, context)
        return {r.id: r.status for r in res.requirements}

    def test_review_activity_reads_the_application(self):
        res = resolve_requirements(
            REVIEW, {"other_income": 5000, "has_co_applicant": False}, {}, {}, CONTEXT
        )
        assessment = res.by_id()["household_assessment"]
        assert assessment.status == "missing"  # ready to run
        # Context dependencies are inputs like any other; inapplicable ones are left out.
        assert assessment.inputs == {"application.annual_income": 60000, "other_income": 5000}
        assert res.by_id()["co_applicant_income"].status == "not_applicable"

    def test_review_asks_self_employed_for_accounts(self):
        self_employed = {
            "application": {**CONTEXT["application"], "employment_type": "self_employed"}
        }
        assert self.statuses(REVIEW, {}, self_employed)["business_accounts_year"] == "missing"
        assert self.statuses(REVIEW, {}, CONTEXT)["business_accounts_year"] == "not_applicable"

    def test_disbursement_questions_depend_on_both_earlier_forms(self):
        context = {**CONTEXT, "offer": {"loan_amount": 5000, "repayment_account": "12345678"}}
        s = self.statuses(DISBURSEMENT, {"pay_to_repayment_account": False}, context)
        assert s["salary_day"] == "missing"  # employed, from application.*
        assert s["business_purpose"] == "not_applicable"
        assert s["payout_account"] == "missing"
        res = complete(DISBURSEMENT, {"pay_to_repayment_account": True, "salary_day": 25}, context)
        assert res.complete and res.outcome == "scheduled"

    @pytest.mark.parametrize(
        "reason, follow_up",
        [
            ("rate_too_high", "preferred_term"),
            ("amount_too_low", "amount_needed"),
            ("other", "comments"),
            ("found_elsewhere", None),
        ],
    )
    def test_feedback_follow_up_question_depends_on_reason(self, reason, follow_up):
        s = self.statuses(FEEDBACK, {"decline_reason": reason}, CONTEXT)
        asked = {f for f in ("preferred_term", "amount_needed", "comments") if s[f] == "missing"}
        assert asked == ({follow_up} if follow_up else set())


def test_depends_on_unknown_namespace_is_rejected():
    raw = copy.deepcopy(load("affordability_review"))
    raw["fields"]["household_assessment"]["dependsOn"].append("offer.loan_amount")
    with pytest.raises(SchemaError) as err:
        parse_schema(raw, ACTIVITIES)
    assert "unknown field 'offer.loan_amount'" in str(err.value)


# ---- End to end: walk each branch on a real server ----------------------------------

APPLY = {
    "applicant_name": "Ada Lovelace",
    "consent_to_checks": True,
    "employment_type": "employed",
    "annual_income": 60000,
    "employer_name": "Acme",
}
OFFER_ANSWERS = {
    "loan_amount": 50000,
    "term_months": "24",
    "repayment_account": "12345678",
    "hr_contact_email": "hr@acme.example",
}


@pytest.fixture
def task_queue() -> str:
    return f"pdr-branch-{uuid.uuid4()}"


async def reach_offer_decision(app: App, accept: bool) -> None:
    await app.approve()
    await app.submit(**OFFER_ANSWERS)
    await app.wait_for(lambda s: requirement_statuses(s).get("accept_offer") == "missing")
    await app.submit(accept_offer=accept)


async def test_accepted_offer_branches_to_disbursement(env: WorkflowEnvironment, task_queue):
    checks = FakeChecks()
    async with worker(env.client, task_queue, checks):
        app = await start_app(env.client, task_queue)
        await app.submit(**APPLY)
        await reach_offer_decision(app, accept=True)
        await app.wait_for(lambda s: s.schema_id == "disbursement" and bool(s.requirements))
        await app.submit(pay_to_repayment_account=True, salary_day=25)
        result = await app.handle.result()
        assert [p.outcome for p in result.phases] == ["approved", "accepted"]
        assert result.outcome == "scheduled"
        booking = result.results["disbursement"].value
        assert booking == {"reference": "DSB-1", "amount": 50000, "account": "12345678"}


async def test_declined_offer_branches_to_feedback(env: WorkflowEnvironment, task_queue):
    async with worker(env.client, task_queue, FakeChecks()):
        app = await start_app(env.client, task_queue)
        await app.submit(**APPLY)
        await reach_offer_decision(app, accept=False)
        state = await app.wait_for(
            lambda s: s.schema_id == "offer_feedback" and bool(s.requirements)
        )
        assert state.context["offer"]["accept_offer"] is False
        await app.submit(decline_reason="rate_too_high")
        assert requirement_statuses(await app.state())["preferred_term"] == "missing"
        await app.submit(preferred_term="12", contact_ok=True)
        result = await app.handle.result()
        assert [p.outcome for p in result.phases] == ["approved", "offer_declined"]
        assert result.outcome == "follow_up_requested"


async def test_unaffordable_application_branches_to_review(env: WorkflowEnvironment, task_queue):
    checks = FakeChecks()
    async with worker(env.client, task_queue, checks):
        app = await start_app(env.client, task_queue)
        await app.submit(**{**APPLY, "annual_income": 20000})
        await app.confirm()
        state = await app.wait_for(
            lambda s: s.schema_id == "affordability_review" and bool(s.requirements)
        )
        assert state.phases[0].outcome == "declined"
        await app.submit(other_income=5000, has_co_applicant=True)
        assert requirement_statuses(await app.state())["co_applicant_income"] == "missing"
        await app.submit(co_applicant_name="Charles Babbage", co_applicant_income=25000)
        result = await app.handle.result()
        # 20,000 from the application + 5,000 + 25,000 = 50,000 household income.
        assert result.results["household_assessment"].value["total"] == 50000
        assert result.outcome == "referred_to_underwriter"
        assert checks.calls["assess_household_affordability"] == 1
