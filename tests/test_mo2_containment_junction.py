import hashlib
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from modlab.validation import windows_junction
from modlab.validation.windows_integrity import (
    IntegrityLevel,
    inspect_path_integrity,
    set_low_integrity_tree,
)
from modlab.validation.windows_junction import (
    ContainmentSafetyError,
    IO_REPARSE_TAG_MOUNT_POINT,
    adopt_unique_staged_mod,
    build_projection,
    create_mod_projection,
    inspect_junction,
)


def _mount_point_payload(substitute_name: str, print_name: str) -> bytes:
    substitute = substitute_name.encode("utf-16-le")
    display = print_name.encode("utf-16-le")
    path_buffer = substitute + b"\0\0" + display + b"\0\0"
    return struct.pack(
        "<IHHHHHH",
        IO_REPARSE_TAG_MOUNT_POINT,
        8 + len(path_buffer),
        0,
        0,
        len(substitute),
        len(substitute) + 2,
        len(display),
    ) + path_buffer
@unittest.skipUnless(os.name == "nt", "NTFS junction tests require Windows")
class Mo2ContainmentJunctionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="modlab-containment-junction-")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_projection_reads_source_without_copying_and_records_exact_payload(self):
        source = self.root / "source" / "Protected Existing"
        stage = self.root / "stage" / "Protected Existing"
        source.mkdir(parents=True)
        stage.parent.mkdir()
        marker = source / "marker.txt"
        marker.write_bytes(b"source")

        evidence = create_mod_projection(source, stage)

        source_path = source.resolve(strict=True)
        substitute = "\\??\\" + str(source_path)
        expected_payload = _mount_point_payload(substitute, str(source_path))
        self.assertEqual(source_path, evidence.target_path)
        self.assertEqual(substitute, evidence.substitute_name)
        self.assertEqual(str(source_path), evidence.print_name)
        self.assertEqual(IO_REPARSE_TAG_MOUNT_POINT, evidence.reparse_tag)
        self.assertEqual(hashlib.sha256(expected_payload).hexdigest(), evidence.reparse_payload_sha256)
        self.assertEqual(b"source", (stage / "marker.txt").read_bytes())

        marker.write_bytes(b"changed-after-projection")
        self.assertEqual(b"changed-after-projection", (stage / "marker.txt").read_bytes())

    def test_projection_rejects_a_reparse_component_in_the_source_path(self):
        outside = self.root / "outside"
        alias = self.root / "source-alias"
        stage = self.root / "stage" / "Projected"
        nested = outside / "Nested"
        nested.mkdir(parents=True)
        stage.parent.mkdir()
        create_mod_projection(outside, alias)

        with self.assertRaisesRegex(ContainmentSafetyError, "reparse component"):
            create_mod_projection(alias / "Nested", stage)

        self.assertFalse(stage.exists())

    def test_failed_projection_readback_removes_only_the_new_junction(self):
        source = self.root / "source"
        stage_root = self.root / "stage"
        link = stage_root / "Projected"
        source.mkdir()
        stage_root.mkdir()
        marker = source / "marker.txt"
        marker.write_bytes(b"protected")

        with mock.patch.object(
            windows_junction,
            "inspect_junction",
            side_effect=ContainmentSafetyError("injected readback failure"),
        ):
            with self.assertRaisesRegex(ContainmentSafetyError, "readback failure"):
                create_mod_projection(source, link)

        self.assertFalse(link.exists())
        self.assertEqual(b"protected", marker.read_bytes())

    def test_build_projection_is_casefold_sorted_and_rejects_source_reparse_entries(self):
        source_mods = self.root / "source-mods"
        stage_mods = self.root / "stage-mods"
        source_mods.mkdir()
        stage_mods.mkdir()
        for name, payload in (("Bravo", b"bravo"), ("alpha", b"alpha")):
            mod = source_mods / name
            mod.mkdir()
            (mod / "marker.txt").write_bytes(payload)

        evidence = build_projection(source_mods, stage_mods)

        self.assertEqual(("alpha", "Bravo"), tuple(item.link_path.name for item in evidence))
        self.assertEqual(b"alpha", (stage_mods / "alpha" / "marker.txt").read_bytes())
        self.assertEqual(b"bravo", (stage_mods / "Bravo" / "marker.txt").read_bytes())

        redirected_source = self.root / "redirected-source"
        redirected_stage = self.root / "redirected-stage"
        redirected_target = self.root / "redirected-target"
        redirected_source.mkdir()
        redirected_stage.mkdir()
        redirected_target.mkdir()
        create_mod_projection(redirected_target, redirected_source / "Redirected")
        with self.assertRaisesRegex(ContainmentSafetyError, "source mod is reparse"):
            build_projection(redirected_source, redirected_stage)

    def test_build_projection_rolls_back_exact_created_junctions_in_reverse_order(self):
        source_mods = self.root / "source"
        stage_mods = self.root / "stage"
        source_mods.mkdir()
        stage_mods.mkdir()
        for name in ("Alpha", "Beta", "Gamma"):
            mod = source_mods / name
            mod.mkdir()
            (mod / "marker.txt").write_bytes(name.encode("ascii"))

        real_create = windows_junction.create_mod_projection
        real_inspect = windows_junction.inspect_junction
        failure_started = False
        rollback_inspections = []

        def create_or_fail(source_mod, staging_mod):
            nonlocal failure_started
            if Path(source_mod).name == "Gamma":
                failure_started = True
                raise OSError("injected third projection failure")
            return real_create(source_mod, staging_mod)

        def inspect_and_record(path):
            evidence = real_inspect(path)
            if failure_started:
                rollback_inspections.append(Path(path).name)
            return evidence

        with (
            mock.patch.object(
                windows_junction,
                "create_mod_projection",
                side_effect=create_or_fail,
            ),
            mock.patch.object(
                windows_junction,
                "inspect_junction",
                side_effect=inspect_and_record,
            ),
        ):
            with self.assertRaisesRegex(OSError, "third projection failure"):
                build_projection(source_mods, stage_mods)

        self.assertEqual(["Beta", "Alpha"], rollback_inspections)
        self.assertEqual([], list(stage_mods.iterdir()))
        for name in ("Alpha", "Beta", "Gamma"):
            self.assertEqual(
                name.encode("ascii"),
                (source_mods / name / "marker.txt").read_bytes(),
            )

    def test_build_projection_rollback_leaves_a_changed_junction_untouched(self):
        source_mods = self.root / "source"
        stage_mods = self.root / "stage"
        wrong_target = self.root / "wrong-target"
        source_mods.mkdir()
        stage_mods.mkdir()
        wrong_target.mkdir()
        (wrong_target / "marker.txt").write_bytes(b"wrong")
        for name in ("Alpha", "Beta", "Gamma"):
            mod = source_mods / name
            mod.mkdir()
            (mod / "marker.txt").write_bytes(name.encode("ascii"))

        real_create = windows_junction.create_mod_projection

        def create_replace_then_fail(source_mod, staging_mod):
            if Path(source_mod).name == "Gamma":
                (stage_mods / "Alpha").rmdir()
                real_create(wrong_target, stage_mods / "Alpha")
                raise OSError("injected third projection failure")
            return real_create(source_mod, staging_mod)

        with mock.patch.object(
            windows_junction,
            "create_mod_projection",
            side_effect=create_replace_then_fail,
        ):
            with self.assertRaisesRegex(
                ContainmentSafetyError,
                "exact rollback failed.*Alpha",
            ):
                build_projection(source_mods, stage_mods)

        self.assertFalse((stage_mods / "Beta").exists())
        changed = inspect_junction(stage_mods / "Alpha")
        self.assertEqual(wrong_target.resolve(strict=True), changed.target_path)
        self.assertEqual(b"wrong", (wrong_target / "marker.txt").read_bytes())
        for name in ("Alpha", "Beta", "Gamma"):
            self.assertEqual(
                name.encode("ascii"),
                (source_mods / name / "marker.txt").read_bytes(),
            )

    def test_adoption_moves_one_medium_integrity_directory_with_identical_tree(self):
        stage, source, quarantine = self._adoption_roots()
        candidate = stage / "ModLab Spike New"
        (candidate / "meshes").mkdir(parents=True)
        (candidate / "marker.txt").write_bytes(b"root")
        (candidate / "meshes" / "new.bin").write_bytes(b"new")
        set_low_integrity_tree(candidate)

        result = adopt_unique_staged_mod(
            stage_mods=stage,
            source_mods=source,
            expected_name="ModLab Spike New",
            before_names=(),
            quarantine_root=quarantine,
        )

        self.assertEqual("ModLab Spike New", result.adopted_name)
        self.assertEqual(source / "ModLab Spike New", result.destination_path)
        self.assertEqual(result.before_tree, result.after_tree)
        self.assertEqual(2, result.before_tree.regular_file_count)
        self.assertEqual(1, result.before_tree.directory_count)
        self.assertEqual(7, result.before_tree.total_size)
        self.assertEqual(IntegrityLevel.MEDIUM, result.final_integrity)
        self.assertFalse(result.final_is_reparse)
        self.assertFalse(candidate.exists())
        self.assertEqual(b"new", (result.destination_path / "meshes" / "new.bin").read_bytes())

    def test_collision_is_quarantined_without_changing_the_existing_source(self):
        stage, source, quarantine = self._adoption_roots()
        existing = source / "Existing"
        existing.mkdir()
        (existing / "marker.txt").write_bytes(b"protected")
        candidate = stage / "existing"
        candidate.mkdir()
        (candidate / "marker.txt").write_bytes(b"candidate")

        with self.assertRaisesRegex(ContainmentSafetyError, "source collision"):
            adopt_unique_staged_mod(
                stage_mods=stage,
                source_mods=source,
                expected_name="existing",
                before_names=(),
                quarantine_root=quarantine,
            )

        self.assertEqual(b"protected", (existing / "marker.txt").read_bytes())
        self.assertEqual(b"candidate", (quarantine / "existing" / "marker.txt").read_bytes())
        self.assertFalse(candidate.exists())

    def test_extra_new_entries_are_all_quarantined_and_never_adopted(self):
        stage, source, quarantine = self._adoption_roots()
        for name in ("Expected", "Unexpected Backup"):
            entry = stage / name
            entry.mkdir()
            (entry / "marker.txt").write_text(name, encoding="utf-8")

        with self.assertRaisesRegex(ContainmentSafetyError, "exactly one new staging entry"):
            adopt_unique_staged_mod(
                stage_mods=stage,
                source_mods=source,
                expected_name="Expected",
                before_names=(),
                quarantine_root=quarantine,
            )

        self.assertEqual([], list(stage.iterdir()))
        self.assertEqual(["Expected", "Unexpected Backup"], sorted(path.name for path in quarantine.iterdir()))
        self.assertEqual([], list(source.iterdir()))

    def test_descendant_reparse_is_quarantined_before_integrity_normalization(self):
        stage, source, quarantine = self._adoption_roots()
        candidate = stage / "Expected"
        target = self.root / "outside"
        candidate.mkdir()
        target.mkdir()
        create_mod_projection(target, candidate / "redirect")

        with mock.patch.object(
            windows_junction,
            "set_medium_integrity_entries",
            wraps=windows_junction.set_medium_integrity_entries,
        ) as normalize:
            with self.assertRaisesRegex(ContainmentSafetyError, "reparse descendant"):
                adopt_unique_staged_mod(
                    stage_mods=stage,
                    source_mods=source,
                    expected_name="Expected",
                    before_names=(),
                    quarantine_root=quarantine,
                )

        normalize.assert_not_called()
        self.assertFalse(candidate.exists())
        self.assertTrue((quarantine / "Expected").exists())
        self.assertEqual([], list(source.iterdir()))

    def test_failed_integrity_normalization_quarantines_the_candidate(self):
        stage, source, quarantine = self._adoption_roots()
        candidate = stage / "Expected"
        candidate.mkdir()
        (candidate / "marker.txt").write_bytes(b"candidate")

        with mock.patch.object(
            windows_junction,
            "set_medium_integrity_entries",
            side_effect=OSError("injected integrity failure"),
        ):
            with self.assertRaisesRegex(ContainmentSafetyError, "integrity failure"):
                adopt_unique_staged_mod(
                    stage_mods=stage,
                    source_mods=source,
                    expected_name="Expected",
                    before_names=(),
                    quarantine_root=quarantine,
                )

        self.assertFalse(candidate.exists())
        self.assertEqual(b"candidate", (quarantine / "Expected" / "marker.txt").read_bytes())
        self.assertEqual([], list(source.iterdir()))

    def test_pinned_descendant_blocks_directory_to_junction_swap_before_recursion(self):
        stage, source, quarantine = self._adoption_roots()
        candidate = stage / "Expected"
        child = candidate / "child"
        held_child = self.root / "held-child"
        external = self.root / "external"
        child.mkdir(parents=True)
        external.mkdir()
        (child / "inside.txt").write_bytes(b"inside")
        (external / "outside.txt").write_bytes(b"outside")
        set_low_integrity_tree(candidate)
        external_integrity = inspect_path_integrity(external)
        real_create_file = windows_junction._kernel32.CreateFileW
        race = {"attempted": False, "blocked": False}

        def create_file_with_swap(path, access, share, security, creation, flags, template):
            handle = real_create_file(path, access, share, security, creation, flags, template)
            if Path(path) == child and share == 0x00000001 and not race["attempted"]:
                race["attempted"] = True
                try:
                    child.rename(held_child)
                    create_mod_projection(external, child)
                except PermissionError:
                    race["blocked"] = True
            return handle

        with (
            mock.patch.object(
                windows_junction._kernel32,
                "CreateFileW",
                side_effect=create_file_with_swap,
            ),
            mock.patch.object(
                windows_junction,
                "set_medium_integrity_entries",
                wraps=windows_junction.set_medium_integrity_entries,
            ) as normalize,
        ):
            try:
                result = adopt_unique_staged_mod(
                    stage_mods=stage,
                    source_mods=source,
                    expected_name="Expected",
                    before_names=(),
                    quarantine_root=quarantine,
                )
            except ContainmentSafetyError as error:
                result = error

        self.assertNotIsInstance(result, ContainmentSafetyError)
        self.assertTrue(race["attempted"])
        self.assertTrue(race["blocked"])
        normalize.assert_called_once_with((candidate, child, child / "inside.txt"))
        self.assertFalse(held_child.exists())
        self.assertEqual(external_integrity, inspect_path_integrity(external))
        self.assertEqual(b"outside", (external / "outside.txt").read_bytes())

    def test_pinned_quarantine_never_moves_replacement_at_the_old_path(self):
        stage, source, quarantine = self._adoption_roots()
        candidate = stage / "Expected"
        held_original = self.root / "held-original"
        candidate.mkdir()
        (candidate / "marker.txt").write_bytes(b"original")
        real_set_information = windows_junction._kernel32.SetFileInformationByHandle
        race = {"attempted": False, "blocked": False}

        def attempt_replacement() -> None:
            if race["attempted"]:
                return
            race["attempted"] = True
            try:
                candidate.rename(held_original)
                candidate.mkdir()
                (candidate / "marker.txt").write_bytes(b"replacement")
            except PermissionError:
                race["blocked"] = True

        def set_information_with_replacement(handle, information_class, information, size):
            if information_class == 3:
                attempt_replacement()
            return real_set_information(handle, information_class, information, size)

        with (
            mock.patch.object(
                windows_junction,
                "set_medium_integrity_entries",
                side_effect=OSError("injected normalization failure"),
            ),
            mock.patch.object(
                windows_junction._kernel32,
                "SetFileInformationByHandle",
                side_effect=set_information_with_replacement,
            ),
        ):
            with self.assertRaisesRegex(ContainmentSafetyError, "normalization failure"):
                adopt_unique_staged_mod(
                    stage_mods=stage,
                    source_mods=source,
                    expected_name="Expected",
                    before_names=(),
                    quarantine_root=quarantine,
                )

        self.assertTrue(race["attempted"])
        self.assertTrue(race["blocked"])
        self.assertFalse(held_original.exists())
        self.assertFalse(candidate.exists())
        self.assertEqual(b"original", (quarantine / "Expected" / "marker.txt").read_bytes())

    def test_replaced_projection_and_candidate_are_quarantined_as_ambiguous(self):
        stage, source, quarantine = self._adoption_roots()
        protected = source / "Protected Existing"
        protected.mkdir()
        (protected / "marker.txt").write_bytes(b"protected")
        projection = stage / "Protected Existing"
        create_mod_projection(protected, projection)
        projection.rmdir()
        projection.mkdir()
        (projection / "marker.txt").write_bytes(b"replacement")
        candidate = stage / "Expected"
        candidate.mkdir()

        with self.assertRaisesRegex(ContainmentSafetyError, "changed staging projection"):
            adopt_unique_staged_mod(
                stage_mods=stage,
                source_mods=source,
                expected_name="Expected",
                before_names=("Protected Existing",),
                quarantine_root=quarantine,
            )

        self.assertEqual([], list(stage.iterdir()))
        self.assertTrue((quarantine / "Protected Existing").is_dir())
        self.assertTrue((quarantine / "Expected").is_dir())
        self.assertEqual(b"protected", (protected / "marker.txt").read_bytes())

    def test_canonical_projection_to_wrong_direct_target_is_quarantined(self):
        stage, source, quarantine = self._adoption_roots()
        protected = source / "Protected Existing"
        wrong_target = self.root / "wrong-direct-target"
        protected.mkdir()
        wrong_target.mkdir()
        (protected / "marker.txt").write_bytes(b"protected")
        (wrong_target / "marker.txt").write_bytes(b"wrong")
        create_mod_projection(wrong_target, stage / "Protected Existing")
        candidate = stage / "Expected"
        candidate.mkdir()
        (candidate / "marker.txt").write_bytes(b"candidate")

        with self.assertRaisesRegex(ContainmentSafetyError, "changed staging projection"):
            adopt_unique_staged_mod(
                stage_mods=stage,
                source_mods=source,
                expected_name="Expected",
                before_names=("Protected Existing",),
                quarantine_root=quarantine,
            )

        self.assertEqual([], list(stage.iterdir()))
        quarantined = inspect_junction(quarantine / "Protected Existing")
        self.assertEqual(wrong_target.resolve(strict=True), quarantined.target_path)
        self.assertEqual(b"candidate", (quarantine / "Expected" / "marker.txt").read_bytes())
        self.assertEqual(b"protected", (protected / "marker.txt").read_bytes())

    def test_destination_appearing_at_rename_boundary_is_not_replaced(self):
        stage, source, quarantine = self._adoption_roots()
        candidate = stage / "Expected"
        candidate.mkdir()
        (candidate / "marker.txt").write_bytes(b"candidate")
        real_collision_check = windows_junction._collision_free_destination

        def race_destination(source_mods: Path, expected_name: str) -> Path:
            destination = real_collision_check(source_mods, expected_name)
            destination.mkdir()
            (destination / "marker.txt").write_bytes(b"racer")
            return destination

        with mock.patch.object(
            windows_junction,
            "_collision_free_destination",
            side_effect=race_destination,
        ):
            with self.assertRaisesRegex(ContainmentSafetyError, "destination already exists"):
                adopt_unique_staged_mod(
                    stage_mods=stage,
                    source_mods=source,
                    expected_name="Expected",
                    before_names=(),
                    quarantine_root=quarantine,
                )

        self.assertEqual(b"racer", (source / "Expected" / "marker.txt").read_bytes())
        self.assertEqual(b"candidate", (quarantine / "Expected" / "marker.txt").read_bytes())

    def test_pinned_destination_blocks_post_move_content_change(self):
        stage, source, quarantine = self._adoption_roots()
        candidate = stage / "Expected"
        candidate.mkdir()
        (candidate / "marker.txt").write_bytes(b"before")
        real_stable_identity = windows_junction.stable_tree_identity
        calls = 0
        blocked = False

        def mutate_before_second_identity(path: Path, *, required_equal_passes: int):
            nonlocal calls, blocked
            calls += 1
            if calls == 2:
                try:
                    (path / "marker.txt").write_bytes(b"after")
                except PermissionError:
                    blocked = True
            return real_stable_identity(path, required_equal_passes=required_equal_passes)

        with mock.patch.object(
            windows_junction,
            "stable_tree_identity",
            side_effect=mutate_before_second_identity,
        ):
            result = adopt_unique_staged_mod(
                stage_mods=stage,
                source_mods=source,
                expected_name="Expected",
                before_names=(),
                quarantine_root=quarantine,
            )

        self.assertTrue(blocked)
        self.assertEqual(result.before_tree, result.after_tree)
        self.assertEqual(b"before", (source / "Expected" / "marker.txt").read_bytes())
        self.assertEqual([], list(quarantine.iterdir()))

    def _adoption_roots(self) -> tuple[Path, Path, Path]:
        stage = self.root / "stage"
        source = self.root / "source"
        quarantine = self.root / "quarantine"
        stage.mkdir()
        source.mkdir()
        quarantine.mkdir()
        return stage, source, quarantine


if __name__ == "__main__":
    unittest.main()
