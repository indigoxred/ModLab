"""Command-line boundary for ModLab's dependency-free core."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

from .recipes.loading import RecipeFormatError, load_environment, load_recipe
from .recipes.model import RecipeReview
from .recipes.reviewing import review_recipe
from .recipes.serialization import review_to_dict
from .workspace import default_workspace_root, initialize_workspace


NO_ACTIONS = "No downloads or installation actions were performed."


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="modlab",
        description="Inspect ModLab recipes without changing a mod setup.",
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
            json.dump(review_to_dict(review), output, indent=2)
            print(file=output)
        else:
            _print_review(output, review)
        return 0 if review.ready_for_approval else 3
    except (RecipeFormatError, ValueError) as error:
        print(f"Recipe error: {error}", file=errors)
        return 2
