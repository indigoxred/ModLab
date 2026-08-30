"""Evidence-based compatibility checks with no repair side effects."""

from .model import (
    CheckState,
    CompatibilityFinding,
    EnvironmentEvidence,
    RecipeComponent,
)


def check_component(
    component: RecipeComponent,
    environment: EnvironmentEvidence,
) -> tuple[CompatibilityFinding, ...]:
    findings: list[CompatibilityFinding] = []
    for constraint in component.constraints:
        actual = environment.dimensions.get(constraint.dimension)
        if actual is None:
            state = CheckState.UNKNOWN
        elif actual in constraint.allowed_values:
            state = CheckState.PASSED
        else:
            state = CheckState.BLOCKED
        findings.append(
            CompatibilityFinding(
                state=state,
                component_id=component.component_id,
                dimension=constraint.dimension,
                allowed_values=constraint.allowed_values,
                actual_value=actual,
                reason=constraint.reason,
            )
        )
    return tuple(findings)
