"""Command-line boundary for ModLab's dependency-free core."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

from .artifacts.model import ArchiveArtifact, ArtifactFinding, ArtifactHealth
from .artifacts.serialization import ArtifactFormatError, artifact_to_dict
from .artifacts.vault import ArtifactNotFoundError, ArchiveImportError, ArchiveVault
from .checkpoints.model import CheckpointFinding, CheckpointHealth
from .checkpoints.serialization import CheckpointFormatError, checkpoint_to_dict
from .checkpoints.store import (
    CheckpointNotFoundError,
    CheckpointStore,
    CheckpointStoreError,
)
from .recipes.loading import RecipeFormatError, load_environment, load_recipe
from .recipes.model import RecipeReview
from .recipes.reviewing import review_recipe
from .recipes.serialization import review_to_dict
from .workspace import default_workspace_root, initialize_workspace


NO_ACTIONS = "No downloads or installation actions were performed."


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="modlab",
        description="Use ModLab's local safety core without changing a game setup.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    workspace = commands.add_parser(
        "workspace",
        help="create or inspect the organized user-data folders",
    )
    workspace_commands = workspace.add_subparsers(
        dest="workspace_command",
        required=True,
    )
    workspace_init = workspace_commands.add_parser(
        "init",
        help="create missing folders without deleting existing content",
    )
    workspace_init.add_argument("--root", type=Path, default=None)

    artifact = commands.add_parser(
        "artifact",
        help="retain and verify local ZIP, 7z, or RAR archives",
    )
    artifact_commands = artifact.add_subparsers(dest="artifact_command", required=True)

    artifact_import = artifact_commands.add_parser(
        "import",
        help="copy an archive into the local vault without extracting or installing it",
    )
    artifact_import.add_argument("source", type=Path)
    artifact_import.add_argument("--workspace", type=Path, default=None)
    artifact_import.add_argument("--source-note", required=True)
    artifact_import.add_argument("--source-url", default=None)
    artifact_import.add_argument("--format", choices=("text", "json"), default="text")

    artifact_list = artifact_commands.add_parser(
        "list",
        help="list retained archive identities",
    )
    artifact_list.add_argument("--workspace", type=Path, default=None)
    artifact_list.add_argument("--format", choices=("text", "json"), default="text")

    artifact_verify = artifact_commands.add_parser(
        "verify",
        help="check retained bytes without repairing or replacing them",
    )
    artifact_verify.add_argument("artifact_id")
    artifact_verify.add_argument("--workspace", type=Path, default=None)
    artifact_verify.add_argument("--format", choices=("text", "json"), default="text")

    checkpoint = commands.add_parser(
        "checkpoint",
        help="inspect and verify immutable state snapshots",
    )
    checkpoint_commands = checkpoint.add_subparsers(
        dest="checkpoint_command", required=True
    )

    checkpoint_list = checkpoint_commands.add_parser(
        "list",
        help="list stored checkpoints without changing them",
    )
    checkpoint_list.add_argument("--workspace", type=Path, default=None)
    checkpoint_list.add_argument("--game", required=True)
    checkpoint_list.add_argument("--format", choices=("text", "json"), default="text")

    checkpoint_show = checkpoint_commands.add_parser(
        "show",
        help="show an exact recorded checkpoint",
    )
    checkpoint_show.add_argument("checkpoint_id")
    checkpoint_show.add_argument("--workspace", type=Path, default=None)
    checkpoint_show.add_argument("--game", required=True)
    checkpoint_show.add_argument("--format", choices=("text", "json"), default="text")

    checkpoint_verify = checkpoint_commands.add_parser(
        "verify",
        help="detect missing or modified lockfiles without repairing them",
    )
    checkpoint_verify.add_argument("checkpoint_id")
    checkpoint_verify.add_argument("--workspace", type=Path, default=None)
    checkpoint_verify.add_argument("--game", required=True)
    checkpoint_verify.add_argument("--format", choices=("text", "json"), default="text")

    recipe = commands.add_parser("recipe", help="check or review a recipe")
    recipe_commands = recipe.add_subparsers(dest="recipe_command", required=True)

    check = recipe_commands.add_parser("check", help="validate a recipe file")
    check.add_argument("recipe", type=Path)

    review = recipe_commands.add_parser(
        "review",
        help="preview selections, dependencies, and compatibility",
    )
    review.add_argument("recipe", type=Path)
    review.add_argument("--environment", required=True, type=Path)
    review.add_argument("--select", action="append", default=[], metavar="ID")
    review.add_argument("--omit", action="append", default=[], metavar="ID")
    review.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def _print_list(output: TextIO, label: str, values: tuple[str, ...]) -> None:
    print(f"{label}:", file=output)
    if values:
        for value in values:
            print(f"  - {value}", file=output)
    else:
        print("  (none)", file=output)


def _print_review(output: TextIO, review: RecipeReview) -> None:
    print(f"Recipe: {review.recipe_id} @ {review.revision}", file=output)
    print(f"Maturity: {review.maturity.value}", file=output)
    print(f"Identity: {review.identity.value}", file=output)
    print(
        f"Ready for approval: {'yes' if review.ready_for_approval else 'no'}",
        file=output,
    )
    _print_list(output, "Selected", review.selected)
    _print_list(output, "Omitted", review.omitted)
    _print_list(output, "Dependency proposals", review.dependency_proposals)
    print("Incompatibilities:", file=output)
    if review.incompatibilities:
        for left, right in review.incompatibilities:
            print(f"  - {left} conflicts with {right}", file=output)
    else:
        print("  (none)", file=output)
    print("Compatibility findings:", file=output)
    if review.findings:
        for finding in review.findings:
            actual = finding.actual_value if finding.actual_value is not None else "missing"
            expected = ", ".join(finding.allowed_values)
            print(
                f"  - {finding.state.value}  {finding.component_id} "
                f"{finding.dimension}: actual={actual}; allowed={expected}",
                file=output,
            )
            print(f"    {finding.reason}", file=output)
    else:
        print("  (none)", file=output)
    print(NO_ACTIONS, file=output)


def _artifact_result(record: ArchiveArtifact, source_retained: bool) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "artifact": artifact_to_dict(record),
        "sourceRetained": source_retained,
        "actionsPerformed": ["archive-import"],
        "installationActionsPerformed": [],
    }


def _finding_to_dict(finding: ArtifactFinding) -> dict[str, object]:
    return {
        "health": finding.health.value,
        "artifactId": finding.artifact_id,
        "expectedSha256": finding.expected_sha256,
        "actualSha256": finding.actual_sha256,
        "path": str(finding.path),
        "message": finding.message,
    }


def _checkpoint_finding_to_dict(finding: CheckpointFinding) -> dict[str, object]:
    return {
        "health": finding.health.value,
        "checkpointId": finding.checkpoint_id,
        "actualCheckpointId": finding.actual_checkpoint_id,
        "path": str(finding.path),
        "message": finding.message,
    }


def _write_json(output: TextIO, value: object) -> None:
    json.dump(value, output, indent=2)
    print(file=output)


def main(
    argv: list[str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output = stdout if stdout is not None else sys.stdout
    errors = stderr if stderr is not None else sys.stderr
    args = _parser().parse_args(argv)
    try:
        if args.command == "workspace" and args.workspace_command == "init":
            root = args.root if args.root is not None else default_workspace_root()
            layout = initialize_workspace(root)
            print(f"Workspace: {layout.root}", file=output)
            print(f"Inbox: {layout.inbox}", file=output)
            print(f"Library: {layout.archives.parent}", file=output)
            print(f"Games: {layout.games}", file=output)
            print(f"Tools: {layout.tools}", file=output)
            print(f"Exports: {layout.exports}", file=output)
            print(f"Runtime: {layout.cache.parent}", file=output)
            print("Existing files were preserved.", file=output)
            return 0

        if args.command == "artifact":
            workspace_root = (
                args.workspace if args.workspace is not None else default_workspace_root()
            )
            vault = ArchiveVault(workspace_root)
            if args.artifact_command == "import":
                record = vault.import_archive(
                    args.source,
                    source_note=args.source_note,
                    source_url=args.source_url,
                )
                source_retained = args.source.expanduser().resolve().is_file()
                if args.format == "json":
                    _write_json(output, _artifact_result(record, source_retained))
                else:
                    print(f"Artifact: {record.artifact_id}", file=output)
                    print(f"SHA-256: {record.sha256}", file=output)
                    print(f"Stored: {record.stored_path(vault.workspace_root)}", file=output)
                    print(
                        f"Source retained: {args.source.expanduser().resolve()} "
                        f"({'yes' if source_retained else 'no'})",
                        file=output,
                    )
                    print(
                        "Archive retained only; nothing was extracted or installed.",
                        file=output,
                    )
                return 0

            if args.artifact_command == "list":
                records = vault.list()
                if args.format == "json":
                    _write_json(
                        output,
                        {
                            "schemaVersion": 1,
                            "artifacts": [artifact_to_dict(record) for record in records],
                            "actionsPerformed": [],
                            "installationActionsPerformed": [],
                        },
                    )
                elif records:
                    for record in records:
                        print(
                            f"{record.artifact_id}  {record.original_name}  {record.size} bytes",
                            file=output,
                        )
                    print("Nothing was extracted or installed.", file=output)
                else:
                    print("No retained archives.", file=output)
                return 0

            finding = vault.verify(args.artifact_id)
            if args.format == "json":
                _write_json(
                    output,
                    {
                        "schemaVersion": 1,
                        "finding": _finding_to_dict(finding),
                        "actionsPerformed": [],
                        "installationActionsPerformed": [],
                    },
                )
            else:
                print(f"{finding.health.value}: {finding.artifact_id}", file=output)
                print(f"Expected SHA-256: {finding.expected_sha256}", file=output)
                print(f"Actual SHA-256: {finding.actual_sha256 or '(missing)'}", file=output)
                print(f"Stored: {finding.path}", file=output)
                print(finding.message, file=output)
                print("Nothing was repaired, replaced, extracted, or installed.", file=output)
            return 0 if finding.health is ArtifactHealth.AVAILABLE else 3

        if args.command == "checkpoint":
            workspace_root = (
                args.workspace if args.workspace is not None else default_workspace_root()
            )
            store = CheckpointStore(workspace_root, args.game)
            if args.checkpoint_command == "list":
                records = store.list()
                if args.format == "json":
                    _write_json(
                        output,
                        {
                            "schemaVersion": 1,
                            "game": args.game,
                            "checkpoints": [
                                checkpoint_to_dict(record) for record in records
                            ],
                            "actionsPerformed": [],
                            "installationActionsPerformed": [],
                        },
                    )
                elif records:
                    for record in records:
                        print(
                            f"{record.checkpoint_id}  {record.created_at}  "
                            f"{record.recipe_id} @ {record.recipe_revision}",
                            file=output,
                        )
                    print("No checkpoint or game state was changed.", file=output)
                else:
                    print(f"No checkpoints for {args.game}.", file=output)
                return 0

            if args.checkpoint_command == "show":
                record = store.get(args.checkpoint_id)
                if args.format == "json":
                    _write_json(
                        output,
                        {
                            "schemaVersion": 1,
                            "checkpoint": checkpoint_to_dict(record),
                            "actionsPerformed": [],
                            "installationActionsPerformed": [],
                        },
                    )
                else:
                    print(f"Checkpoint: {record.checkpoint_id}", file=output)
                    print(f"Created: {record.created_at}", file=output)
                    print(f"Game / environment: {record.game} / {record.environment_id}", file=output)
                    print(f"Adapter: {record.adapter_id}", file=output)
                    print(
                        f"Recipe: {record.recipe_id} @ {record.recipe_revision} "
                        f"({record.recipe_maturity.value}, {record.recipe_identity.value})",
                        file=output,
                    )
                    print(f"Lineage: {record.lineage_id}", file=output)
                    print(f"Artifacts: {len(record.artifact_ids)}", file=output)
                    print(
                        "Recorded adapter state only; no live adapter was inspected and "
                        "nothing was promoted or changed.",
                        file=output,
                    )
                return 0

            finding = store.verify(args.checkpoint_id)
            if args.format == "json":
                _write_json(
                    output,
                    {
                        "schemaVersion": 1,
                        "finding": _checkpoint_finding_to_dict(finding),
                        "actionsPerformed": [],
                        "installationActionsPerformed": [],
                    },
                )
            else:
                print(f"{finding.health.value}: {finding.checkpoint_id}", file=output)
                print(f"Actual checkpoint ID: {finding.actual_checkpoint_id or '(none)'}", file=output)
                print(f"Lockfile: {finding.path}", file=output)
                print(finding.message, file=output)
                print("Nothing was repaired, restored, promoted, or installed.", file=output)
            return 0 if finding.health is CheckpointHealth.AVAILABLE else 3

        if args.command == "recipe" and args.recipe_command == "check":
            recipe = load_recipe(args.recipe)
            print(
                f"Recipe valid: {recipe.recipe_id} @ {recipe.revision}",
                file=output,
            )
            print(f"Name: {recipe.display_name}", file=output)
            print(f"Maturity: {recipe.maturity.value}", file=output)
            print(f"Components: {len(recipe.components)}", file=output)
            print(NO_ACTIONS, file=output)
            return 0

        recipe = load_recipe(args.recipe)
        environment = load_environment(args.environment)
        review = review_recipe(
            recipe,
            environment,
            select=tuple(args.select),
            omit=tuple(args.omit),
        )
        if args.format == "json":
            _write_json(output, review_to_dict(review))
        else:
            _print_review(output, review)
        return 0 if review.ready_for_approval else 3
    except (ArchiveImportError, ArtifactFormatError, ArtifactNotFoundError) as error:
        print(f"Artifact error: {error}", file=errors)
        return 2
    except (
        CheckpointFormatError,
        CheckpointNotFoundError,
        CheckpointStoreError,
    ) as error:
        print(f"Checkpoint error: {error}", file=errors)
        return 2
    except (RecipeFormatError, ValueError) as error:
        print(f"Recipe error: {error}", file=errors)
        return 2
