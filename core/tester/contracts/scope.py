from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from core.tester.errors import TesterValidationError

FORBIDDEN_SCOPE_WILDCARDS = frozenset({"*", ".*", "all", "any", "%", "/...", "..."})


def is_wildcard_token(entry: str) -> bool:
    norm = str(entry).strip().lower()
    if norm in FORBIDDEN_SCOPE_WILDCARDS:
        return True
    if "*" in norm or "..." in norm:
        return True
    return False


@dataclass
class TestScope:
    """
    Explicit, bounded testing scope authorized by Manager.
    Restricts Tester inspection to designated routes, features, user flows,
    components/areas, environments, and viewport targets.
    
    Invariants:
    - Must NOT be empty (prevents unbounded or unintentional exploration).
    - Rejects wildcard tokens ('*', 'all') to prevent boundary bypass.
    - Ambiguous or malformed scopes must be rejected.
    """
    __test__ = False
    routes: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    flows: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    environments: list[str] = field(default_factory=list)
    viewports: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        """Return True if no targets are specified in any category or all are blank."""
        targets = self.routes + self.features + self.flows + self.components
        return not targets or not any(str(t).strip() for t in targets)

    def all_targets(self) -> list[str]:
        """Return a consolidated list of all target identifiers across all categories."""
        return self.routes + self.features + self.flows + self.components

    def to_list(self) -> list[str]:
        """Return a consolidated flat list of all authorized targets."""
        return self.all_targets()

    def __iter__(self):
        """Iterate over all authorized target identifiers."""
        return iter(self.all_targets())

    def contains_target(self, target: str) -> bool:
        """
        Check whether a given route, component, feature, or flow is covered by this scope.
        Uses deterministic, simple string matching (case-insensitive substring/prefix).
        No complex query languages or semantic expansion.
        """
        if not target or not target.strip():
            return False
        target_norm = target.strip().lower()
        for item in self.all_targets():
            item_norm = str(item).strip().lower()
            if not item_norm:
                continue
            if target_norm == item_norm or target_norm.startswith(item_norm) or item_norm.startswith(target_norm):
                return True
        return False

    def validate(self) -> None:
        """
        Validate internal consistency and explicitness of scope.
        Raises TesterValidationError if empty, malformed, or wildcard-bypassing.
        """
        if self.is_empty():
            raise TesterValidationError(
                "TestScope cannot be empty. At least one route, feature, flow, or component must be explicitly authorized.",
                field_name="test_scope",
            )

        # Check for wildcard tokens in any field
        all_entries = self.all_targets() + self.environments + self.viewports
        for entry in all_entries:
            if not str(entry).strip():
                raise TesterValidationError(
                    "TestScope entries cannot be blank or whitespace-only.",
                    field_name="test_scope",
                )
            if is_wildcard_token(str(entry)):
                raise TesterValidationError(
                    f"Forbidden wildcard '{entry}' in TestScope. Scope must be explicit to prevent accidental out-of-bounds testing.",
                    field_name="test_scope",
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "routes": list(self.routes),
            "features": list(self.features),
            "flows": list(self.flows),
            "components": list(self.components),
            "environments": list(self.environments),
            "viewports": list(self.viewports),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestScope:
        return cls(
            routes=list(data.get("routes", [])),
            features=list(data.get("features", [])),
            flows=list(data.get("flows", [])),
            components=list(data.get("components", [])),
            environments=list(data.get("environments", [])),
            viewports=list(data.get("viewports", [])),
        )

    @classmethod
    def from_items(
        cls,
        items: Sequence[str],
        category: str = "components",
    ) -> TestScope:
        """Convenience constructor from a flat list of items."""
        valid_items = [str(i).strip() for i in items if str(i).strip()]
        kwargs: dict[str, list[str]] = {category: valid_items}
        return cls(**kwargs)
