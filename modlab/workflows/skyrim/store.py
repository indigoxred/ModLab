"""Atomic local storage for Skyrim intent and immutable source bytes."""

from __future__ import annotations

import hashlib
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from modlab.recipes.loading import LoadedEnvironmentSource, LoadedRecipeSource
from modlab.workspace import WorkspaceLayout, workspace_layout

from .configuration import (
    ConfigurationChange,
    SkyrimConfigurationFormatError,
    SkyrimEnvironmentConfiguration,
    StoredSourceReference,
    configuration_bytes,
    configuration_changes,
    configuration_from_bytes,
)


class SkyrimEnvironmentNotConfiguredError(LookupError):
    """No Skyrim environment configuration exists in this workspace."""


class SkyrimEnvironmentStoreError(RuntimeError):
    """Stored Skyrim intent cannot be read or changed safely."""


class SkyrimEnvironmentConflictError(SkyrimEnvironmentStoreError):
    """A safe write requires explicit replacement or a fresh snapshot."""

    def __init__(
        self,
        message: str,
        changes: tuple[ConfigurationChange, ...] = (),
    ):
        self.changes = changes
        super().__init__(message)


@dataclass(frozen=True)
class ConfigurationSnapshot:
    configuration: SkyrimEnvironmentConfiguration
    sha256: str
    data: bytes
    path: Path


@dataclass(frozen=True)
class ConfigurationWrite:
    configuration: SkyrimEnvironmentConfiguration
    snapshot: ConfigurationSnapshot
    changed: bool
    paths_written: tuple[Path, ...]


@dataclass(frozen=True)
class SourceRetention:
    reference: StoredSourceReference
    changed: bool
    path: Path


_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class SkyrimEnvironmentStore:
    def __init__(self, workspace_root: Path):
        self.layout = workspace_layout(workspace_root)

    def load(self) -> ConfigurationSnapshot:
        path = self.layout.skyrim_environment_configuration
        if not path.exists() and not path.is_symlink():
            raise SkyrimEnvironmentNotConfiguredError(
                f"Skyrim environment is not configured: {path}"
            )
        self._validate_existing_file(path, "configuration")
        try:
            data = path.read_bytes()
            configuration = configuration_from_bytes(data)
        except (OSError, SkyrimConfigurationFormatError) as error:
            raise SkyrimEnvironmentStoreError(
                f"Cannot load Skyrim environment configuration {path}: {error}"
            ) from error
        return ConfigurationSnapshot(
            configuration=configuration,
            sha256=hashlib.sha256(data).hexdigest(),
            data=data,
            path=path,
        )

    def retain_recipe(self, source: LoadedRecipeSource) -> SourceRetention:
        return self._retain_source(
            source=source,
            directory=self.layout.skyrim_recipes,
            filename="recipe.json",
            label="recipe",
        )

    def retain_target_environment(
        self, source: LoadedEnvironmentSource
    ) -> SourceRetention:
        return self._retain_source(
            source=source,
            directory=self.layout.skyrim_target_environments,
            filename="environment.json",
            label="target environment",
        )

    def write(
        self,
        configuration: SkyrimEnvironmentConfiguration,
        *,
        replace: bool,
    ) -> ConfigurationWrite:
        desired_data = self._validated_configuration_bytes(configuration)
        target = self.layout.skyrim_environment_configuration
        existing: ConfigurationSnapshot | None = None
        if target.exists() or target.is_symlink():
            existing = self.load()
            if existing.configuration == configuration:
                return ConfigurationWrite(
                    configuration=existing.configuration,
                    snapshot=existing,
                    changed=False,
                    paths_written=(),
                )
            changes = configuration_changes(existing.configuration, configuration)
            if not replace:
                raise SkyrimEnvironmentConflictError(
                    "Skyrim environment configuration differs; explicit replacement is required",
                    changes,
                )

        self._atomic_write(target, desired_data, "skyrim-environment")
        snapshot = self._load_written_configuration(configuration, desired_data)
        return ConfigurationWrite(
            configuration=snapshot.configuration,
            snapshot=snapshot,
            changed=True,
            paths_written=(snapshot.path,),
        )

    def compare_and_swap(
        self,
        expected_sha256: str,
        configuration: SkyrimEnvironmentConfiguration,
    ) -> ConfigurationWrite:
        if (
            not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_sha256)
        ):
            raise SkyrimEnvironmentStoreError(
                "expected configuration SHA-256 must be 64 lowercase hexadecimal characters"
            )
        desired_data = self._validated_configuration_bytes(configuration)
        existing = self.load()
        if existing.sha256 != expected_sha256:
            raise SkyrimEnvironmentConflictError(
                "Skyrim environment configuration changed after observation"
            )
        if existing.configuration == configuration:
            return ConfigurationWrite(
                configuration=existing.configuration,
                snapshot=existing,
                changed=False,
                paths_written=(),
            )

        self._atomic_write(existing.path, desired_data, "skyrim-environment-cas")
        snapshot = self._load_written_configuration(configuration, desired_data)
        return ConfigurationWrite(
            configuration=snapshot.configuration,
            snapshot=snapshot,
            changed=True,
            paths_written=(snapshot.path,),
        )

    def _retain_source(
        self,
        *,
        source: LoadedRecipeSource | LoadedEnvironmentSource,
        directory: Path,
        filename: str,
        label: str,
    ) -> SourceRetention:
        actual_sha256 = hashlib.sha256(source.data).hexdigest()
        if actual_sha256 != source.sha256:
            raise SkyrimEnvironmentStoreError(
                f"{label} bytes do not match their recorded SHA-256"
            )
        self._validate_existing_file(source.path, f"{label} source")
        try:
            current_source_data = source.path.read_bytes()
        except OSError as error:
            raise SkyrimEnvironmentStoreError(
                f"Cannot re-read {label} source {source.path}: {error}"
            ) from error
        if current_source_data != source.data:
            raise SkyrimEnvironmentStoreError(
                f"{label} source changed after it was validated"
            )

        target = directory / source.sha256 / filename
        self._validate_target(target)
        reference = StoredSourceReference(
            source_sha256=source.sha256,
            stored_path=target.relative_to(self.layout.root).as_posix(),
        )
        if target.exists() or target.is_symlink():
            self._validate_existing_file(target, f"retained {label}")
            try:
                existing_data = target.read_bytes()
            except OSError as error:
                raise SkyrimEnvironmentStoreError(
                    f"Cannot read retained {label} {target}: {error}"
                ) from error
            if existing_data != source.data:
                raise SkyrimEnvironmentStoreError(
                    f"retained {label} path contains different bytes: {target}"
                )
            return SourceRetention(reference=reference, changed=False, path=target)

        self._atomic_write(target, source.data, f"skyrim-{label.replace(' ', '-')}")
        self._validate_existing_file(target, f"retained {label}")
        try:
            stored_data = target.read_bytes()
        except OSError as error:
            raise SkyrimEnvironmentStoreError(
                f"Cannot verify retained {label} {target}: {error}"
            ) from error
        if stored_data != source.data:
            raise SkyrimEnvironmentStoreError(
                f"retained {label} bytes differ after write"
            )
        return SourceRetention(reference=reference, changed=True, path=target)

    @staticmethod
    def _validated_configuration_bytes(
        configuration: SkyrimEnvironmentConfiguration,
    ) -> bytes:
        try:
            return configuration_bytes(configuration)
        except SkyrimConfigurationFormatError as error:
            raise SkyrimEnvironmentStoreError(
                f"Proposed Skyrim environment configuration is invalid: {error}"
            ) from error

    def _load_written_configuration(
        self,
        expected: SkyrimEnvironmentConfiguration,
        expected_data: bytes,
    ) -> ConfigurationSnapshot:
        snapshot = self.load()
        if snapshot.configuration != expected or snapshot.data != expected_data:
            raise SkyrimEnvironmentStoreError(
                "Stored Skyrim environment configuration differs after write"
            )
        return snapshot

    def _atomic_write(self, target: Path, data: bytes, prefix: str) -> None:
        self._validate_target(target)
        self._prepare_directory(self.layout.jobs)
        self._prepare_directory(target.parent)
        self._validate_target(target)
        staging = self.layout.jobs / f"{prefix}-{uuid.uuid4().hex}.part"
        try:
            with staging.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            self._validate_existing_file(staging, "staging file")
            self._validate_target(target)
            os.replace(staging, target)
        except SkyrimEnvironmentStoreError:
            raise
        except Exception as error:
            raise SkyrimEnvironmentStoreError(
                f"Could not atomically write {target}: {error}"
            ) from error
        finally:
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                pass

    def _prepare_directory(self, directory: Path) -> None:
        self._validate_target(directory)
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise SkyrimEnvironmentStoreError(
                f"Cannot create workspace directory {directory}: {error}"
            ) from error
        self._validate_existing_directory(directory, "workspace directory")

    def _validate_target(self, target: Path) -> None:
        try:
            relative = target.relative_to(self.layout.root)
        except ValueError as error:
            raise SkyrimEnvironmentStoreError(
                f"target is outside the workspace: {target}"
            ) from error
        if not relative.parts:
            raise SkyrimEnvironmentStoreError("workspace root is not a writable target")

        candidates = [self.layout.root]
        current = self.layout.root
        for part in relative.parts:
            current = current / part
            candidates.append(current)
        for candidate in candidates:
            if candidate.exists() or candidate.is_symlink():
                self._reject_redirect(candidate)
                if candidate != target and not candidate.is_dir():
                    raise SkyrimEnvironmentStoreError(
                        f"workspace ancestor is not a directory: {candidate}"
                    )

    def _validate_existing_file(self, path: Path, label: str) -> None:
        self._validate_ancestors(path)
        try:
            metadata = path.lstat()
        except OSError as error:
            raise SkyrimEnvironmentStoreError(
                f"Cannot inspect {label} {path}: {error}"
            ) from error
        self._reject_redirect(path, metadata)
        if not stat.S_ISREG(metadata.st_mode):
            raise SkyrimEnvironmentStoreError(
                f"{label} must be a direct regular file: {path}"
            )

    def _validate_existing_directory(self, path: Path, label: str) -> None:
        self._validate_ancestors(path)
        try:
            metadata = path.lstat()
        except OSError as error:
            raise SkyrimEnvironmentStoreError(
                f"Cannot inspect {label} {path}: {error}"
            ) from error
        self._reject_redirect(path, metadata)
        if not stat.S_ISDIR(metadata.st_mode):
            raise SkyrimEnvironmentStoreError(
                f"{label} must be a direct directory: {path}"
            )

    def _validate_ancestors(self, path: Path) -> None:
        for candidate in path.parents:
            if candidate.exists() or candidate.is_symlink():
                self._reject_redirect(candidate)

    @staticmethod
    def _reject_redirect(path: Path, metadata=None) -> None:
        try:
            metadata = metadata if metadata is not None else path.lstat()
        except OSError as error:
            raise SkyrimEnvironmentStoreError(
                f"Cannot inspect path {path}: {error}"
            ) from error
        if path.is_symlink() or bool(
            getattr(metadata, "st_file_attributes", 0)
            & _FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise SkyrimEnvironmentStoreError(
                f"redirected workspace or source path is not allowed: {path}"
            )
