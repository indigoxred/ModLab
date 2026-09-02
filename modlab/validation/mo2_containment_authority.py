"""Immutable authority records for MO2 containment capability decisions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .mo2_containment_model import (
    CapabilityDecision,
    CapabilityVerdict,
    DecisionBindings,
)


AUTHORITY_POLICY_VERSION = 1
HISTORICAL_CONTAINMENT_RUN_ID = (
    "containment-run:2c6220d4ceae48809653c18d5c74562b"
)
HISTORICAL_CONTAINMENT_DECISION_ID = (
    "containment-decision-sha256:"
    "141a46d8a8feb5ccb5ef2353563b3ec9f7aa1cede9f2dab55af23fb923b7a4b7"
)
HISTORICAL_CONTAINMENT_MECHANISM = (
    "isolated-low-integrity-junction-projection-v1"
)
HISTORICAL_RETIREMENT_REASON_CODES = (
    "inaccurate-cli-side-effect-reporting",
    "premature-adjudication-freezing-incomplete-evidence",
    "validated-outcome-candidate-substitution-before-pathname-publication",
)


class ContainmentAuthorityError(RuntimeError):
    """The immutable containment authority graph does not prove one safe leaf."""


@dataclass(frozen=True)
class CapabilityRetirement:
    schema_version: int
    authority_policy_version: int
    decision_id: str
    run_id: str
    mechanism: str
    status: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class CapabilityReview:
    schema_version: int
    authority_policy_version: int
    decision_id: str
    run_id: str
    mechanism: str
    scenario_result_ids: tuple[str, ...]
    bindings: DecisionBindings
    reviewer_id: str
    independent: bool
    status: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class CapabilitySupersession:
    schema_version: int
    authority_policy_version: int
    retirement_id: str
    retired_decision_id: str
    replacement_decision_id: str
    replacement_run_id: str
    replacement_mechanism: str
    replacement_scenario_result_ids: tuple[str, ...]
    replacement_bindings: DecisionBindings
    review_id: str


@dataclass(frozen=True)
class CapabilityEligibility:
    schema_version: int
    authority_policy_version: int
    decision_id: str
    run_id: str
    supersession_id: str
    review_id: str
    status: str


@dataclass(frozen=True)
class ResolvedCapabilityDecision:
    decision: CapabilityDecision
    decision_id: str
    retirement: CapabilityRetirement
    retirement_id: str
    supersession: CapabilitySupersession
    supersession_id: str
    review: CapabilityReview
    review_id: str
    eligibility: CapabilityEligibility
    eligibility_id: str


def resolve_current_eligible_decision(
    validation_root: Path,
) -> ResolvedCapabilityDecision:
    """Resolve the only current eligible leaf without caller-selected identity."""
    from .mo2_containment_store import ContainmentStore, ContainmentStoreError

    try:
        store = ContainmentStore(Path(validation_root))
        run_ids = set(store.list_run_ids())
        retirement_ids = store.list_retirement_ids()
        supersession_ids = store.list_supersession_ids()
        review_ids = store.list_review_ids()
        eligibility_ids = store.list_eligibility_ids()
        retirements = {
            identifier: store.load_retirement(identifier)
            for identifier in retirement_ids
        }
        supersessions = {
            identifier: store.load_supersession(identifier)
            for identifier in supersession_ids
        }
        reviews = {
            identifier: store.load_review(identifier) for identifier in review_ids
        }
        eligibilities = {
            identifier: store.load_eligibility(identifier)
            for identifier in eligibility_ids
        }
    except ContainmentStoreError as error:
        raise ContainmentAuthorityError(
            f"containment authority graph is malformed or redirected: {error}"
        ) from error

    if not eligibilities:
        if retirements:
            raise ContainmentAuthorityError(
                "no current eligible decision: retired authority requires a "
                "supersession, passing independent review, and eligibility record"
            )
        raise ContainmentAuthorityError(
            "no current eligible decision: found zero eligibility records"
        )
    if len(eligibilities) != 1:
        raise ContainmentAuthorityError(
            "multiple current eligibility records make containment authority ambiguous"
        )

    eligibility_id, eligibility = next(iter(eligibilities.items()))
    if (
        eligibility.decision_id == HISTORICAL_CONTAINMENT_DECISION_ID
        or eligibility.run_id == HISTORICAL_CONTAINMENT_RUN_ID
    ):
        raise ContainmentAuthorityError(
            "the historical containment run and decision are retired"
        )
    supersession = supersessions.get(eligibility.supersession_id)
    if supersession is None:
        raise ContainmentAuthorityError(
            "eligibility references a missing supersession record"
        )
    review = reviews.get(eligibility.review_id)
    if review is None:
        raise ContainmentAuthorityError(
            "eligibility references a missing independent review record"
        )
    retirement = retirements.get(supersession.retirement_id)
    if retirement is None:
        raise ContainmentAuthorityError(
            "supersession references a missing retirement record"
        )

    if supersession.review_id != eligibility.review_id:
        raise ContainmentAuthorityError(
            "eligibility and supersession review bindings differ"
        )
    if supersession.retired_decision_id != retirement.decision_id:
        raise ContainmentAuthorityError(
            "supersession retired-decision binding differs from retirement"
        )
    if supersession.replacement_decision_id != eligibility.decision_id:
        raise ContainmentAuthorityError(
            "eligibility and supersession replacement decision bindings differ"
        )
    if supersession.replacement_run_id != eligibility.run_id:
        raise ContainmentAuthorityError(
            "eligibility and supersession replacement run bindings differ"
        )
    if review.decision_id != eligibility.decision_id:
        raise ContainmentAuthorityError(
            "review decision binding differs from eligibility decision"
        )
    if review.run_id != eligibility.run_id:
        raise ContainmentAuthorityError(
            "review run binding differs from eligibility decision"
        )

    retirement_groups: dict[str, list[str]] = {}
    for identifier, value in retirements.items():
        retirement_groups.setdefault(value.decision_id, []).append(identifier)
    if any(len(identifiers) != 1 for identifiers in retirement_groups.values()):
        raise ContainmentAuthorityError(
            "conflicting retirement records make containment authority ambiguous"
        )
    if eligibility.decision_id in retirement_groups:
        raise ContainmentAuthorityError(
            "the otherwise eligible containment decision is retired"
        )

    matching_reviews = [
        identifier
        for identifier, value in reviews.items()
        if value.decision_id == eligibility.decision_id
    ]
    if len(matching_reviews) != 1:
        raise ContainmentAuthorityError(
            "ambiguous review records leave zero eligible containment decisions"
        )
    if matching_reviews[0] != eligibility.review_id:
        raise ContainmentAuthorityError(
            "eligibility does not bind the unique review for its decision"
        )

    matching_supersessions = [
        identifier
        for identifier, value in supersessions.items()
        if value.replacement_decision_id == eligibility.decision_id
    ]
    if len(matching_supersessions) != 1:
        raise ContainmentAuthorityError(
            "conflicting supersession records make containment authority ambiguous"
        )
    if matching_supersessions[0] != eligibility.supersession_id:
        raise ContainmentAuthorityError(
            "eligibility does not bind the unique supersession for its decision"
        )

    if review.status != "Passed":
        raise ContainmentAuthorityError(
            "independent review failed; zero decisions are eligible"
        )
    if not review.independent:
        raise ContainmentAuthorityError(
            "a passing independent review is required for eligibility"
        )

    if retirement.run_id not in run_ids:
        raise ContainmentAuthorityError(
            "retirement references a missing or non-direct historical run"
        )
    if eligibility.run_id not in run_ids:
        raise ContainmentAuthorityError(
            "eligibility references a missing or non-direct replacement run"
        )
    try:
        retired_decision = store.load_decision(
            retirement.run_id,
            retirement.decision_id,
        )
        decision = store.load_decision(
            eligibility.run_id,
            eligibility.decision_id,
        )
    except ContainmentStoreError as error:
        raise ContainmentAuthorityError(
            f"decision or binding evidence is invalid: {error}"
        ) from error

    if (
        retired_decision.run_id != retirement.run_id
        or retired_decision.mechanism != retirement.mechanism
    ):
        raise ContainmentAuthorityError(
            "retirement does not bind the exact historical decision"
        )
    if decision.schema_version != 2 or decision.bindings is None:
        raise ContainmentAuthorityError(
            "eligible replacement must be a source-bound version-2 decision"
        )
    if decision.verdict is not CapabilityVerdict.SUPPORTED:
        raise ContainmentAuthorityError(
            "eligible replacement decision must have a Supported verdict"
        )
    if (
        supersession.replacement_run_id != decision.run_id
        or supersession.replacement_mechanism != decision.mechanism
        or supersession.replacement_scenario_result_ids
        != decision.scenario_result_ids
        or supersession.replacement_bindings != decision.bindings
    ):
        raise ContainmentAuthorityError(
            "supersession source, version, result, or mechanism binding differs "
            "from replacement decision"
        )
    if (
        review.run_id != decision.run_id
        or review.mechanism != decision.mechanism
        or review.scenario_result_ids != decision.scenario_result_ids
        or review.bindings != decision.bindings
    ):
        raise ContainmentAuthorityError(
            "review source, version, result, or mechanism binding differs from decision"
        )

    return ResolvedCapabilityDecision(
        decision=decision,
        decision_id=eligibility.decision_id,
        retirement=retirement,
        retirement_id=supersession.retirement_id,
        supersession=supersession,
        supersession_id=eligibility.supersession_id,
        review=review,
        review_id=eligibility.review_id,
        eligibility=eligibility,
        eligibility_id=eligibility_id,
    )


__all__ = [
    "AUTHORITY_POLICY_VERSION",
    "HISTORICAL_CONTAINMENT_RUN_ID",
    "HISTORICAL_CONTAINMENT_DECISION_ID",
    "HISTORICAL_CONTAINMENT_MECHANISM",
    "HISTORICAL_RETIREMENT_REASON_CODES",
    "CapabilityRetirement",
    "CapabilityReview",
    "CapabilitySupersession",
    "CapabilityEligibility",
    "ResolvedCapabilityDecision",
    "ContainmentAuthorityError",
    "resolve_current_eligible_decision",
]
