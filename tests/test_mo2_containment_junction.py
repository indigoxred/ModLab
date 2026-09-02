import hashlib
import ctypes
from dataclasses import replace
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from modlab.platform import windows_exact_fs
from modlab.validation import windows_junction
from modlab.validation.windows_integrity import (
    IntegrityLevel,
    inspect_path_integrity,
    set_low_integrity_tree,
)
from modlab.validation.windows_junction import (
    ContainmentSafetyError,
    IO_REPARSE_TAG_MOUNT_POINT,
    JunctionOwnershipError,
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
        real_set_information = windows_exact_fs._ntdll.NtSetInformationFile
        race = {"attempted": False, "blocked": False}

        def set_information_with_root_swap(handle, io_status, information, size, information_class):
            if information_class == windows_exact_fs._FILE_RENAME_INFORMATION_CLASS:
                race["attempted"] = True
                try:
                    candidate.rename(held)
                    candidate.mkdir()
                    (candidate / "marker.txt").write_bytes(b"replacement")
                except PermissionError:
                    race["blocked"] = True
            return real_set_information(handle, io_status, information, size, information_class)

        with mock.patch.object(
            windows_exact_fs._ntdll,
            "NtSetInformationFile",
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
        real_set_information = windows_exact_fs._ntdll.NtSetInformationFile
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

        def set_information_with_replacement(handle, io_status, information, size, information_class):
            if information_class == windows_exact_fs._FILE_RENAME_INFORMATION_CLASS:
                attempt_replacement()
            return real_set_information(handle, io_status, information, size, information_class)

        with (
            mock.patch.object(
                windows_junction,
                "set_medium_integrity_entries",
                side_effect=OSError("injected normalization failure"),
            ),
            mock.patch.object(
                windows_exact_fs._ntdll,
                "NtSetInformationFile",
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

    def test_legacy_pinned_close_failure_keeps_live_handle_ownership(self):
        pinned = windows_junction._PinnedObject(self.root / "retained", 123, (1, 2))

        def fail_close(_handle):
            ctypes.set_last_error(5)
            return False

        with mock.patch.object(
            windows_junction._kernel32,
            "CloseHandle",
            side_effect=fail_close,
        ):
            with self.assertRaisesRegex(OSError, "CloseHandle failed"):
                pinned.close()

        self.assertEqual(123, pinned.handle)

    def test_shared_exact_rename_adapter_preserves_full_typed_ownership(self):
        owner_type = windows_exact_fs.RetainedObjectOwner
        role = windows_exact_fs.RetainedObjectRole
        candidates = (
            windows_exact_fs.PinnedObject(
                self.root / "candidate-one",
                171,
                windows_exact_fs.PinnedIdentity(1, 1, 0),
            ),
            windows_exact_fs.PinnedObject(
                self.root / "candidate-two",
                172,
                windows_exact_fs.PinnedIdentity(1, 2, 0),
            ),
        )
        ownership = windows_exact_fs.ExactObjectOwnershipError(
            "injected shared ownership",
            owners=tuple(owner_type(role.CANDIDATE, candidate) for candidate in candidates),
        )
        source = windows_junction._PinnedObject(self.root / "source", 173, (1, 3))
        parent = windows_junction._PinnedObject(self.root, 174, (1, 4))

        with mock.patch.object(
            windows_junction,
            "rename_pinned_no_replace",
            side_effect=ownership,
        ):
            with self.assertRaises(JunctionOwnershipError) as raised:
                windows_junction._rename_pinned_object(
                    source,
                    self.root / "destination",
                    parent,
                )

        self.assertIs(ownership, raised.exception.exact_ownership)
        self.assertEqual(ownership.owners, raised.exception.owners)
        self.assertEqual(candidates, raised.exception.pins)

        local = windows_junction._PinnedObject(self.root / "local-owner", 175, (1, 5))

        def fail_local_close(pinned):
            if pinned is local:
                raise OSError("injected local close failure")
            pinned.handle = 0

        with mock.patch.object(
            windows_junction._PinnedObject,
            "close",
            new=fail_local_close,
        ):
            with self.assertRaises(JunctionOwnershipError) as aggregated:
                windows_junction._close_retained_owners(
                    (raised.exception, local),
                    "shared ownership aggregation",
                )

        self.assertIs(ownership, aggregated.exception.exact_ownership)
        self.assertEqual(ownership.owners, aggregated.exception.owners)
        self.assertEqual((*candidates, local), aggregated.exception.pins)

        def close_local(pinned):
            self.assertIs(local, pinned)
            pinned.handle = 0

        with (
            mock.patch.object(ownership, "resolve"),
            mock.patch.object(
                windows_junction._PinnedObject,
                "close",
                new=close_local,
            ),
        ):
            with self.assertRaises(JunctionOwnershipError) as unresolved:
                aggregated.exception.resolve()
        self.assertIs(ownership, unresolved.exception.exact_ownership)
        self.assertEqual(ownership.owners, unresolved.exception.owners)

        def resolve_exact():
            for candidate in candidates:
                candidate.handle = 0

        with (
            mock.patch.object(
                ownership,
                "resolve",
                side_effect=resolve_exact,
            ) as exact_resolver,
            mock.patch.object(
                windows_junction._PinnedObject,
                "close",
                new=close_local,
            ),
        ):
            unresolved.exception.resolve()
        exact_resolver.assert_called_once_with()
        self.assertEqual(0, local.handle)

    def test_pinned_tree_close_attempts_every_pin_and_supports_exact_retry(self):
        for position, failing_handle in (("first", 93), ("middle", 92), ("final", 91)):
            with self.subTest(position=position):
                root = windows_junction._PinnedObject(self.root / "root", 91, (1, 1))
                earlier = windows_junction._PinnedObject(self.root / "earlier", 92, (1, 2))
                later = windows_junction._PinnedObject(self.root / "later", 93, (1, 3))
                tree = windows_junction._PinnedTree(
                    root,
                    [
                        windows_junction._PinnedEntry("earlier", False, earlier),
                        windows_junction._PinnedEntry("later", False, later),
                    ],
                    self.root,
                )
                pins = (later, earlier, root)

                def close_once(handle):
                    if handle == failing_handle:
                        raise OSError("injected close failure")

                with mock.patch.object(
                    windows_junction,
                    "_close_handle",
                    side_effect=close_once,
                ):
                    with self.assertRaises(JunctionOwnershipError) as raised:
                        tree.close()

                failing = next(pin for pin in pins if pin.handle == failing_handle)
                self.assertEqual((failing,), raised.exception.pins)
                self.assertEqual(
                    [failing_handle],
                    [pin.handle for pin in pins if pin.handle],
                )

                with mock.patch.object(
                    windows_junction,
                    "_close_handle",
                    side_effect=lambda _handle: None,
                ):
                    raised.exception.resolve()
                self.assertEqual(0, failing.handle)

    def test_changed_baseline_cleanup_attempts_every_close_and_retains_only_failures(self):
        stage, source, quarantine = self._adoption_roots()
        protected = source / "Protected Existing"
        wrong_target = self.root / "wrong-target"
        baseline = stage / "Protected Existing"
        candidate = stage / "Expected"
        protected.mkdir()
        wrong_target.mkdir()
        (wrong_target / "marker.txt").write_bytes(b"wrong")
        create_mod_projection(wrong_target, baseline)
        candidate.mkdir()
        (candidate / "marker.txt").write_bytes(b"candidate")

        real_pin = windows_junction._pin_object
        real_close = windows_junction._close_handle
        top_level_pins = []
        failed_once = False

        def record_top_level_pin(path, *args, **kwargs):
            pinned = real_pin(path, *args, **kwargs)
            if Path(path).parent == stage:
                top_level_pins.append(pinned)
            return pinned

        def fail_baseline_close_once(handle):
            nonlocal failed_once
            baseline_pin = next(
                (pinned for pinned in top_level_pins if pinned.path.name == baseline.name),
                None,
            )
            if baseline_pin is not None and handle == baseline_pin.handle and not failed_once:
                failed_once = True
                raise OSError("injected baseline close failure")
            return real_close(handle)

        raised = None
        try:
            with (
                mock.patch.object(
                    windows_junction,
                    "_pin_object",
                    side_effect=record_top_level_pin,
                ),
                mock.patch.object(
                    windows_junction,
                    "_close_handle",
                    side_effect=fail_baseline_close_once,
                ),
            ):
                with self.assertRaises(JunctionOwnershipError) as caught:
                    adopt_unique_staged_mod(
                        stage_mods=stage,
                        source_mods=source,
                        expected_name="Expected",
                        before_names=("Protected Existing",),
                        quarantine_root=quarantine,
                    )
                raised = caught.exception

            self.assertTrue(failed_once)
            baseline_pin = next(
                pinned for pinned in top_level_pins if pinned.path.name == baseline.name
            )
            candidate_pin = next(
                pinned for pinned in top_level_pins if pinned.path.name == candidate.name
            )
            self.assertEqual((baseline_pin,), raised.pins)
            self.assertNotEqual(0, baseline_pin.handle)
            self.assertEqual(0, candidate_pin.handle)
            self.assertFalse(baseline.exists())
            self.assertFalse(candidate.exists())
            self.assertEqual(
                b"wrong",
                (quarantine / "Protected Existing" / "marker.txt").read_bytes(),
            )
            self.assertEqual(
                b"candidate",
                (quarantine / "Expected" / "marker.txt").read_bytes(),
            )
        finally:
            for pinned in top_level_pins:
                if pinned.handle:
                    real_close(pinned.handle)
                    pinned.handle = 0

    def test_exact_quarantine_finalizers_aggregate_source_and_parent_ownership(self):
        stage, _source, quarantine = self._adoption_roots()
        candidate = stage / "Unexpected"
        destination = quarantine / candidate.name
        candidate.mkdir()
        (candidate / "marker.txt").write_bytes(b"candidate")

        real_pin = windows_junction._pin_object
        real_parent_pin = windows_junction._pin_parent_directory
        real_close = windows_junction._close_handle
        retained = {}

        def record_source(path, *args, **kwargs):
            pinned = real_pin(path, *args, **kwargs)
            if Path(path) == candidate:
                retained["source"] = pinned
            return pinned

        def record_parent(path):
            pinned = real_parent_pin(path)
            retained["parent"] = pinned
            return pinned

        def fail_owned_closes(handle):
            if handle in {
                retained.get("source").handle if retained.get("source") else None,
                retained.get("parent").handle if retained.get("parent") else None,
            }:
                raise OSError("injected retained close failure")
            return real_close(handle)

        raised = None
        try:
            with (
                mock.patch.object(windows_junction, "_pin_object", side_effect=record_source),
                mock.patch.object(
                    windows_junction,
                    "_pin_parent_directory",
                    side_effect=record_parent,
                ),
                mock.patch.object(
                    windows_junction,
                    "_close_handle",
                    side_effect=fail_owned_closes,
                ),
            ):
                with self.assertRaises(JunctionOwnershipError) as caught:
                    quarantine_exact_object(candidate, quarantine)
                raised = caught.exception

            self.assertEqual(
                (retained["parent"], retained["source"]),
                raised.pins,
            )
            self.assertFalse(candidate.exists())
            self.assertEqual(b"candidate", (destination / "marker.txt").read_bytes())
            self.assertNotEqual(0, retained["parent"].handle)
            self.assertNotEqual(0, retained["source"].handle)
        finally:
            for pinned in retained.values():
                if pinned.handle:
                    real_close(pinned.handle)
                    pinned.handle = 0

    def test_tree_quarantine_finalizers_aggregate_root_and_parent_ownership(self):
        stage, _source, quarantine = self._adoption_roots()
        candidate = stage / "Protected Existing"
        destination = quarantine / candidate.name
        candidate.mkdir()
        (candidate / "marker.txt").write_bytes(b"replacement")

        real_pin_tree = windows_junction._pin_tree
        real_parent_pin = windows_junction._pin_parent_directory
        real_close = windows_junction._close_handle
        retained = {}

        def record_tree(path):
            tree = real_pin_tree(path)
            retained["root"] = tree.root
            return tree

        def record_parent(path):
            pinned = real_parent_pin(path)
            retained["parent"] = pinned
            return pinned

        def fail_owned_closes(handle):
            if any(handle == pinned.handle for pinned in retained.values()):
                raise OSError("injected tree-quarantine close failure")
            return real_close(handle)

        try:
            with (
                mock.patch.object(windows_junction, "_pin_tree", side_effect=record_tree),
                mock.patch.object(
                    windows_junction,
                    "_pin_parent_directory",
                    side_effect=record_parent,
                ),
                mock.patch.object(
                    windows_junction,
                    "_close_handle",
                    side_effect=fail_owned_closes,
                ),
            ):
                with self.assertRaises(JunctionOwnershipError) as caught:
                    quarantine_replacement_tree(
                        stage_mods=stage,
                        expected_name="Protected Existing",
                        quarantine_root=quarantine,
                    )

            self.assertEqual(
                (retained["parent"], retained["root"]),
                caught.exception.pins,
            )
            self.assertFalse(candidate.exists())
            self.assertEqual(b"replacement", (destination / "marker.txt").read_bytes())
        finally:
            for pinned in retained.values():
                if pinned.handle:
                    real_close(pinned.handle)
                    pinned.handle = 0

    def test_object_quarantine_body_ownership_unions_exact_candidates_and_parent(self):
        self._assert_quarantine_body_owner_union(tree=False)

    def test_tree_quarantine_body_ownership_unions_exact_candidates_and_parent(self):
        self._assert_quarantine_body_owner_union(tree=True)

    def _assert_quarantine_body_owner_union(self, *, tree: bool):
        stage, _source, quarantine = self._adoption_roots()
        candidate = stage / ("Tree Candidate" if tree else "Object Candidate")
        destination = quarantine / candidate.name
        if tree:
            candidate.mkdir()
            (candidate / "marker.txt").write_bytes(b"moved tree")
            retained_subject = windows_junction._pin_tree(candidate)
            retained_subject.close_descendants()
            pinned = retained_subject.root
        else:
            candidate.write_bytes(b"moved object")
            pinned = windows_junction._pin_object(
                candidate,
                desired_access=(
                    windows_junction._DELETE
                    | windows_junction._GENERIC_READ
                    | windows_junction._FILE_READ_ATTRIBUTES
                ),
                allow_reparse=True,
            )
            retained_subject = pinned

        exact_paths = (
            self.root / f"{'tree' if tree else 'object'}-cleanup-one.tmp",
            self.root / f"{'tree' if tree else 'object'}-cleanup-two.tmp",
        )
        for index, path in enumerate(exact_paths, start=1):
            path.write_bytes(f"exact-{index}".encode("ascii"))
        exact_candidates = tuple(
            windows_exact_fs.pin_direct_object(path, "file") for path in exact_paths
        )
        owner_type = windows_exact_fs.RetainedObjectOwner
        role = windows_exact_fs.RetainedObjectRole
        exact_ownership = windows_exact_fs.ExactObjectOwnershipError(
            "injected active exact candidates",
            owners=(
                owner_type(role.CANDIDATE, exact_candidates[0]),
                owner_type(role.CANDIDATE, exact_candidates[0]),
                owner_type(role.CANDIDATE, exact_candidates[1]),
            ),
        )

        real_rename = windows_junction._rename_pinned_object
        real_parent_pin = windows_junction._pin_parent_directory
        real_local_close = windows_junction._PinnedObject.close
        real_exact_delete = windows_exact_fs.delete_pinned_object
        real_exact_close = windows_exact_fs.PinnedObject.close
        quarantine_parent = None
        parent_failures = 0
        delete_calls = {id(exact): 0 for exact in exact_candidates}

        def move_then_surface_ownership(source, target, parent):
            real_rename(source, target, parent)
            raise JunctionOwnershipError(
                "injected post-move quarantine ownership",
                (pinned, pinned, *exact_candidates),
                exact_ownership=exact_ownership,
            )

        def record_parent(path):
            nonlocal quarantine_parent
            result = real_parent_pin(path)
            quarantine_parent = result
            return result

        def fail_parent_close_twice(local_pin):
            nonlocal parent_failures
            if local_pin is quarantine_parent and parent_failures < 2:
                parent_failures += 1
                raise OSError("injected quarantine parent close failure")
            return real_local_close(local_pin)

        def fail_candidate_delete_once(exact):
            marker = id(exact)
            delete_calls[marker] += 1
            if delete_calls[marker] == 1:
                raise OSError("injected exact candidate delete failure")
            return real_exact_delete(exact)

        try:
            with (
                mock.patch.object(
                    windows_junction,
                    "_rename_pinned_object",
                    side_effect=move_then_surface_ownership,
                ),
                mock.patch.object(
                    windows_junction,
                    "_pin_parent_directory",
                    side_effect=record_parent,
                ),
                mock.patch.object(
                    windows_junction._PinnedObject,
                    "close",
                    new=fail_parent_close_twice,
                ),
                mock.patch.object(
                    windows_exact_fs,
                    "delete_pinned_object",
                    side_effect=fail_candidate_delete_once,
                ),
            ):
                with self.assertRaises(JunctionOwnershipError) as surfaced:
                    if tree:
                        windows_junction._quarantine_pinned_tree(
                            retained_subject,
                            quarantine,
                        )
                    else:
                        windows_junction._quarantine_pinned_objects(
                            (retained_subject,),
                            quarantine,
                        )

                self.assertFalse(candidate.exists())
                if tree:
                    self.assertEqual(
                        b"moved tree",
                        (destination / "marker.txt").read_bytes(),
                    )
                else:
                    self.assertEqual(
                        (hashlib.sha256(b"moved object").hexdigest(), len(b"moved object")),
                        windows_junction._hash_pinned_file(pinned),
                    )

                with self.assertRaises(JunctionOwnershipError) as retry:
                    surfaced.exception.resolve()
                retry.exception.resolve()

            expected = (*exact_candidates, pinned, quarantine_parent)
            self.assertEqual(2, parent_failures)
            self.assertEqual(
                {id(exact): 2 for exact in exact_candidates},
                delete_calls,
            )
            self.assertTrue(all(owner.handle == 0 for owner in expected))
            self.assertTrue(all(not path.exists() for path in exact_paths))
            self.assertFalse(candidate.exists())
            if tree:
                self.assertEqual(b"moved tree", (destination / "marker.txt").read_bytes())
            else:
                self.assertEqual(b"moved object", destination.read_bytes())
        finally:
            for exact in exact_candidates:
                if exact.handle:
                    real_exact_close(exact)
            if pinned.handle:
                real_local_close(pinned)
            if quarantine_parent is not None and quarantine_parent.handle:
                real_local_close(quarantine_parent)

    def test_post_move_adoption_failure_quarantines_exact_tree_and_aggregates_all_finalizers(self):
        stage, source, quarantine = self._adoption_roots()
        protected = source / "Protected Existing"
        baseline = stage / "Protected Existing"
        candidate = stage / "Expected"
        destination = source / "Expected"
        quarantined = quarantine / "Expected"
        protected.mkdir()
        (protected / "protected.txt").write_bytes(b"protected")
        create_mod_projection(protected, baseline)
        (candidate / "child").mkdir(parents=True)
        (candidate / "child" / "marker.txt").write_bytes(b"candidate")
        set_low_integrity_tree(candidate)

        real_identity = windows_junction._stable_pinned_tree_identity
        real_close = windows_junction._PinnedObject.close
        identity_calls = 0
        failed_pins = []

        def fail_after_move(tree, *, required_equal_passes):
            nonlocal identity_calls
            identity_calls += 1
            if identity_calls == 2:
                self.assertEqual(destination, tree.current_path)
                raise OSError("injected post-move adoption failure")
            return real_identity(tree, required_equal_passes=required_equal_passes)

        def fail_each_final_owner(pinned):
            if pinned.path in {source, quarantined, baseline}:
                if all(pinned is not existing for existing in failed_pins):
                    failed_pins.append(pinned)
                raise OSError(f"injected final close failure for {pinned.path.name}")
            return real_close(pinned)

        with (
            mock.patch.object(
                windows_junction,
                "_stable_pinned_tree_identity",
                side_effect=fail_after_move,
            ),
            mock.patch.object(
                windows_junction._PinnedObject,
                "close",
                new=fail_each_final_owner,
            ),
        ):
            with self.assertRaises(JunctionOwnershipError) as caught:
                adopt_unique_staged_mod(
                    stage_mods=stage,
                    source_mods=source,
                    expected_name="Expected",
                    before_names=("Protected Existing",),
                    quarantine_root=quarantine,
                )

        self.assertEqual(2, identity_calls)
        self.assertEqual(3, len(caught.exception.pins))
        self.assertEqual(tuple(failed_pins), caught.exception.pins)
        self.assertFalse(candidate.exists())
        self.assertFalse(destination.exists())
        self.assertEqual(
            b"candidate",
            (quarantined / "child" / "marker.txt").read_bytes(),
        )
        self.assertEqual(
            protected.resolve(strict=True),
            inspect_junction(baseline).target_path,
        )

        caught.exception.resolve()
        self.assertTrue(all(pinned.handle == 0 for pinned in failed_pins))

    def test_post_move_adoption_preserves_quarantine_parent_close_ownership(self):
        stage, source, quarantine = self._adoption_roots()
        candidate = stage / "Expected"
        destination = source / "Expected"
        quarantined = quarantine / "Expected"
        candidate.mkdir()
        (candidate / "marker.txt").write_bytes(b"candidate")
        set_low_integrity_tree(candidate)

        real_identity = windows_junction._stable_pinned_tree_identity
        real_parent_pin = windows_junction._pin_parent_directory
        real_close = windows_junction._PinnedObject.close
        identity_calls = 0
        quarantine_parent = None

        def fail_after_move(tree, *, required_equal_passes):
            nonlocal identity_calls
            identity_calls += 1
            if identity_calls == 2:
                self.assertEqual(destination, tree.current_path)
                raise OSError("injected post-move adoption failure")
            return real_identity(tree, required_equal_passes=required_equal_passes)

        def record_parent(path):
            nonlocal quarantine_parent
            pinned = real_parent_pin(path)
            if Path(path) == quarantine:
                quarantine_parent = pinned
            return pinned

        def fail_quarantine_parent_close(pinned):
            if pinned is quarantine_parent:
                raise OSError("injected quarantine parent close failure")
            return real_close(pinned)

        try:
            with (
                mock.patch.object(
                    windows_junction,
                    "_stable_pinned_tree_identity",
                    side_effect=fail_after_move,
                ),
                mock.patch.object(
                    windows_junction,
                    "_pin_parent_directory",
                    side_effect=record_parent,
                ),
                mock.patch.object(
                    windows_junction._PinnedObject,
                    "close",
                    new=fail_quarantine_parent_close,
                ),
            ):
                with self.assertRaises(JunctionOwnershipError) as caught:
                    adopt_unique_staged_mod(
                        stage_mods=stage,
                        source_mods=source,
                        expected_name="Expected",
                        before_names=(),
                        quarantine_root=quarantine,
                    )

            self.assertEqual((quarantine_parent,), caught.exception.pins)
            self.assertFalse(candidate.exists())
            self.assertFalse(destination.exists())
            self.assertEqual(b"candidate", (quarantined / "marker.txt").read_bytes())
        finally:
            if quarantine_parent is not None and quarantine_parent.handle:
                real_close(quarantine_parent)

    def test_tree_construction_unions_new_leaf_descendant_and_root_across_retries(self):
        tree_path = self.root / "tree-under-construction"
        tree_path.mkdir()
        (tree_path / "a-retained.txt").write_bytes(b"retained")
        (tree_path / "b-clean.txt").write_bytes(b"clean")
        (tree_path / "z-rejected.txt").write_bytes(b"rejected")

        real_pin = windows_junction._pin_object
        real_attributes = windows_junction._attribute_tag_for_handle
        real_close = windows_junction._close_handle
        pins: dict[str, object] = {}
        close_failures: dict[str, int] = {
            "tree-under-construction": 0,
            "a-retained.txt": 0,
            "z-rejected.txt": 0,
        }

        def record_pin(path, *args, **kwargs):
            pinned = real_pin(path, *args, **kwargs)
            pins[Path(path).name] = pinned
            return pinned

        def reject_new_leaf(handle, path):
            attributes, tag = real_attributes(handle, path)
            if Path(path).name == "z-rejected.txt":
                return (
                    attributes | windows_junction._FILE_ATTRIBUTE_REPARSE_POINT,
                    1,
                )
            return attributes, tag

        def fail_owned_closes_twice(handle):
            for name, failure_count in close_failures.items():
                pinned = pins.get(name)
                if (
                    pinned is not None
                    and handle == pinned.handle
                    and failure_count < 2
                ):
                    close_failures[name] += 1
                    raise OSError(f"injected {name} close failure")
            return real_close(handle)

        try:
            with (
                mock.patch.object(
                    windows_junction,
                    "_pin_object",
                    side_effect=record_pin,
                ),
                mock.patch.object(
                    windows_junction,
                    "_attribute_tag_for_handle",
                    side_effect=reject_new_leaf,
                ),
                mock.patch.object(
                    windows_junction,
                    "_close_handle",
                    side_effect=fail_owned_closes_twice,
                ),
            ):
                observed_error = None
                try:
                    windows_junction._pin_tree(tree_path)
                except BaseException as error:
                    observed_error = error

                self.assertIsInstance(observed_error, JunctionOwnershipError)
                ownership = observed_error
                expected = (
                    pins["z-rejected.txt"],
                    pins["a-retained.txt"],
                    pins["tree-under-construction"],
                )
                self.assertEqual(expected, ownership.pins)
                self.assertEqual(0, pins["b-clean.txt"].handle)
                self.assertEqual(
                    (hashlib.sha256(b"retained").hexdigest(), len(b"retained")),
                    windows_junction._hash_pinned_file(pins["a-retained.txt"]),
                )
                self.assertEqual(
                    (hashlib.sha256(b"rejected").hexdigest(), len(b"rejected")),
                    windows_junction._hash_pinned_file(pins["z-rejected.txt"]),
                )

                with self.assertRaises(JunctionOwnershipError) as retry:
                    ownership.resolve()
                self.assertEqual(expected, retry.exception.pins)
                retry.exception.resolve()

            self.assertEqual(
                {
                    "tree-under-construction": 2,
                    "a-retained.txt": 2,
                    "z-rejected.txt": 2,
                },
                close_failures,
            )
            self.assertTrue(all(pinned.handle == 0 for pinned in expected))
        finally:
            for pinned in pins.values():
                if pinned.handle:
                    real_close(pinned.handle)
                    pinned.handle = 0

    def test_rejection_quarantine_unions_parent_and_tree_ownership_across_retries(self):
        stage, _source, quarantine = self._adoption_roots()
        candidate = stage / "Rejected"
        candidate.mkdir()
        (candidate / "marker.txt").write_bytes(b"candidate")
        destination = quarantine / candidate.name

        real_pin = windows_junction._pin_object
        real_parent_pin = windows_junction._pin_parent_directory
        real_attributes = windows_junction._attribute_tag_for_handle
        real_close = windows_junction._PinnedObject.close
        retained: dict[str, object] = {}
        failures = {"root": 0, "parent": 0}

        def record_pin(path, *args, **kwargs):
            pinned = real_pin(path, *args, **kwargs)
            if Path(path) == candidate:
                retained["root"] = pinned
            return pinned

        def record_parent(path):
            pinned = real_parent_pin(path)
            if Path(path) == quarantine:
                retained["parent"] = pinned
            return pinned

        def reject_marker(handle, path):
            attributes, tag = real_attributes(handle, path)
            if Path(path).name == "marker.txt":
                return (
                    attributes | windows_junction._FILE_ATTRIBUTE_REPARSE_POINT,
                    1,
                )
            return attributes, tag

        def fail_owned_closes_twice(pinned):
            for role in ("parent", "root"):
                if pinned is retained.get(role) and failures[role] < 2:
                    failures[role] += 1
                    raise OSError(f"injected rejection {role} close failure")
            return real_close(pinned)

        try:
            with (
                mock.patch.object(windows_junction, "_pin_object", side_effect=record_pin),
                mock.patch.object(
                    windows_junction,
                    "_pin_parent_directory",
                    side_effect=record_parent,
                ),
                mock.patch.object(
                    windows_junction,
                    "_attribute_tag_for_handle",
                    side_effect=reject_marker,
                ),
                mock.patch.object(
                    windows_junction._PinnedObject,
                    "close",
                    new=fail_owned_closes_twice,
                ),
            ):
                observed_error = None
                try:
                    quarantine_replacement_tree(
                        stage_mods=stage,
                        expected_name="Rejected",
                        quarantine_root=quarantine,
                    )
                except BaseException as error:
                    observed_error = error

                self.assertIsInstance(observed_error, JunctionOwnershipError)
                ownership = observed_error
                expected = (retained["parent"], retained["root"])
                self.assertEqual(expected, ownership.pins)
                self.assertFalse(candidate.exists())
                self.assertEqual(b"candidate", (destination / "marker.txt").read_bytes())

                with self.assertRaises(JunctionOwnershipError) as retry:
                    ownership.resolve()
                self.assertEqual(expected, retry.exception.pins)
                retry.exception.resolve()

            self.assertEqual({"root": 2, "parent": 2}, failures)
            self.assertTrue(all(pinned.handle == 0 for pinned in expected))
        finally:
            for pinned in retained.values():
                if pinned.handle:
                    real_close(pinned)

    def test_staging_rejection_unions_quarantine_parent_and_entry_ownership(self):
        stage, source, quarantine = self._adoption_roots()
        first = stage / "First"
        second = stage / "Second"
        first.mkdir()
        second.mkdir()
        (first / "marker.txt").write_bytes(b"first")
        (second / "marker.txt").write_bytes(b"second")

        real_pin = windows_junction._pin_object
        real_parent_pin = windows_junction._pin_parent_directory
        real_close = windows_junction._PinnedObject.close
        retained: dict[str, object] = {}
        failures = {"parent": 0, "entry": 0}

        def record_pin(path, *args, **kwargs):
            pinned = real_pin(path, *args, **kwargs)
            if Path(path) == first:
                retained["entry"] = pinned
            return pinned

        def record_parent(path):
            pinned = real_parent_pin(path)
            if Path(path) == quarantine:
                retained["parent"] = pinned
            return pinned

        def fail_owned_closes_twice(pinned):
            for role in ("parent", "entry"):
                if pinned is retained.get(role) and failures[role] < 2:
                    failures[role] += 1
                    raise OSError(f"injected staging {role} close failure")
            return real_close(pinned)

        try:
            with (
                mock.patch.object(windows_junction, "_pin_object", side_effect=record_pin),
                mock.patch.object(
                    windows_junction,
                    "_pin_parent_directory",
                    side_effect=record_parent,
                ),
                mock.patch.object(
                    windows_junction._PinnedObject,
                    "close",
                    new=fail_owned_closes_twice,
                ),
            ):
                observed_error = None
                try:
                    adopt_unique_staged_mod(
                        stage_mods=stage,
                        source_mods=source,
                        expected_name="Expected",
                        before_names=(),
                        quarantine_root=quarantine,
                    )
                except BaseException as error:
                    observed_error = error

                self.assertIsInstance(observed_error, JunctionOwnershipError)
                ownership = observed_error
                expected = (retained["parent"], retained["entry"])
                self.assertEqual(expected, ownership.pins)
                self.assertEqual([], list(stage.iterdir()))
                self.assertEqual(b"first", (quarantine / "First" / "marker.txt").read_bytes())
                self.assertEqual(b"second", (quarantine / "Second" / "marker.txt").read_bytes())

                with self.assertRaises(JunctionOwnershipError) as retry:
                    ownership.resolve()
                self.assertEqual(expected, retry.exception.pins)
                retry.exception.resolve()

            self.assertEqual({"parent": 2, "entry": 2}, failures)
            self.assertTrue(all(pinned.handle == 0 for pinned in expected))
        finally:
            for pinned in retained.values():
                if pinned.handle:
                    real_close(pinned)

    def test_adoption_unions_prior_and_quarantine_ownership_without_rollback(self):
        stage, source, quarantine = self._adoption_roots()
        candidate = stage / "Expected"
        destination = source / "Expected"
        quarantined = quarantine / "Expected"
        candidate.mkdir()
        (candidate / "marker.txt").write_bytes(b"candidate")
        set_low_integrity_tree(candidate)
        prior_path = self.root / "prior-owner.txt"
        prior_path.write_bytes(b"prior")
        prior = windows_junction._pin_object(
            prior_path,
            desired_access=windows_junction._DELETE | windows_junction._GENERIC_READ,
            allow_reparse=False,
        )

        real_identity = windows_junction._stable_pinned_tree_identity
        real_parent_pin = windows_junction._pin_parent_directory
        real_close = windows_junction._PinnedObject.close
        identity_calls = 0
        quarantine_parent = None
        failures = {"prior": 0, "quarantine": 0}

        def fail_after_move(tree, *, required_equal_passes):
            nonlocal identity_calls
            identity_calls += 1
            if identity_calls == 2:
                self.assertEqual(destination, tree.current_path)
                try:
                    prior.close()
                except OSError as error:
                    raise JunctionOwnershipError(
                        "injected prior adoption ownership",
                        (prior,),
                    ) from error
            return real_identity(tree, required_equal_passes=required_equal_passes)

        def record_parent(path):
            nonlocal quarantine_parent
            pinned = real_parent_pin(path)
            if Path(path) == quarantine:
                quarantine_parent = pinned
            return pinned

        def fail_owned_closes_twice(pinned):
            if pinned is prior and failures["prior"] < 2:
                failures["prior"] += 1
                raise OSError("injected prior close failure")
            if pinned is quarantine_parent and failures["quarantine"] < 2:
                failures["quarantine"] += 1
                raise OSError("injected quarantine parent close failure")
            return real_close(pinned)

        try:
            with (
                mock.patch.object(
                    windows_junction,
                    "_stable_pinned_tree_identity",
                    side_effect=fail_after_move,
                ),
                mock.patch.object(
                    windows_junction,
                    "_pin_parent_directory",
                    side_effect=record_parent,
                ),
                mock.patch.object(
                    windows_junction._PinnedObject,
                    "close",
                    new=fail_owned_closes_twice,
                ),
            ):
                observed_error = None
                try:
                    adopt_unique_staged_mod(
                        stage_mods=stage,
                        source_mods=source,
                        expected_name="Expected",
                        before_names=(),
                        quarantine_root=quarantine,
                    )
                except BaseException as error:
                    observed_error = error

                self.assertIsInstance(observed_error, JunctionOwnershipError)
                ownership = observed_error
                expected = (prior, quarantine_parent)
                self.assertEqual(expected, ownership.pins)
                self.assertFalse(candidate.exists())
                self.assertFalse(destination.exists())
                self.assertEqual(b"candidate", (quarantined / "marker.txt").read_bytes())
                self.assertEqual(
                    (hashlib.sha256(b"prior").hexdigest(), len(b"prior")),
                    windows_junction._hash_pinned_file(prior),
                )

                with self.assertRaises(JunctionOwnershipError) as retry:
                    ownership.resolve()
                self.assertEqual(expected, retry.exception.pins)
                retry.exception.resolve()

            self.assertEqual({"prior": 2, "quarantine": 2}, failures)
            self.assertTrue(all(pinned.handle == 0 for pinned in expected))
        finally:
            for pinned in (prior, quarantine_parent):
                if pinned is not None and pinned.handle:
                    real_close(pinned)

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
