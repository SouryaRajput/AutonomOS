from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from typing import Any, Optional, Sequence, Union

from core.tester.contracts.identifiers import (
    OBSERVATION_SET_ID_PREFIX,
    new_observation_set_id,
    validate_evidence_id,
    validate_execution_id,
    validate_observation_id,
    validate_observation_set_id,
    validate_step_id,
    validate_test_case_id,
)
from core.tester.contracts.observation import TesterObservation
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    ObservationCompleteness,
    ObservationType,
)

logger = logging.getLogger("AutonomOS.Tester.ObservationSet")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# Patterns indicating evaluative judgment rather than factual observation
FORBIDDEN_EVALUATIVE_KEYS = {
    "is_defect",
    "defect",
    "is_failure",
    "verdict",
    "pass_fail",
    "passed",
    "failed",
    "defect_severity",
    "ux_score",
    "visual_score",
    "quality_score",
}


def _is_failed_observation(obs: TesterObservation) -> bool:
    """
    Check whether an observation represents a failed, unavailable, or errored observation attempt.
    """
    state = obs.observed_state or {}
    prov = obs.provenance or {}
    status = str(state.get("status") or prov.get("status") or "").upper()
    if status in {"FAILED", "UNAVAILABLE", "TIMEOUT", "UNSUPPORTED"}:
        return True
    if state.get("error_message") or obs.metadata.get("error_message"):
        return True
    if obs.confidence == 0.0:
        return True
    return False


# ---------------------------------------------------------------------------
# Aggregated Observation Set Model
# ---------------------------------------------------------------------------

@dataclass
class ObservationSet:
    """
    Structured, factual container aggregating observations produced during a TestCase execution.
    
    Organizes observations across:
    - execution
    - test_case
    - test_step
    - observation_type
    - evidence_source
    
    Invariants:
    - Purely observational: records WHAT WAS OBSERVED and organizes evidence coherently.
    - Zero evaluation: calling to_defect(), to_finding(), or assert_verdict() raises TesterBoundaryViolationError.
    - Evaluative metadata rejection: forbidden evaluative keys raise TesterBoundaryViolationError.
    - Tenant and execution isolation: all observations must belong to the set's execution_id and project_id.
    - Completeness states: COMPLETE, PARTIAL, EMPTY, FAILED.
      NOTE: PARTIAL does NOT mean TestCase failed. It simply reflects observation availability.
    """
    __test__ = False
    observation_set_id: str
    execution_id: str
    project_id: str
    test_case_id: Optional[str] = None
    observations: list[TesterObservation] = field(default_factory=list)
    evidence_references: list[str] = field(default_factory=list)
    completeness: ObservationCompleteness = ObservationCompleteness.COMPLETE
    provenance: dict[str, Any] = field(default_factory=dict)
    trace_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        # 1. Identifier Validations
        validate_observation_set_id(self.observation_set_id)
        validate_execution_id(self.execution_id)
        if not self.project_id or not str(self.project_id).strip():
            raise TesterLineageError("ObservationSet must have a valid non-empty project_id.")
        if self.test_case_id is not None:
            validate_test_case_id(self.test_case_id)

        # 2. Completeness Normalization
        if isinstance(self.completeness, str):
            try:
                self.completeness = ObservationCompleteness(self.completeness.upper())
            except (ValueError, KeyError):
                self.completeness = ObservationCompleteness.COMPLETE

        # 3. Guard against Evaluative Keys
        for forbidden in FORBIDDEN_EVALUATIVE_KEYS:
            for container_name, container in (
                ("metadata", self.metadata),
                ("provenance", self.provenance),
            ):
                if forbidden in container:
                    raise TesterBoundaryViolationError(
                        action="OBSERVATION_SET_EVALUATION",
                        reason=(
                            f"ObservationSet contains forbidden evaluative key '{forbidden}' in {container_name}. "
                            "ObservationSet is an organizational structure only and cannot contain quality scoring, verdicts, or defects."
                        ),
                    )

        # 4. Normalize & Validate Observations and Lineage
        normalized_obs: list[TesterObservation] = []
        for o in self.observations:
            if isinstance(o, dict):
                o = TesterObservation.from_dict(o)
            elif not isinstance(o, TesterObservation):
                raise TesterValidationError(
                    f"ObservationSet requires TesterObservation instances, got {type(o).__name__}."
                )

            # Lineage & Isolation checks
            if o.execution_id != self.execution_id:
                raise TesterLineageError(
                    f"Cross-execution observation rejected: observation '{o.observation_id}' execution_id "
                    f"('{o.execution_id}') does not match ObservationSet execution_id ('{self.execution_id}')."
                )
            if o.project_id != self.project_id:
                raise TesterBoundaryViolationError(
                    action="OBSERVATION_SET_PROJECT_ISOLATION",
                    reason=(
                        f"Cross-project observation rejected: observation '{o.observation_id}' project_id "
                        f"('{o.project_id}') does not match ObservationSet project_id ('{self.project_id}')."
                    ),
                )
            if self.test_case_id is not None and o.test_case_id is not None and o.test_case_id != self.test_case_id:
                raise TesterLineageError(
                    f"Cross-test-case observation rejected: observation '{o.observation_id}' test_case_id "
                    f"('{o.test_case_id}') does not match ObservationSet test_case_id ('{self.test_case_id}')."
                )
            normalized_obs.append(o)
        self.observations = normalized_obs

        # 5. Populate / reconcile evidence references
        ev_refs: list[str] = list(self.evidence_references)
        for o in self.observations:
            for eid in o.evidence_ids:
                if eid and eid not in ev_refs:
                    ev_refs.append(eid)
            # Also collect frame evidence reference if present in observed state
            f_ref = o.observed_state.get("frame_evidence_reference")
            if f_ref and f_ref not in ev_refs:
                ev_refs.append(f_ref)
        self.evidence_references = ev_refs

    # -----------------------------------------------------------------------
    # Boundary Protections
    # -----------------------------------------------------------------------
    def to_defect(self, *args: Any, **kwargs: Any) -> Any:
        raise TesterBoundaryViolationError(
            action="OBSERVATION_SET_TO_DEFECT",
            reason=(
                "ObservationSet cannot produce a TesterDefect. Observation aggregation is organizational and descriptive only. "
                "Defect identification occurs during evaluation phases against acceptance criteria."
            ),
        )

    def to_finding(self, *args: Any, **kwargs: Any) -> Any:
        raise TesterBoundaryViolationError(
            action="OBSERVATION_SET_TO_FINDING",
            reason="ObservationSet cannot produce a TesterFinding. Aggregation is descriptive only.",
        )

    def to_recommendation(self, *args: Any, **kwargs: Any) -> Any:
        raise TesterBoundaryViolationError(
            action="OBSERVATION_SET_TO_RECOMMENDATION",
            reason="ObservationSet cannot produce recommendations.",
        )

    def assert_verdict(self, *args: Any, **kwargs: Any) -> Any:
        raise TesterBoundaryViolationError(
            action="OBSERVATION_SET_ASSERT_VERDICT",
            reason=(
                "ObservationSet cannot assert test verdicts. "
                "Completeness status (PARTIAL, FAILED, EMPTY) reflects observation collection, not product failure."
            ),
        )

    # -----------------------------------------------------------------------
    # Query & Grouping Capabilities
    # -----------------------------------------------------------------------
    def get_observations_by_step(self, step_id: Optional[str]) -> list[TesterObservation]:
        """Return all observations bound to the specified test_step_id."""
        return [o for o in self.observations if o.test_step_id == step_id]

    def get_observations_by_type(
        self,
        observation_type: Union[ObservationType, str],
    ) -> list[TesterObservation]:
        """Return all observations of a specific ObservationType."""
        target = observation_type.value if hasattr(observation_type, "value") else str(observation_type).upper()
        return [
            o for o in self.observations
            if (o.observation_type.value if hasattr(o.observation_type, "value") else str(o.observation_type).upper()) == target
        ]

    def get_observations_by_evidence(self, evidence_id: str) -> list[TesterObservation]:
        """Return all observations originating from or referencing the specified evidence."""
        return [o for o in self.observations if evidence_id in o.evidence_ids]

    def group_by_step(self) -> dict[Optional[str], list[TesterObservation]]:
        """
        Group observations by test_step_id, preserving chronological order within each step.
        """
        grouped: dict[Optional[str], list[TesterObservation]] = {}
        for o in self.observations:
            step = o.test_step_id
            if step not in grouped:
                grouped[step] = []
            grouped[step].append(o)
        return grouped

    def group_by_type(self) -> dict[str, list[TesterObservation]]:
        """
        Group observations by observation_type string value.
        """
        grouped: dict[str, list[TesterObservation]] = {}
        for o in self.observations:
            t = o.observation_type.value if hasattr(o.observation_type, "value") else str(o.observation_type).upper()
            if t not in grouped:
                grouped[t] = []
            grouped[t].append(o)
        return grouped

    def group_by_evidence(self) -> dict[str, list[TesterObservation]]:
        """
        Group observations by evidence source identifier.
        """
        grouped: dict[str, list[TesterObservation]] = {}
        for o in self.observations:
            for eid in o.evidence_ids:
                if eid not in grouped:
                    grouped[eid] = []
                grouped[eid].append(o)
        return grouped

    # -----------------------------------------------------------------------
    # Serialization
    # -----------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_set_id": self.observation_set_id,
            "execution_id": self.execution_id,
            "project_id": self.project_id,
            "test_case_id": self.test_case_id,
            "observations": [o.to_dict() for o in self.observations],
            "evidence_references": list(self.evidence_references),
            "completeness": self.completeness.value,
            "provenance": dict(self.provenance),
            "trace_id": self.trace_id,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ObservationSet:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for ObservationSet, got {type(data).__name__}.")

        obs_list = [
            TesterObservation.from_dict(item) if isinstance(item, dict) else item
            for item in data.get("observations", [])
        ]
        comp = data.get("completeness", ObservationCompleteness.COMPLETE)
        if isinstance(comp, str):
            try:
                comp = ObservationCompleteness(comp.upper())
            except ValueError:
                comp = ObservationCompleteness.COMPLETE

        return cls(
            observation_set_id=data["observation_set_id"],
            execution_id=data["execution_id"],
            project_id=data["project_id"],
            test_case_id=data.get("test_case_id"),
            observations=obs_list,
            evidence_references=list(data.get("evidence_references", [])),
            completeness=comp,
            provenance=dict(data.get("provenance", {})),
            trace_id=data.get("trace_id"),
            metadata=dict(data.get("metadata", {})),
            created_at=str(data.get("created_at", utc_now())),
        )


# ---------------------------------------------------------------------------
# Bounded Observation Aggregator Service
# ---------------------------------------------------------------------------

class ObservationAggregator:
    """
    Deterministic aggregator service that compiles and organizes observations produced
    during TestCase execution into an authoritative ObservationSet.
    
    Principles:
    - Avoids accidental duplicate observations (identity- and evidence-based deduplication).
    - Preserves chronological ordering where timestamps exist, keeping step associations intact.
    - Computes observational completeness (COMPLETE, PARTIAL, EMPTY, FAILED) without conflating
      PARTIAL observation with test or product failure.
    - Enforces execution and project boundaries.
    """

    def __init__(self, default_deduplicate: bool = True) -> None:
        self.default_deduplicate = default_deduplicate

    def compute_completeness(
        self,
        observations: Sequence[TesterObservation],
        expected_step_ids: Optional[Sequence[str]] = None,
    ) -> ObservationCompleteness:
        """
        Deterministically compute the completeness of an observation set.
        
        Rules:
        - 0 observations -> EMPTY
        - All observations failed / errored / unavailable -> FAILED
        - Some observations failed / uncertain, or expected steps missing -> PARTIAL
        - All observations successful and expected steps satisfied -> COMPLETE
        
        NOTE: PARTIAL does NOT mean TestCase failed.
        """
        if not observations:
            return ObservationCompleteness.EMPTY

        total = len(observations)
        failed_count = sum(1 for o in observations if _is_failed_observation(o))
        uncertain_count = sum(1 for o in observations if o.is_uncertain or o.confidence < 1.0)

        # Check step coverage if expected steps provided
        missing_steps = False
        if expected_step_ids:
            observed_steps = {o.test_step_id for o in observations if o.test_step_id}
            for exp_step in expected_step_ids:
                if exp_step not in observed_steps:
                    missing_steps = True
                    break

        if failed_count == total:
            return ObservationCompleteness.FAILED

        if failed_count > 0 or uncertain_count > 0 or missing_steps:
            return ObservationCompleteness.PARTIAL

        return ObservationCompleteness.COMPLETE

    def aggregate(
        self,
        execution_id: str,
        project_id: str,
        test_case_id: Optional[str] = None,
        observations: Optional[Sequence[Union[TesterObservation, dict[str, Any]]]] = None,
        observation_set_id: Optional[str] = None,
        expected_step_ids: Optional[Sequence[str]] = None,
        trace_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        provenance: Optional[dict[str, Any]] = None,
        deduplicate: Optional[bool] = None,
        sort_chronological: bool = True,
        override_completeness: Optional[ObservationCompleteness] = None,
    ) -> ObservationSet:
        """
        Aggregate observations into a validated, deduplicated, and chronologically ordered ObservationSet.
        """
        validate_execution_id(execution_id)
        if not project_id or not str(project_id).strip():
            raise TesterLineageError("ObservationAggregator requires a valid non-empty project_id.")
        if test_case_id is not None:
            validate_test_case_id(test_case_id)

        set_id = observation_set_id or new_observation_set_id()
        raw_obs = observations or []
        do_dedup = self.default_deduplicate if deduplicate is None else deduplicate

        # 1. Normalize and check initial types
        normalized_list: list[TesterObservation] = []
        for item in raw_obs:
            if isinstance(item, dict):
                normalized_list.append(TesterObservation.from_dict(item))
            elif isinstance(item, TesterObservation):
                normalized_list.append(item)
            else:
                raise TesterValidationError(
                    f"Expected TesterObservation or dict, got {type(item).__name__}."
                )

        # 2. Deduplication (Identity and Evidence-based)
        final_obs: list[TesterObservation] = []
        seen_ids: set[str] = set()
        seen_evidence_signatures: set[str] = set()

        for obs in normalized_list:
            if do_dedup:
                # Check 1: exact same observation_id
                if obs.observation_id in seen_ids:
                    logger.debug(f"Skipping duplicate observation_id: {obs.observation_id}")
                    continue

                # Check 2: exact same evidence processed repeatedly for the same step and type
                # Compute signature over step, type, source, evidence, and deterministic state
                state_str = json.dumps(obs.observed_state, sort_keys=True, default=str)
                ev_tuple = tuple(sorted(obs.evidence_ids))
                type_str = obs.observation_type.value if hasattr(obs.observation_type, "value") else str(obs.observation_type)
                sig = f"{obs.test_step_id}|{type_str}|{obs.source}|{ev_tuple}|{obs.description}|{state_str}"

                if ev_tuple and sig in seen_evidence_signatures:
                    logger.debug(f"Skipping duplicate observation evidence processing: {sig}")
                    continue

                seen_ids.add(obs.observation_id)
                if ev_tuple:
                    seen_evidence_signatures.add(sig)

            final_obs.append(obs)

        # 3. Chronological Ordering
        if sort_chronological and len(final_obs) > 1:
            # Sort chronologically by ISO 8601 timestamp; python's stable sort preserves step order for ties
            final_obs.sort(key=lambda o: str(o.timestamp or ""))

        # 4. Determine Completeness
        if override_completeness is not None:
            comp = override_completeness
        else:
            comp = self.compute_completeness(final_obs, expected_step_ids=expected_step_ids)

        # 5. Build Provenance
        prov = {
            "source": "observation_aggregator",
            "deduplicated": do_dedup,
            "sorted_chronological": sort_chronological,
            "total_raw": len(raw_obs),
            "total_aggregated": len(final_obs),
        }
        if provenance:
            prov.update(provenance)

        # 6. Instantiate and return ObservationSet
        return ObservationSet(
            observation_set_id=set_id,
            execution_id=execution_id,
            project_id=project_id,
            test_case_id=test_case_id,
            observations=final_obs,
            completeness=comp,
            provenance=prov,
            trace_id=trace_id,
            metadata=dict(metadata or {}),
        )
