import unittest
from types import MappingProxyType

from modlab.recipes.checking import check_component
from modlab.recipes.model import (
    CheckState,
    CompatibilityConstraint,
    ComponentImportance,
    EnvironmentEvidence,
    RecipeComponent,
)


def native_component() -> RecipeComponent:
    return RecipeComponent(
        component_id="native-fix",
        display_name="Native Fix",
        importance=ComponentImportance.REQUIRED,
        default_selected=True,
        role="Permanent Core",
        deployment="Data/VFS",
        requires=(),
        incompatible_with=(),
        constraints=(
            CompatibilityConstraint(
                dimension="executableRuntime",
                allowed_values=("1.7.104",),
                reason="This DLL targets 1.7.104.",
            ),
        ),
        rationale="Provides a native fix.",
        sources=(),
    )


class CompatibilityCheckingTests(unittest.TestCase):
    def test_exact_value_is_passed(self):
        environment = EnvironmentEvidence(
            1,
            "current",
            MappingProxyType({"executableRuntime": "1.7.104"}),
        )

        findings = check_component(native_component(), environment)

        self.assertEqual(CheckState.PASSED, findings[0].state)
        self.assertEqual("1.7.104", findings[0].actual_value)

    def test_proven_wrong_value_is_blocked(self):
        environment = EnvironmentEvidence(
            1,
            "fallback",
            MappingProxyType({"executableRuntime": "1.6.1170"}),
        )

        findings = check_component(native_component(), environment)

        self.assertEqual(CheckState.BLOCKED, findings[0].state)
        self.assertEqual(("1.7.104",), findings[0].allowed_values)

    def test_missing_dimension_is_unknown(self):
        environment = EnvironmentEvidence(1, "uninspected", MappingProxyType({}))

        findings = check_component(native_component(), environment)

        self.assertEqual(CheckState.UNKNOWN, findings[0].state)
        self.assertIsNone(findings[0].actual_value)

    def test_component_without_constraints_has_no_findings(self):
        component = native_component()
        unconstrained = RecipeComponent(
            component_id=component.component_id,
            display_name=component.display_name,
            importance=component.importance,
            default_selected=component.default_selected,
            role=component.role,
            deployment=component.deployment,
            requires=component.requires,
            incompatible_with=component.incompatible_with,
            constraints=(),
            rationale=component.rationale,
            sources=component.sources,
        )

        findings = check_component(
            unconstrained,
            EnvironmentEvidence(1, "any", MappingProxyType({})),
        )

        self.assertEqual((), findings)


if __name__ == "__main__":
    unittest.main()
