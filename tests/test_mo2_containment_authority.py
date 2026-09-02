import inspect
import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from modlab.validation.mo2_containment_authority import (
    HISTORICAL_CONTAINMENT_DECISION_ID,
    HISTORICAL_CONTAINMENT_MECHANISM,
    HISTORICAL_CONTAINMENT_RUN_ID,
    HISTORICAL_RETIREMENT_REASON_CODES,
    CapabilityEligibility,
    CapabilityRetirement,
    CapabilityReview,
    CapabilitySupersession,
    ContainmentAuthorityError,
    resolve_current_eligible_decision,
)
from modlab.validation.mo2_containment_model import (
    CapabilityDecision,
    CapabilityVerdict,
    ContainmentScenario,
    DecisionBindings,
)
from modlab.validation.mo2_containment_serialization import (
    scenario_result_id_for,
    source_artifact_id_for,
    watch_outcome_id_for,
)
from modlab.validation.mo2_containment_store import (
    ContainmentStore,
    ContainmentStoreError,
)
from tests.test_mo2_containment_store import valid_scenario_result


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


class AuthorityFixture:
    def __init__(self, root: Path, slot: int = 0) -> None:
        self.root = root
        self.store = ContainmentStore(root)
        digits = ("1", "2", "3", "4") if slot == 0 else ("5", "6", "7", "8")
        self.historical_run_id = "containment-run:" + digits[0] * 32
        self.replacement_run_id = "containment-run:" + digits[1] * 32
        self.alternate_run_id = "containment-run:" + digits[2] * 32
        self.source_seed = "a" if slot == 0 else "d"
        self.reviewer_id = f"containment-reviewer:independent-{slot}"

    def _write_decision(
        self,
        run_id: str,
        *,
        bound: bool,
        source_seed: str,
    ):
        pairs = []
        for scenario in ContainmentScenario:
            result, outcome = valid_scenario_result(scenario)
            outcome = replace(outcome, run_id=run_id)
            result = replace(
                result,
                run_id=run_id,
                watch_outcome_id=watch_outcome_id_for(outcome),
            )
            self.store.write_watch_outcome(outcome)
            self.store.write_result(result)
            pairs.append((result, outcome))
        bindings = None
        schema_version = 1
        if bound:
            schema_version = 2
            bindings = DecisionBindings(
                binding_version=2,
                source_commit_id=source_seed * 40,
                source_tree_id=("b" if source_seed != "b" else "c") * 40,
                source_artifact_id=source_artifact_id_for(
                    {
                        "artifact": f"authority-fixture-{source_seed}",
                        "schemaVersion": 1,
                    }
                ),
                protocol_version=2,
                publication_policy_version="handle-pinned-no-replace-v2",
                fixture_version=2,
                effect_receipt_version=1,
                authority_policy_version=1,
                mo2_version="2.5.2.0",
                mo2_executable_sha256="c" * 64,
            )
        decision = CapabilityDecision(
            schema_version=schema_version,
            run_id=run_id,
            mechanism=HISTORICAL_CONTAINMENT_MECHANISM,
            verdict=CapabilityVerdict.SUPPORTED,
            scenario_result_ids=tuple(
                scenario_result_id_for(result, outcome) for result, outcome in pairs
            ),
            reasons=(),
            bindings=bindings,
        )
        return self.store.write_decision(decision)

    def write_historical_decision(self):
        if not hasattr(self, "historical_decision_write"):
            self.historical_decision_write = self._write_decision(
                self.historical_run_id,
                bound=False,
                source_seed=self.source_seed,
            )
            self.historical_decision = self.historical_decision_write.value
            self.historical_decision_id = self.historical_decision_write.content_id
        return self.historical_decision_write

    def write_retirement(
        self,
        *,
        status: str = "RetiredInvalidated",
        decision_write=None,
        reason_codes: tuple[str, ...] = HISTORICAL_RETIREMENT_REASON_CODES,
    ):
        decision_write = decision_write or self.write_historical_decision()
        retirement = CapabilityRetirement(
            schema_version=1,
            authority_policy_version=1,
            decision_id=decision_write.content_id,
            run_id=decision_write.value.run_id,
            mechanism=decision_write.value.mechanism,
            status=status,
            reason_codes=reason_codes,
        )
        self.retirement_write = self.store.write_retirement(retirement)
        self.retirement = self.retirement_write.value
        self.retirement_id = self.retirement_write.content_id
        return self.retirement_write

    def write_replacement_decision(self, *, alternate: bool = False):
        run_id = self.alternate_run_id if alternate else self.replacement_run_id
        seed = "e" if alternate else self.source_seed
        write = self._write_decision(run_id, bound=True, source_seed=seed)
        if alternate:
            self.alternate_decision_write = write
            self.alternate_decision = write.value
            self.alternate_decision_id = write.content_id
        else:
            self.replacement_decision_write = write
            self.replacement_decision = write.value
            self.replacement_decision_id = write.content_id
        return write

    def write_review(
        self,
        *,
        decision_write=None,
        status: str = "Passed",
        independent: bool = True,
        reviewer_id: str | None = None,
        reason_codes: tuple[str, ...] | None = None,
    ):
        decision_write = decision_write or self.replacement_decision_write
        if reason_codes is None:
            reason_codes = () if status == "Passed" else ("review-evidence-mismatch",)
        review = CapabilityReview(
            schema_version=1,
            authority_policy_version=1,
            decision_id=decision_write.content_id,
            run_id=decision_write.value.run_id,
            mechanism=decision_write.value.mechanism,
            scenario_result_ids=decision_write.value.scenario_result_ids,
            bindings=decision_write.value.bindings,
            reviewer_id=reviewer_id or self.reviewer_id,
            independent=independent,
            status=status,
            reason_codes=reason_codes,
        )
        write = self.store.write_review(review)
        if status == "Passed" and independent:
            self.review_write = write
            self.review = write.value
            self.review_id = write.content_id
        return write

    def write_passing_review(self, **kwargs):
        return self.write_review(status="Passed", independent=True, **kwargs)

    def write_supersession(
        self,
        *,
        retirement_write=None,
        decision_write=None,
        review_write=None,
        bindings: DecisionBindings | None = None,
    ):
        retirement_write = retirement_write or self.retirement_write
        decision_write = decision_write or self.replacement_decision_write
        review_write = review_write or self.review_write
        decision = decision_write.value
        supersession = CapabilitySupersession(
            schema_version=1,
            authority_policy_version=1,
            retirement_id=retirement_write.content_id,
            retired_decision_id=retirement_write.value.decision_id,
            replacement_decision_id=decision_write.content_id,
            replacement_run_id=decision.run_id,
            replacement_mechanism=decision.mechanism,
            replacement_scenario_result_ids=decision.scenario_result_ids,
            replacement_bindings=bindings or decision.bindings,
            review_id=review_write.content_id,
        )
        self.supersession_write = self.store.write_supersession(supersession)
        self.supersession = self.supersession_write.value
        self.supersession_id = self.supersession_write.content_id
        return self.supersession_write

    def write_eligibility(
        self,
        *,
        supersession_write=None,
        review_write=None,
        decision_write=None,
    ):
        supersession_write = supersession_write or self.supersession_write
        review_write = review_write or self.review_write
        decision_write = decision_write or self.replacement_decision_write
        eligibility = CapabilityEligibility(
            schema_version=1,
            authority_policy_version=1,
            decision_id=decision_write.content_id,
            run_id=decision_write.value.run_id,
            supersession_id=supersession_write.content_id,
            review_id=review_write.content_id,
            status="Eligible",
        )
        self.eligibility_write = self.store.write_eligibility(eligibility)
        self.eligibility = self.eligibility_write.value
        self.eligibility_id = self.eligibility_write.content_id
        return self.eligibility_write

    def complete(self):
        self.write_retirement()
        self.write_replacement_decision()
        self.write_passing_review()
        self.write_supersession()
        self.write_eligibility()
        return self

    def resolve_current(self):
        return resolve_current_eligible_decision(self.root)

    def resolve_specific(self, _decision_id: str):
        return self.resolve_current()


class Mo2ContainmentAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="modlab-authority-")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_historical_decision_is_rejected_even_when_bytes_still_exist(self):
        fixture = AuthorityFixture(self.root)
        historical = fixture.write_historical_decision()
        fixture.write_retirement(status="RetiredInvalidated")

        self.assertTrue(historical.path.is_file())
        self.assertEqual(
            fixture.historical_decision,
            fixture.store.load_decision(fixture.historical_run_id),
        )
        with self.assertRaisesRegex(ContainmentAuthorityError, "retired"):
            fixture.resolve_specific(fixture.historical_decision_id)

    def test_only_independently_reviewed_replacement_is_eligible(self):
        fixture = AuthorityFixture(self.root)
        fixture.write_retirement()
        fixture.write_replacement_decision()

        with self.assertRaisesRegex(ContainmentAuthorityError, "review"):
            fixture.resolve_current()

        fixture.write_passing_review()
        fixture.write_supersession()
        fixture.write_eligibility()
        resolved = fixture.resolve_current()
        self.assertEqual(fixture.replacement_decision, resolved.decision)
        self.assertEqual(fixture.replacement_decision_id, resolved.decision_id)

    def test_two_eligible_records_are_rejected_as_ambiguous(self):
        first = AuthorityFixture(self.root, slot=0)
        first.complete()
        second = AuthorityFixture(self.root, slot=1)
        second.complete()

        with self.assertRaisesRegex(ContainmentAuthorityError, "multiple"):
            resolve_current_eligible_decision(self.root)

    def test_eligibility_without_supersession_is_rejected(self):
        store = ContainmentStore(self.root)
        eligibility = CapabilityEligibility(
            schema_version=1,
            authority_policy_version=1,
            decision_id="containment-decision-sha256:" + "1" * 64,
            run_id="containment-run:" + "2" * 32,
            supersession_id="containment-supersession-sha256:" + "3" * 64,
            review_id="containment-review-sha256:" + "4" * 64,
            status="Eligible",
        )
        store.write_eligibility(eligibility)

        with self.assertRaisesRegex(ContainmentAuthorityError, "supersession"):
            resolve_current_eligible_decision(self.root)

    def test_supersession_without_retirement_is_rejected(self):
        fixture = AuthorityFixture(self.root)
        fixture.write_replacement_decision()
        fixture.write_passing_review()
        fake_retirement_id = "containment-retirement-sha256:" + "9" * 64
        supersession = CapabilitySupersession(
            schema_version=1,
            authority_policy_version=1,
            retirement_id=fake_retirement_id,
            retired_decision_id="containment-decision-sha256:" + "8" * 64,
            replacement_decision_id=fixture.replacement_decision_id,
            replacement_run_id=fixture.replacement_run_id,
            replacement_mechanism=fixture.replacement_decision.mechanism,
            replacement_scenario_result_ids=(
                fixture.replacement_decision.scenario_result_ids
            ),
            replacement_bindings=fixture.replacement_decision.bindings,
            review_id=fixture.review_id,
        )
        supersession_write = fixture.store.write_supersession(supersession)
        fixture.write_eligibility(supersession_write=supersession_write)

        with self.assertRaisesRegex(ContainmentAuthorityError, "retirement"):
            fixture.resolve_current()

    def test_review_bound_to_different_decision_bytes_is_rejected(self):
        fixture = AuthorityFixture(self.root)
        fixture.write_retirement()
        first = fixture.write_replacement_decision()
        review = fixture.write_passing_review(decision_write=first)
        second = fixture.write_replacement_decision(alternate=True)
        supersession = fixture.write_supersession(
            decision_write=second,
            review_write=review,
        )
        fixture.write_eligibility(
            supersession_write=supersession,
            review_write=review,
            decision_write=second,
        )

        with self.assertRaisesRegex(ContainmentAuthorityError, "review.*decision"):
            fixture.resolve_current()

    def test_changed_source_version_or_result_binding_is_rejected(self):
        mutations = {
            "source": lambda document: document["bindings"].__setitem__(
                "sourceCommitId", "f" * 40
            ),
            "version": lambda document: document["bindings"].__setitem__(
                "fixtureVersion", 1
            ),
            "result": lambda document: document["scenarioResultIds"].__setitem__(
                "NewFolder", "containment-result-sha256:" + "9" * 64
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(binding=label):
                with tempfile.TemporaryDirectory(prefix=f"modlab-authority-{label}-") as root:
                    fixture = AuthorityFixture(Path(root))
                    fixture.complete()
                    decision_path = fixture.replacement_decision_write.path
                    document = json.loads(decision_path.read_text(encoding="utf-8"))
                    mutate(document)
                    decision_path.write_bytes(_canonical(document))

                    with self.assertRaisesRegex(
                        ContainmentAuthorityError,
                        "binding|decision|result|version",
                    ):
                        fixture.resolve_current()

    def test_malformed_direct_authority_entry_fails_closed(self):
        fixture = AuthorityFixture(self.root)
        fixture.complete()
        malformed = fixture.store.authority_path() / "eligibilities" / "current.json"
        malformed.write_bytes(b"{}\n")

        with self.assertRaisesRegex(ContainmentAuthorityError, "noncanonical"):
            fixture.resolve_current()

    def test_reparse_authority_ancestor_fails_closed(self):
        fixture = AuthorityFixture(self.root)
        fixture.complete()
        authority = fixture.store.authority_path()
        target = self.root / "relocated-authority"
        authority.rename(target)
        if os.name == "nt":
            created = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(authority), str(target)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, created.returncode, created.stdout + created.stderr)
        else:
            os.symlink(target, authority, target_is_directory=True)
        try:
            with self.assertRaisesRegex(
                ContainmentAuthorityError,
                "redirected|reparse",
            ):
                fixture.resolve_current()
        finally:
            if authority.exists() or authority.is_symlink():
                if authority.is_symlink():
                    authority.unlink()
                else:
                    authority.rmdir()
            target.rename(authority)

    def test_caller_cannot_select_the_historical_run_or_decision(self):
        fixture = AuthorityFixture(self.root)
        fixture.complete()
        resolved = fixture.resolve_current()

        self.assertEqual(
            ("validation_root",),
            tuple(inspect.signature(resolve_current_eligible_decision).parameters),
        )
        with self.assertRaises(TypeError):
            resolve_current_eligible_decision(self.root, fixture.historical_run_id)
        with self.assertRaises(TypeError):
            resolve_current_eligible_decision(self.root, fixture.historical_decision_id)
        self.assertEqual(fixture.replacement_decision_id, resolved.decision_id)

    def test_historical_identity_cannot_be_reintroduced_by_eligibility(self):
        candidates = (
            (HISTORICAL_CONTAINMENT_DECISION_ID, "containment-run:" + "a" * 32),
            ("containment-decision-sha256:" + "b" * 64, HISTORICAL_CONTAINMENT_RUN_ID),
        )
        for decision_id, run_id in candidates:
            with self.subTest(decision_id=decision_id, run_id=run_id):
                with tempfile.TemporaryDirectory(prefix="modlab-retired-identity-") as root:
                    store = ContainmentStore(Path(root))
                    store.write_eligibility(
                        CapabilityEligibility(
                            schema_version=1,
                            authority_policy_version=1,
                            decision_id=decision_id,
                            run_id=run_id,
                            supersession_id=(
                                "containment-supersession-sha256:" + "c" * 64
                            ),
                            review_id="containment-review-sha256:" + "d" * 64,
                            status="Eligible",
                        )
                    )
                    with self.assertRaisesRegex(ContainmentAuthorityError, "retired"):
                        resolve_current_eligible_decision(Path(root))

    def test_eligible_decision_that_is_later_retired_is_rejected(self):
        fixture = AuthorityFixture(self.root)
        fixture.complete()
        fixture.write_retirement(
            decision_write=fixture.replacement_decision_write,
            reason_codes=("replacement-authority-invalidated",),
        )

        with self.assertRaisesRegex(ContainmentAuthorityError, "retired"):
            fixture.resolve_current()

    def test_failed_or_non_independent_review_never_confers_eligibility(self):
        for status, independent, message in (
            ("Failed", True, "review.*failed"),
            ("Passed", False, "independent review"),
        ):
            with self.subTest(status=status, independent=independent):
                with tempfile.TemporaryDirectory(prefix="modlab-authority-review-") as root:
                    fixture = AuthorityFixture(Path(root))
                    fixture.write_retirement()
                    fixture.write_replacement_decision()
                    review = fixture.write_review(
                        status=status,
                        independent=independent,
                    )
                    supersession = fixture.write_supersession(review_write=review)
                    fixture.write_eligibility(
                        review_write=review,
                        supersession_write=supersession,
                    )
                    with self.assertRaisesRegex(ContainmentAuthorityError, message):
                        fixture.resolve_current()

    def test_multiple_reviews_for_one_decision_are_ambiguous(self):
        fixture = AuthorityFixture(self.root)
        fixture.write_retirement()
        fixture.write_replacement_decision()
        passing = fixture.write_passing_review()
        fixture.write_review(
            status="Failed",
            independent=True,
            reviewer_id="containment-reviewer:independent-second",
        )
        supersession = fixture.write_supersession(review_write=passing)
        fixture.write_eligibility(
            review_write=passing,
            supersession_write=supersession,
        )

        with self.assertRaisesRegex(ContainmentAuthorityError, "ambiguous review"):
            fixture.resolve_current()

    def test_authority_records_are_canonical_content_addressed_and_immutable(self):
        fixture = AuthorityFixture(self.root)
        fixture.complete()
        resolved = fixture.resolve_current()
        records = (
            (
                fixture.retirement_write,
                fixture.store.write_retirement,
                fixture.store.load_retirement,
            ),
            (
                fixture.review_write,
                fixture.store.write_review,
                fixture.store.load_review,
            ),
            (
                fixture.supersession_write,
                fixture.store.write_supersession,
                fixture.store.load_supersession,
            ),
            (
                fixture.eligibility_write,
                fixture.store.write_eligibility,
                fixture.store.load_eligibility,
            ),
        )
        for written, writer, loader in records:
            with self.subTest(record=written.content_id.split(":", 1)[0]):
                self.assertEqual(written.value, loader(written.content_id))
                repeated = writer(written.value)
                self.assertTrue(repeated.existed)
                self.assertEqual(written.content_id, repeated.content_id)
                data = written.path.read_bytes()
                self.assertTrue(data.endswith(b"\n"))
                self.assertEqual(_canonical(json.loads(data)), data)

        fixture.eligibility_write.path.write_bytes(b"{}\n")
        with self.assertRaisesRegex(ContainmentStoreError, "different bytes"):
            fixture.store.write_eligibility(resolved.eligibility)
        with self.assertRaises(ContainmentStoreError):
            fixture.store.load_eligibility(fixture.eligibility_id)

    def test_exact_historical_retirement_identity_requires_exact_defect_codes(self):
        store = ContainmentStore(self.root)
        exact = CapabilityRetirement(
            schema_version=1,
            authority_policy_version=1,
            decision_id=HISTORICAL_CONTAINMENT_DECISION_ID,
            run_id=HISTORICAL_CONTAINMENT_RUN_ID,
            mechanism=HISTORICAL_CONTAINMENT_MECHANISM,
            status="RetiredInvalidated",
            reason_codes=HISTORICAL_RETIREMENT_REASON_CODES,
        )
        written = store.write_retirement(exact)
        self.assertEqual(exact, store.load_retirement(written.content_id))

        for changed in (
            replace(exact, reason_codes=("inaccurate-cli-side-effect-reporting",)),
            replace(exact, decision_id="containment-decision-sha256:" + "f" * 64),
            replace(exact, run_id="containment-run:" + "f" * 32),
        ):
            with self.subTest(changed=changed):
                with self.assertRaises(ContainmentStoreError):
                    store.write_retirement(changed)

    def test_zero_eligible_leaves_is_the_safe_default(self):
        with self.assertRaisesRegex(ContainmentAuthorityError, "zero|no current"):
            resolve_current_eligible_decision(self.root)


if __name__ == "__main__":
    unittest.main()
