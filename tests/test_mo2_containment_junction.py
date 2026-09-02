import hashlib
import ctypes
from dataclasses import replace
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
    ensure_direct_subdirectory,
    inspect_junction,
    quarantine_exact_object,
    quarantine_replacement_tree,
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

    def test_file_disposition_info_uses_the_one_byte_boolean_abi(self):
        self.assertEqual(1, ctypes.sizeof(windows_junction._FILE_DISPOSITION_INFO))

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
            "_inspect_junction_handle",
            side_effect=ContainmentSafetyError("injected readback failure"),
        ):
            with self.assertRaisesRegex(ContainmentSafetyError, "readback failure"):
                create_mod_projection(source, link)

        self.assertFalse(link.exists())
        self.assertEqual(b"protected", marker.read_bytes())

    def test_projection_readback_retains_the_exact_link_against_reconstruction(self):
        source = self.root / "source"
        stage_root = self.root / "stage"
        link = stage_root / "Projected"
        source.mkdir()
        stage_root.mkdir()
        real_read = windows_junction._read_reparse_payload_handle
        real_create = windows_junction.create_mod_projection
        race = {"attempted": False, "blocked": False, "reconstructing": False}

        def read_with_reconstruction(handle, path):
            if Path(path) == link and not race["attempted"] and not race["reconstructing"]:
                race["attempted"] = True
                try:
                    link.rmdir()
                    race["reconstructing"] = True
                    real_create(source, link)
                except PermissionError:
                    race["blocked"] = True
                finally:
                    race["reconstructing"] = False
            return real_read(handle, path)

        with mock.patch.object(
            windows_junction,
            "_read_reparse_payload_handle",
            side_effect=read_with_reconstruction,
        ):
            evidence = create_mod_projection(source, link)

        self.assertTrue(race["attempted"])
        self.assertTrue(race["blocked"])
        self.assertEqual(source.resolve(strict=True), evidence.target_path)

    def test_projection_open_failure_leaves_unknown_identity_untouched(self):
        source = self.root / "source"
        stage_root = self.root / "stage"
        link = stage_root / "Projected"
        source.mkdir()
        stage_root.mkdir()
        real_open = windows_junction._open_no_follow

        def fail_link_open(path, desired_access=0, **kwargs):
            if Path(path) == link:
                raise PermissionError("injected exact-identity failure")
            return real_open(path, desired_access, **kwargs)

        with mock.patch.object(
            windows_junction,
            "_open_no_follow",
            side_effect=fail_link_open,
        ):
            with self.assertRaisesRegex(
                ContainmentSafetyError,
                "identity was never established",
            ):
                create_mod_projection(source, link)

        self.assertTrue(link.is_dir())
        self.assertEqual([], list(link.iterdir()))

    def test_projection_identity_read_failure_closes_handle_and_leaves_path(self):
        source = self.root / "source"
        stage_root = self.root / "stage"
        link = stage_root / "Projected"
        source.mkdir()
        stage_root.mkdir()
        real_information = windows_junction._handle_information

        def fail_link_identity(handle, path):
            if Path(path) == link:
                raise OSError("injected file-id failure")
            return real_information(handle, path)

        with mock.patch.object(
            windows_junction,
            "_handle_information",
            side_effect=fail_link_identity,
        ):
            with self.assertRaisesRegex(
                ContainmentSafetyError,
                "identity was never established",
            ):
                create_mod_projection(source, link)

        self.assertTrue(link.is_dir())
        link.rmdir()
        self.assertFalse(link.exists())

    def test_projection_readback_cleanup_failure_is_explicit_and_retains_link(self):
        source = self.root / "source"
        stage_root = self.root / "stage"
        link = stage_root / "Projected"
        source.mkdir()
        stage_root.mkdir()

        def fail_disposition(*args):
            ctypes.set_last_error(5)
            return False

        with (
            mock.patch.object(
                windows_junction,
                "_inspect_junction_handle",
                side_effect=ContainmentSafetyError("injected readback failure"),
            ),
            mock.patch.object(
                windows_junction._kernel32,
                "SetFileInformationByHandle",
                side_effect=fail_disposition,
            ),
        ):
            with self.assertRaisesRegex(
                ContainmentSafetyError,
                "readback failure.*exact-handle cleanup also failed",
            ):
                create_mod_projection(source, link)

        self.assertTrue(link.exists())

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

        real_create = windows_junction._create_mod_projection_retained
        real_delete = windows_junction._delete_exact_projection
        failure_started = False
        rollback_deletions = []

        def create_or_fail(source_mod, staging_mod):
            nonlocal failure_started
            if Path(source_mod).name == "Gamma":
                failure_started = True
                raise OSError("injected third projection failure")
            return real_create(source_mod, staging_mod)

        def delete_and_record(retained):
            rollback_deletions.append(retained.pinned.path.name)
            return real_delete(retained)

        with (
            mock.patch.object(
                windows_junction,
                "_create_mod_projection_retained",
                side_effect=create_or_fail,
            ),
            mock.patch.object(
                windows_junction,
                "_delete_exact_projection",
                side_effect=delete_and_record,
            ),
        ):
            with self.assertRaisesRegex(OSError, "third projection failure"):
                build_projection(source_mods, stage_mods)

        self.assertEqual(["Beta", "Alpha"], rollback_deletions)
        self.assertEqual([], list(stage_mods.iterdir()))
        for name in ("Alpha", "Beta", "Gamma"):
            self.assertEqual(
                name.encode("ascii"),
                (source_mods / name / "marker.txt").read_bytes(),
            )

    def test_build_projection_blocks_identical_reconstruction_before_rollback(self):
        source_mods = self.root / "source"
        stage_mods = self.root / "stage"
        source_mods.mkdir()
        stage_mods.mkdir()
        for name in ("Alpha", "Beta", "Gamma"):
            mod = source_mods / name
            mod.mkdir()
            (mod / "marker.txt").write_bytes(name.encode("ascii"))

        alpha_link = stage_mods / "Alpha"
        real_ioctl = windows_junction._kernel32.DeviceIoControl
        real_create = windows_junction.create_mod_projection
        state = {
            "set_calls": 0,
            "attempted": False,
            "blocked": False,
            "reconstructing": False,
        }

        def fail_third_set(*args):
            control_code = args[1]
            if control_code == windows_junction._FSCTL_SET_REPARSE_POINT:
                if not state["reconstructing"]:
                    state["set_calls"] += 1
                if state["set_calls"] == 3 and not state["attempted"]:
                    state["attempted"] = True
                    try:
                        alpha_link.rmdir()
                        state["reconstructing"] = True
                        real_create(source_mods / "Alpha", alpha_link)
                    except PermissionError:
                        state["blocked"] = True
                    finally:
                        state["reconstructing"] = False
                    ctypes.set_last_error(5)
                    return False
            return real_ioctl(*args)

        with mock.patch.object(
            windows_junction._kernel32,
            "DeviceIoControl",
            side_effect=fail_third_set,
        ):
            with self.assertRaisesRegex(OSError, "FSCTL_SET_REPARSE_POINT"):
                build_projection(source_mods, stage_mods)

        self.assertTrue(state["attempted"])
        self.assertTrue(state["blocked"])
        self.assertEqual([], list(stage_mods.iterdir()))
        for name in ("Alpha", "Beta", "Gamma"):
            self.assertEqual(
                name.encode("ascii"),
                (source_mods / name / "marker.txt").read_bytes(),
            )

    def test_build_projection_rollback_leaves_a_changed_junction_untouched(self):
        source_mods = self.root / "source"
        stage_mods = self.root / "stage"
        source_mods.mkdir()
        stage_mods.mkdir()
        for name in ("Alpha", "Beta", "Gamma"):
            mod = source_mods / name
            mod.mkdir()
            (mod / "marker.txt").write_bytes(name.encode("ascii"))

        real_create = windows_junction._create_mod_projection_retained
        real_inspect = windows_junction._inspect_junction_handle
        failure_started = False

        def create_then_fail(source_mod, staging_mod):
            nonlocal failure_started
            if Path(source_mod).name == "Gamma":
                failure_started = True
                raise OSError("injected third projection failure")
            return real_create(source_mod, staging_mod)

        def changed_readback(path, handle):
            evidence = real_inspect(path, handle)
            if failure_started and Path(path).name == "Alpha":
                return replace(evidence, reparse_payload_sha256="0" * 64)
            return evidence

        with (
            mock.patch.object(
                windows_junction,
                "_create_mod_projection_retained",
                side_effect=create_then_fail,
            ),
            mock.patch.object(
                windows_junction,
                "_inspect_junction_handle",
                side_effect=changed_readback,
            ),
        ):
            with self.assertRaisesRegex(
                ContainmentSafetyError,
                "exact rollback failed.*Alpha",
            ):
                build_projection(source_mods, stage_mods)

        self.assertFalse((stage_mods / "Beta").exists())
        changed = inspect_junction(stage_mods / "Alpha")
        self.assertEqual((source_mods / "Alpha").resolve(strict=True), changed.target_path)
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

    def test_replacement_quarantine_binds_outputs_and_move_to_one_pinned_tree(self):
        stage, _source, quarantine = self._adoption_roots()
        candidate = stage / "Protected Existing"
        (candidate / "meshes").mkdir(parents=True)
        (candidate / "meshes" / "canary.bin").write_bytes(b"replacement")
        (candidate / "meshes" / "new.bin").write_bytes(b"new")
        (candidate / "meta.ini").write_bytes(b"[General]\n")

        result = quarantine_replacement_tree(
            stage_mods=stage,
            expected_name="Protected Existing",
            quarantine_root=quarantine,
        )

        self.assertEqual(
            ("meshes/canary.bin", "meshes/new.bin", "meta.ini"),
            result.output_names,
        )
        self.assertEqual(result.before_tree, result.after_tree)
        self.assertFalse(candidate.exists())
        self.assertEqual(
            b"replacement",
            (result.destination_path / "meshes" / "canary.bin").read_bytes(),
        )

    def test_replacement_quarantine_blocks_root_swap_at_rename_boundary(self):
        stage, _source, quarantine = self._adoption_roots()
        candidate = stage / "Protected Existing"
        held = self.root / "held-root"
        candidate.mkdir()
        (candidate / "marker.txt").write_bytes(b"original")
        real_set_information = windows_junction._kernel32.SetFileInformationByHandle
        race = {"attempted": False, "blocked": False}

        def set_information_with_root_swap(handle, information_class, information, size):
            if information_class == windows_junction._FILE_RENAME_INFO_CLASS:
                race["attempted"] = True
                try:
                    candidate.rename(held)
                    candidate.mkdir()
                    (candidate / "marker.txt").write_bytes(b"replacement")
                except PermissionError:
                    race["blocked"] = True
            return real_set_information(handle, information_class, information, size)

        with mock.patch.object(
            windows_junction._kernel32,
            "SetFileInformationByHandle",
            side_effect=set_information_with_root_swap,
        ):
            result = quarantine_replacement_tree(
                stage_mods=stage,
                expected_name="Protected Existing",
                quarantine_root=quarantine,
            )

        self.assertTrue(race["attempted"])
        self.assertTrue(race["blocked"])
        self.assertFalse(held.exists())
        self.assertFalse(candidate.exists())
        self.assertEqual(b"original", (result.destination_path / "marker.txt").read_bytes())

    def test_replacement_quarantine_detects_descendant_swap_across_root_move(self):
        stage, _source, quarantine = self._adoption_roots()
        candidate = stage / "Protected Existing"
        child = candidate / "meshes"
        held = self.root / "held-child"
        child.mkdir(parents=True)
        (child / "canary.bin").write_bytes(b"original")
        real_quarantine = windows_junction._quarantine_pinned_tree
        race = {"attempted": False, "blocked": False}

        def quarantine_with_descendant_swap(tree, quarantine_root):
            race["attempted"] = True
            try:
                child.rename(held)
                child.mkdir()
                (child / "canary.bin").write_bytes(b"replacement")
            except PermissionError:
                race["blocked"] = True
            return real_quarantine(tree, quarantine_root)

        with mock.patch.object(
            windows_junction,
            "_quarantine_pinned_tree",
            side_effect=quarantine_with_descendant_swap,
        ):
            with self.assertRaisesRegex(
                ContainmentSafetyError,
                "member identity changed|membership changed",
            ):
                quarantine_replacement_tree(
                    stage_mods=stage,
                    expected_name="Protected Existing",
                    quarantine_root=quarantine,
                )

        self.assertTrue(race["attempted"])
        self.assertFalse(race["blocked"])
        self.assertEqual(
            b"original",
            (held / "canary.bin").read_bytes(),
        )
        self.assertEqual(
            b"replacement",
            (
                quarantine
                / "Protected Existing"
                / "meshes"
                / "canary.bin"
            ).read_bytes(),
        )

    def test_redirected_scenario_quarantine_is_rejected_without_moving_candidate(self):
        stage, _source, quarantine_parent = self._adoption_roots()
        candidate = stage / "Protected Existing"
        external = self.root / "external-quarantine"
        candidate.mkdir()
        (candidate / "marker.txt").write_bytes(b"candidate")
        external.mkdir()
        scenario_quarantine = quarantine_parent / "ReplaceExisting"
        create_mod_projection(external, scenario_quarantine)

        with self.assertRaisesRegex(ContainmentSafetyError, "reparse"):
            ensure_direct_subdirectory(quarantine_parent, "ReplaceExisting")
        with self.assertRaisesRegex(ContainmentSafetyError, "reparse"):
            quarantine_replacement_tree(
                stage_mods=stage,
                expected_name="Protected Existing",
                quarantine_root=scenario_quarantine,
            )

        self.assertEqual(b"candidate", (candidate / "marker.txt").read_bytes())
        self.assertEqual([], list(external.iterdir()))

    def test_replacement_reparse_rejection_quarantines_and_releases_all_handles(self):
        stage, _source, quarantine = self._adoption_roots()
        candidate = stage / "Protected Existing"
        external = self.root / "external-payload"
        candidate.mkdir()
        external.mkdir()
        (candidate / "marker.txt").write_bytes(b"candidate")
        (external / "outside.txt").write_bytes(b"outside")
        create_mod_projection(external, candidate / "redirect")

        with self.assertRaisesRegex(ContainmentSafetyError, "reparse descendant"):
            quarantine_replacement_tree(
                stage_mods=stage,
                expected_name="Protected Existing",
                quarantine_root=quarantine,
            )

        quarantined = quarantine / "Protected Existing"
        released = quarantine / "Released Replacement"
        self.assertFalse(candidate.exists())
        quarantined.rename(released)
        self.assertEqual(b"candidate", (released / "marker.txt").read_bytes())
        self.assertEqual(
            external.resolve(strict=True),
            inspect_junction(released / "redirect").target_path,
        )

    def test_replacement_quarantine_collision_is_no_replace_and_leaves_candidate(self):
        stage, _source, quarantine = self._adoption_roots()
        candidate = stage / "Protected Existing"
        collision = quarantine / "Protected Existing"
        candidate.mkdir()
        collision.mkdir()
        (candidate / "marker.txt").write_bytes(b"candidate")
        (collision / "marker.txt").write_bytes(b"existing")

        with self.assertRaisesRegex(ContainmentSafetyError, "quarantine collision"):
            quarantine_replacement_tree(
                stage_mods=stage,
                expected_name="Protected Existing",
                quarantine_root=quarantine,
            )

        self.assertEqual(b"candidate", (candidate / "marker.txt").read_bytes())
        self.assertEqual(b"existing", (collision / "marker.txt").read_bytes())

    def test_exact_object_quarantine_uses_pinned_no_replace_move(self):
        stage, _source, quarantine = self._adoption_roots()
        candidate = stage / "Unexpected Backup"
        candidate.mkdir()
        (candidate / "marker.txt").write_bytes(b"candidate")

        destination = quarantine_exact_object(candidate, quarantine)

        self.assertEqual(quarantine / candidate.name, destination)
        self.assertFalse(candidate.exists())
        self.assertEqual(b"candidate", (destination / "marker.txt").read_bytes())

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

    def test_late_junction_during_pin_construction_quarantines_exact_candidate(self):
        stage, source, quarantine = self._adoption_roots()
        candidate = stage / "Expected"
        late = candidate / "late"
        external = self.root / "external"
        candidate.mkdir()
        external.mkdir()
        (candidate / "inside.txt").write_bytes(b"inside")
        (external / "outside.txt").write_bytes(b"outside")
        set_low_integrity_tree(candidate)
        external_integrity = inspect_path_integrity(external)
        real_scandir = windows_junction.os.scandir
        race = {
            "inserted": False,
            "swapped": False,
            "external_traversed": False,
        }

        def scandir_with_late_swap(path):
            entries = real_scandir(path)
            if Path(path) == candidate and not race["inserted"]:
                try:
                    snapshot = tuple(entries)
                finally:
                    entries.close()
                late.mkdir()
                race["inserted"] = True
                late.rmdir()
                create_mod_projection(external, late)
                race["swapped"] = True
                return iter(snapshot)
            if Path(path) == late and race["swapped"]:
                race["external_traversed"] = True
            return entries

        with (
            mock.patch.object(
                windows_junction.os,
                "scandir",
                side_effect=scandir_with_late_swap,
            ),
            mock.patch.object(
                windows_junction,
                "set_medium_integrity_entries",
                wraps=windows_junction.set_medium_integrity_entries,
            ) as normalize,
        ):
            with self.assertRaisesRegex(ContainmentSafetyError, "membership changed"):
                adopt_unique_staged_mod(
                    stage_mods=stage,
                    source_mods=source,
                    expected_name="Expected",
                    before_names=(),
                    quarantine_root=quarantine,
                )

        self.assertTrue(race["inserted"])
        self.assertTrue(race["swapped"])
        self.assertFalse(race["external_traversed"])
        normalize.assert_not_called()
        self.assertFalse(candidate.exists())
        self.assertEqual(
            b"inside",
            (quarantine / "Expected" / "inside.txt").read_bytes(),
        )
        self.assertEqual(
            external.resolve(strict=True),
            inspect_junction(quarantine / "Expected" / "late").target_path,
        )
        self.assertEqual(external_integrity, inspect_path_integrity(external))
        self.assertEqual(b"outside", (external / "outside.txt").read_bytes())

    def test_initial_tree_classification_failure_quarantines_then_closes_root_pin(self):
        stage, source, quarantine = self._adoption_roots()
        candidate = stage / "Expected"
        candidate.mkdir()
        (candidate / "inside.txt").write_bytes(b"inside")
        real_attribute_tag = windows_junction._attribute_tag_for_handle
        real_close_handle = windows_junction._close_handle
        real_rename = windows_junction._rename_pinned_object
        observed = {
            "candidate_classifications": 0,
            "candidate_handle": None,
            "quarantine_saw_live_handle": False,
            "candidate_handle_closes": 0,
        }

        def fail_initial_tree_classification(handle, path):
            if Path(path) == candidate:
                observed["candidate_classifications"] += 1
                if observed["candidate_classifications"] == 3:
                    observed["candidate_handle"] = handle
                    raise OSError("injected initial root classification failure")
            return real_attribute_tag(handle, path)

        def record_close(handle):
            if handle == observed["candidate_handle"]:
                observed["candidate_handle_closes"] += 1
            return real_close_handle(handle)

        def require_live_root_for_quarantine(pinned, destination, destination_parent):
            if pinned.path == candidate:
                self.assertEqual(observed["candidate_handle"], pinned.handle)
                self.assertEqual(0, observed["candidate_handle_closes"])
                observed["quarantine_saw_live_handle"] = True
            return real_rename(pinned, destination, destination_parent)

        with (
            mock.patch.object(
                windows_junction,
                "_attribute_tag_for_handle",
                side_effect=fail_initial_tree_classification,
            ),
            mock.patch.object(
                windows_junction,
                "_close_handle",
                side_effect=record_close,
            ),
            mock.patch.object(
                windows_junction,
                "_rename_pinned_object",
                side_effect=require_live_root_for_quarantine,
            ),
        ):
            with self.assertRaisesRegex(
                ContainmentSafetyError,
                "initial root classification failure",
            ):
                adopt_unique_staged_mod(
                    stage_mods=stage,
                    source_mods=source,
                    expected_name="Expected",
                    before_names=(),
                    quarantine_root=quarantine,
                )

        self.assertEqual(3, observed["candidate_classifications"])
        self.assertTrue(observed["quarantine_saw_live_handle"])
        self.assertEqual(1, observed["candidate_handle_closes"])
        self.assertFalse(candidate.exists())
        self.assertEqual(b"inside", (quarantine / "Expected" / "inside.txt").read_bytes())

    def test_pin_first_type_validation_uses_the_retained_handle_not_direntry_stat(self):
        stage, source, quarantine = self._adoption_roots()
        candidate = stage / "Expected"
        candidate.mkdir()
        (candidate / "inside.txt").write_bytes(b"inside")
        set_low_integrity_tree(candidate)
        real_scandir = windows_junction.os.scandir

        class EntryWithoutStat:
            def __init__(self, entry):
                self.name = entry.name
                self.path = entry.path

            def stat(self, *, follow_symlinks=True):
                raise AssertionError("DirEntry.stat must not classify a retained child")

        def scandir_without_stat(path):
            entries = real_scandir(path)
            if Path(path) != candidate:
                return entries
            try:
                wrapped = tuple(EntryWithoutStat(entry) for entry in entries)
            finally:
                entries.close()
            return iter(wrapped)

        with mock.patch.object(
            windows_junction.os,
            "scandir",
            side_effect=scandir_without_stat,
        ):
            result = adopt_unique_staged_mod(
                stage_mods=stage,
                source_mods=source,
                expected_name="Expected",
                before_names=(),
                quarantine_root=quarantine,
            )

        self.assertEqual(result.before_tree, result.after_tree)
        self.assertEqual(b"inside", (source / "Expected" / "inside.txt").read_bytes())

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

    def test_wrong_baseline_quarantine_uses_the_classified_junction_handle(self):
        stage, source, quarantine = self._adoption_roots()
        protected = source / "Protected Existing"
        wrong_target = self.root / "wrong-direct-target"
        baseline = stage / "Protected Existing"
        protected.mkdir()
        wrong_target.mkdir()
        (protected / "marker.txt").write_bytes(b"protected")
        (wrong_target / "marker.txt").write_bytes(b"wrong")
        create_mod_projection(wrong_target, baseline)
        candidate = stage / "Expected"
        candidate.mkdir()
        (candidate / "marker.txt").write_bytes(b"candidate")
        real_inspect_handle = windows_junction._inspect_junction_handle
        race = {"attempted": False, "blocked": False}

        def inspect_then_replace(path, handle):
            evidence = real_inspect_handle(path, handle)
            if Path(path) == baseline and not race["attempted"]:
                race["attempted"] = True
                try:
                    baseline.rmdir()
                    baseline.mkdir()
                    (baseline / "marker.txt").write_bytes(b"replacement")
                except PermissionError:
                    race["blocked"] = True
            return evidence

        with mock.patch.object(
            windows_junction,
            "_inspect_junction_handle",
            side_effect=inspect_then_replace,
        ):
            with self.assertRaisesRegex(ContainmentSafetyError, "changed staging projection"):
                adopt_unique_staged_mod(
                    stage_mods=stage,
                    source_mods=source,
                    expected_name="Expected",
                    before_names=("Protected Existing",),
                    quarantine_root=quarantine,
                )

        self.assertTrue(race["attempted"])
        self.assertTrue(race["blocked"])
        quarantined = inspect_junction(quarantine / "Protected Existing")
        self.assertEqual(wrong_target.resolve(strict=True), quarantined.target_path)
        self.assertEqual(b"wrong", (wrong_target / "marker.txt").read_bytes())
        self.assertEqual(b"protected", (protected / "marker.txt").read_bytes())

    def test_valid_baseline_handle_is_retained_through_candidate_normalization(self):
        stage, source, quarantine = self._adoption_roots()
        protected = source / "Protected Existing"
        baseline = stage / "Protected Existing"
        protected.mkdir()
        (protected / "marker.txt").write_bytes(b"protected")
        create_mod_projection(protected, baseline)
        candidate = stage / "Expected"
        candidate.mkdir()
        (candidate / "marker.txt").write_bytes(b"candidate")
        set_low_integrity_tree(candidate)
        real_normalize = windows_junction.set_medium_integrity_entries
        race = {"attempted": False, "blocked": False}

        def normalize_with_baseline_replacement(paths):
            race["attempted"] = True
            try:
                baseline.rmdir()
                baseline.mkdir()
                (baseline / "marker.txt").write_bytes(b"replacement")
            except PermissionError:
                race["blocked"] = True
            return real_normalize(paths)

        with mock.patch.object(
            windows_junction,
            "set_medium_integrity_entries",
            side_effect=normalize_with_baseline_replacement,
        ):
            result = adopt_unique_staged_mod(
                stage_mods=stage,
                source_mods=source,
                expected_name="Expected",
                before_names=("Protected Existing",),
                quarantine_root=quarantine,
            )

        self.assertTrue(race["attempted"])
        self.assertTrue(race["blocked"])
        self.assertEqual(result.before_tree, result.after_tree)
        baseline_evidence = inspect_junction(baseline)
        self.assertEqual(protected.resolve(strict=True), baseline_evidence.target_path)
        self.assertEqual(b"protected", (protected / "marker.txt").read_bytes())

    def test_early_reparse_rejection_quarantines_the_classified_handle(self):
        stage, source, quarantine = self._adoption_roots()
        external = self.root / "external"
        candidate = stage / "Expected"
        external.mkdir()
        (external / "outside.txt").write_bytes(b"outside")
        create_mod_projection(external, candidate)
        real_attribute_tag = windows_junction._attribute_tag_for_handle
        race = {"attempted": False, "blocked": False}

        def classify_then_replace(handle, path):
            attributes = real_attribute_tag(handle, path)
            if Path(path) == candidate and not race["attempted"]:
                race["attempted"] = True
                try:
                    candidate.rmdir()
                    candidate.mkdir()
                    (candidate / "marker.txt").write_bytes(b"replacement")
                except PermissionError:
                    race["blocked"] = True
            return attributes

        with mock.patch.object(
            windows_junction,
            "_attribute_tag_for_handle",
            side_effect=classify_then_replace,
        ):
            with self.assertRaisesRegex(
                ContainmentSafetyError,
                "not a direct regular directory",
            ):
                adopt_unique_staged_mod(
                    stage_mods=stage,
                    source_mods=source,
                    expected_name="Expected",
                    before_names=(),
                    quarantine_root=quarantine,
                )

        self.assertTrue(race["attempted"])
        self.assertTrue(race["blocked"])
        quarantined = inspect_junction(quarantine / "Expected")
        self.assertEqual(external.resolve(strict=True), quarantined.target_path)
        self.assertEqual(b"outside", (external / "outside.txt").read_bytes())

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
        real_stable_identity = windows_junction._stable_pinned_tree_identity
        calls = 0
        blocked = False

        def mutate_before_second_identity(tree, *, required_equal_passes: int):
            nonlocal calls, blocked
            calls += 1
            if calls == 2:
                try:
                    (tree.root.path / "marker.txt").write_bytes(b"after")
                except PermissionError:
                    blocked = True
            return real_stable_identity(tree, required_equal_passes=required_equal_passes)

        with mock.patch.object(
            windows_junction,
            "_stable_pinned_tree_identity",
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
