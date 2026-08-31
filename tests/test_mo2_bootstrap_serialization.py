import json
import unittest
from dataclasses import replace

from modlab.adapters.mo2.bootstrap_model import BootstrapJobState
from modlab.adapters.mo2.bootstrap_serialization import (
    BootstrapFormatError,
    journal_from_bytes,
    journal_to_bytes,
    plan_from_bytes,
    plan_id_for,
    plan_to_bytes,
    receipt_from_bytes,
    receipt_id_for,
    receipt_to_bytes,
)
from tests.support.mo2_bootstrap import (
    make_journal_fixture,
    make_plan_fixture,
    make_receipt_fixture,
    malformed_bootstrap_documents,
)


class BootstrapSerializationTests(unittest.TestCase):
    def assert_canonical_key_order(self, value: object):
        if isinstance(value, dict):
            self.assertEqual(list(value), sorted(value))
            for nested in value.values():
                self.assert_canonical_key_order(nested)
        elif isinstance(value, list):
            for nested in value:
                self.assert_canonical_key_order(nested)

    def test_plan_identity_excludes_no_fields_except_its_derived_id(self):
        plan = make_plan_fixture()

        self.assertEqual(plan, plan_from_bytes(plan_to_bytes(plan)))
        self.assertEqual(plan.plan_id, plan_id_for(plan))
        changed = replace(
            plan,
            target=replace(plan.target, inventory_sha256="b" * 64),
        )
        self.assertNotEqual(plan.plan_id, plan_id_for(changed))

    def test_job_identity_and_state_transition_fields_round_trip(self):
        journal = make_journal_fixture(state=BootstrapJobState.STAGED)

        self.assertEqual(journal, journal_from_bytes(journal_to_bytes(journal)))

    def test_receipt_identity_covers_package_extras_and_profiles(self):
        receipt = make_receipt_fixture()

        self.assertEqual(receipt, receipt_from_bytes(receipt_to_bytes(receipt)))
        changed_extra = replace(receipt, extra_entries=("runtime.log",))
        changed_profile = replace(receipt, profile_state_sha256="b" * 64)
        self.assertNotEqual(receipt.receipt_id, receipt_id_for(changed_extra))
        self.assertNotEqual(receipt.receipt_id, receipt_id_for(changed_profile))

    def test_every_document_rejects_duplicates_saves_and_extra_fields(self):
        for data, loader in malformed_bootstrap_documents():
            with self.subTest(loader=loader.__name__):
                with self.assertRaises(BootstrapFormatError):
                    loader(data)

    def test_canonical_documents_are_sorted_utf8_with_one_trailing_newline(self):
        for data in (
            plan_to_bytes(make_plan_fixture()),
            journal_to_bytes(make_journal_fixture()),
            receipt_to_bytes(make_receipt_fixture()),
        ):
            with self.subTest(prefix=data[:40]):
                self.assertTrue(data.endswith(b"\n"))
                self.assertFalse(data.endswith(b"\n\n"))
                self.assertEqual(data, data.decode("utf-8").encode("utf-8"))
                self.assert_canonical_key_order(json.loads(data))


if __name__ == "__main__":
    unittest.main()
