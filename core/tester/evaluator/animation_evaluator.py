from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import re
from typing import Any, Callable, Optional, Sequence, Union

from core.tester.contracts.animation import (
    AnimationAssertion,
    AnimationAssertionResult,
    AnimationEvaluationResult,
    AnimationStateExpectation,
)
from core.tester.contracts.finding import (
    TesterDefect,
    TesterEvidence,
    TesterFinding,
)
from core.tester.contracts.identifiers import (
    new_animation_evaluation_id,
    new_defect_id,
    new_evidence_id,
    new_finding_id,
    validate_execution_id,
)
from core.tester.contracts.video_frame import (
    FrameExtractionStatus,
    FramePosition,
    VideoFrameObservation,
)
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    AnimationCheckType,
    AnimationEvaluationStatus,
    DefectSeverity,
    DefectType,
    FindingCategory,
    TestSurface,
)

logger = logging.getLogger("AutonomOS.Tester.AnimationEvaluator")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# Maximum number of video frames sampled and analyzed to prevent endless loops
MAX_EVALUATION_FRAMES = 10

# Subjective motion quality patterns that must NEVER be classified as defects
SUBJECTIVE_MOTION_PATTERNS = [
    r"animation\s+feels?\s+(?:slow|fast|sluggish|choppy|weird|bad)",
    r"animation\s+(?:isn't|is\s+not)\s+premium",
    r"animation\s+(?:isn't|is\s+not)\s+smooth\s+enough",
    r"motion\s+feels?\s+(?:clunky|awkward|unnatural)",
    r"(?:transition|motion)\s+is\s+(?:boring|ugly|cheap)",
]


class AnimationEvaluator:
    """
    Phase 6.5: Deterministic Animation & Transition Evaluation Engine.

    Evaluates animation and transition behavior using Phase 4 VideoFrameObservation.

    Guarantees:
    1. Bounded Frame Analysis: Samples at most MAX_EVALUATION_FRAMES (10) frames,
       never analyzing every frame or entering infinite loops.
    2. Zero Subjective Motion Scoring: Opinions like 'animation feels slow' or 'isn't premium'
       are strictly rejected from defect classification.
    3. Concrete Measurable Checks: Evaluates transition completion, stuck animations,
       missing transitions, incomplete final states, and visual collapse.
    4. Intentional No-Animation Awareness: Instantaneous state changes without gradual
       transitions are recognized as intentional and not flagged as defects.
    5. Zero Performance Metrics: Frame rate, FPS, and dropped frames are not evaluated (deferred to Phase 7).
    6. Zero Automatic Fixing: Does not modify product source or attempt automated fixes.
    7. Strict Lineage & Provenance: Enforces project_id and execution_id isolation.
    """
    __test__ = False

    def __init__(
        self,
        execution_id: Optional[str] = None,
        project_id: Optional[str] = None,
        work_order_id: Optional[str] = None,
        max_evaluation_frames: int = MAX_EVALUATION_FRAMES,
    ) -> None:
        self.execution_id = execution_id
        self.project_id = project_id
        self.work_order_id = work_order_id
        self.max_evaluation_frames = max(2, min(int(max_evaluation_frames), MAX_EVALUATION_FRAMES))

    # ---------------------------------------------------------------------------
    # Transition Evaluation
    # ---------------------------------------------------------------------------

    def evaluate_transition(
        self,
        expectation: AnimationStateExpectation,
        frame_observations: Sequence[VideoFrameObservation],
        source_evidence_ids: Optional[Sequence[str]] = None,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
    ) -> AnimationAssertionResult:
        """
        Evaluate whether an animation or transition cleanly progresses and reaches its expected final state.

        Analyzes sampled sequence of VideoFrameObservations:
        1. Initial state (start of transition)
        2. Intermediate frames (progress toward final state)
        3. Final state (completion of transition)
        """
        assertion_id = new_animation_evaluation_id()
        elem_id = expectation.target_element_id or "animated_element"

        # 1. Lineage & Runtime Failure Validation
        for frame in frame_observations:
            self._validate_frame_lineage(frame)

        ev_ids = self._collect_evidence_ids(frame_observations, source_evidence_ids)

        # Check for empty frame observations -> NOT_VERIFIED
        if not frame_observations:
            return AnimationAssertionResult(
                assertion_id=assertion_id,
                check_type=AnimationCheckType.TRANSITION_COMPLETION,
                status=AnimationEvaluationStatus.NOT_VERIFIED,
                target_element_id=elem_id,
                description=f"No video frame observations provided; cannot evaluate transition for '{elem_id}'.",
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        # Check for frame extraction failures/timeouts -> BLOCKED
        for frame in frame_observations:
            if frame.status in (FrameExtractionStatus.FAILED, FrameExtractionStatus.TIMEOUT):
                return AnimationAssertionResult(
                    assertion_id=assertion_id,
                    check_type=AnimationCheckType.TRANSITION_COMPLETION,
                    status=AnimationEvaluationStatus.BLOCKED,
                    target_element_id=elem_id,
                    description=(
                        f"Video frame observation '{frame.frame_observation_id}' encountered runtime error: "
                        f"{frame.status.value} ({frame.error_message or 'extraction failure'})."
                    ),
                    evidence_ids=ev_ids,
                    test_case_id=test_case_id,
                    test_step_id=test_step_id,
                )

        # 2. Bounded Sampling Guard (Never analyze all frames in unbounded video)
        sampled_frames = self._sample_bounded_frames(frame_observations)

        # Check for insufficient frame count when transition progression is required
        if len(sampled_frames) < 2 and expectation.require_intermediate_progress:
            first_state = self._extract_state_from_frame(sampled_frames[0], elem_id)
            if first_state != expectation.expected_final_state:
                return AnimationAssertionResult(
                    assertion_id=assertion_id,
                    check_type=AnimationCheckType.TRANSITION_COMPLETION,
                    status=AnimationEvaluationStatus.NOT_VERIFIED,
                    target_element_id=elem_id,
                    initial_state_observed=first_state,
                    frames_evaluated_count=len(sampled_frames),
                    description="Insufficient frame sequence (only 1 frame available) to verify transition progress.",
                    evidence_ids=ev_ids,
                    test_case_id=test_case_id,
                    test_step_id=test_step_id,
                )

        # 3. Extract Observed States Across Sampled Frames
        observed_states: list[str] = [
            self._extract_state_from_frame(f, elem_id) for f in sampled_frames
        ]

        initial_state_observed = observed_states[0]
        final_state_observed = observed_states[-1]
        expected_final = expectation.expected_final_state.lower().strip()

        # Check A: Intentional No-Animation Behavior
        # If element changes directly from initial to expected final state in 1 step without intermediate
        if (not expectation.require_intermediate_progress or len(sampled_frames) <= 2) and final_state_observed.lower() == expected_final:
            return AnimationAssertionResult(
                assertion_id=assertion_id,
                check_type=AnimationCheckType.TRANSITION_COMPLETION,
                status=AnimationEvaluationStatus.PASS,
                target_element_id=elem_id,
                initial_state_observed=initial_state_observed,
                final_state_observed=final_state_observed,
                transition_completed=True,
                frames_evaluated_count=len(sampled_frames),
                description=f"State transitioned directly to '{final_state_observed}' (intentional instantaneous transition).",
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        # Check B: Missing Transition (All frames remain in initial state, no change occurred)
        all_identical_to_initial = all(
            s.lower() == initial_state_observed.lower() for s in observed_states
        )
        if all_identical_to_initial and initial_state_observed.lower() != expected_final:
            defect = self._create_animation_defect(
                title=f"Missing Transition: '{elem_id}' never transitioned from '{initial_state_observed}'",
                description=(
                    f"Action triggered transition for '{elem_id}', but element remained in initial state "
                    f"'{initial_state_observed}' across all {len(sampled_frames)} observed frames without any motion."
                ),
                severity=DefectSeverity.HIGH,
                check_type=AnimationCheckType.TRANSITION_COMPLETION,
                expected_behavior=f"Element '{elem_id}' should transition from '{initial_state_observed}' to '{expectation.expected_final_state}'.",
                observed_behavior=f"Element remained static at '{initial_state_observed}'.",
                reproduction_steps=[
                    f"Trigger transition on '{elem_id}'.",
                    f"Inspect video frame sequence across {len(sampled_frames)} moments.",
                    f"Observe state never changes from '{initial_state_observed}'.",
                ],
                affected_components=[elem_id],
                test_case_id=test_case_id,
                evidence_ids=ev_ids,
            )

            return AnimationAssertionResult(
                assertion_id=assertion_id,
                check_type=AnimationCheckType.TRANSITION_COMPLETION,
                status=AnimationEvaluationStatus.FAIL,
                target_element_id=elem_id,
                initial_state_observed=initial_state_observed,
                final_state_observed=final_state_observed,
                transition_completed=False,
                frames_evaluated_count=len(sampled_frames),
                description=f"Missing transition: '{elem_id}' remained stuck at '{initial_state_observed}'.",
                defect=defect,
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        # Check C: Stuck Animation (Transition started, but stalled at an intermediate state)
        # Check if multiple trailing frames are identical intermediate states that do not match expected final
        if len(observed_states) >= 3:
            intermediate_state = observed_states[1]
            # If intermediate is different from initial, but subsequent frames remain identical to intermediate
            if (
                intermediate_state.lower() != initial_state_observed.lower()
                and intermediate_state.lower() != expected_final
                and final_state_observed.lower() == intermediate_state.lower()
            ):
                defect = self._create_animation_defect(
                    title=f"Stuck Animation: '{elem_id}' stuck in intermediate state '{intermediate_state}'",
                    description=(
                        f"Animation for '{elem_id}' started from '{initial_state_observed}' into '{intermediate_state}', "
                        f"but stalled indefinitely and never completed to '{expectation.expected_final_state}'."
                    ),
                    severity=DefectSeverity.HIGH,
                    check_type=AnimationCheckType.STUCK_ANIMATION,
                    expected_behavior=f"Animation for '{elem_id}' should complete to '{expectation.expected_final_state}'.",
                    observed_behavior=f"Animation stalled at intermediate state '{intermediate_state}'.",
                    reproduction_steps=[
                        f"Trigger transition for '{elem_id}'.",
                        f"Observe progress starting from '{initial_state_observed}'.",
                        f"Observe animation stalls permanently at intermediate state '{intermediate_state}'.",
                    ],
                    affected_components=[elem_id],
                    test_case_id=test_case_id,
                    evidence_ids=ev_ids,
                )

                return AnimationAssertionResult(
                    assertion_id=assertion_id,
                    check_type=AnimationCheckType.STUCK_ANIMATION,
                    status=AnimationEvaluationStatus.FAIL,
                    target_element_id=elem_id,
                    initial_state_observed=initial_state_observed,
                    final_state_observed=final_state_observed,
                    transition_completed=False,
                    frames_evaluated_count=len(sampled_frames),
                    stuck_detected=True,
                    description=f"Stuck animation: '{elem_id}' stalled in state '{intermediate_state}'.",
                    defect=defect,
                    evidence_ids=ev_ids,
                    test_case_id=test_case_id,
                    test_step_id=test_step_id,
                )

        # Check D: Incomplete Final State (Motion ended, but final state does not equal expected final state)
        if final_state_observed.lower() != expected_final:
            defect = self._create_animation_defect(
                title=f"Incomplete Final State: '{elem_id}' reached '{final_state_observed}' instead of expected '{expectation.expected_final_state}'",
                description=(
                    f"Transition completed, but final state '{final_state_observed}' does not satisfy "
                    f"required final state '{expectation.expected_final_state}'."
                ),
                severity=DefectSeverity.HIGH,
                check_type=AnimationCheckType.INCOMPLETE_FINAL_STATE,
                expected_behavior=f"Element '{elem_id}' should reach final state '{expectation.expected_final_state}'.",
                observed_behavior=f"Element reached '{final_state_observed}'.",
                reproduction_steps=[
                    f"Trigger transition on '{elem_id}'.",
                    f"Allow transition to finish.",
                    f"Observe resulting final state '{final_state_observed}' differs from expected '{expectation.expected_final_state}'.",
                ],
                affected_components=[elem_id],
                test_case_id=test_case_id,
                evidence_ids=ev_ids,
            )

            return AnimationAssertionResult(
                assertion_id=assertion_id,
                check_type=AnimationCheckType.INCOMPLETE_FINAL_STATE,
                status=AnimationEvaluationStatus.FAIL,
                target_element_id=elem_id,
                initial_state_observed=initial_state_observed,
                final_state_observed=final_state_observed,
                transition_completed=False,
                frames_evaluated_count=len(sampled_frames),
                description=f"Incomplete final state: '{elem_id}' reached '{final_state_observed}'.",
                defect=defect,
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        # Check E: Successful Transition
        return AnimationAssertionResult(
            assertion_id=assertion_id,
            check_type=AnimationCheckType.TRANSITION_COMPLETION,
            status=AnimationEvaluationStatus.PASS,
            target_element_id=elem_id,
            initial_state_observed=initial_state_observed,
            final_state_observed=final_state_observed,
            transition_completed=True,
            frames_evaluated_count=len(sampled_frames),
            description=f"Transition for '{elem_id}' successfully completed from '{initial_state_observed}' to '{final_state_observed}'.",
            evidence_ids=ev_ids,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
        )

    # ---------------------------------------------------------------------------
    # Subjective Motion Quality Filter
    # ---------------------------------------------------------------------------

    def filter_subjective_motion_critique(
        self,
        critique_text: str,
        test_case_id: Optional[str] = None,
    ) -> tuple[bool, Optional[TesterFinding]]:
        """
        Check if an input critique represents subjective motion perception.

        Returns (is_subjective, optional_recommendation).
        Guarantees: Subjective motion opinions ('feels slow', 'isn't premium')
        are strictly rejected from defect classification.
        """
        c_lower = critique_text.lower()
        is_subjective = any(re.search(pat, c_lower) for pat in SUBJECTIVE_MOTION_PATTERNS)

        if not is_subjective:
            return (False, None)

        finding = TesterFinding(
            finding_id=new_finding_id(),
            category=FindingCategory.RECOMMENDATION,
            title=f"Motion Suggestion: {critique_text[:60]}",
            description=f"Subjective motion opinion noted: '{critique_text}'. Not classified as an animation defect.",
            affected_area="animation",
            confidence=0.5,
            metadata={
                "execution_id": self.execution_id or "texec-default",
                "evaluator": "AnimationEvaluator",
                "phase": "Phase 6.5",
                "is_subjective_motion": True,
            },
        )
        return (True, finding)

    # ---------------------------------------------------------------------------
    # Aggregate Evaluation Runner
    # ---------------------------------------------------------------------------

    def evaluate_animation(
        self,
        assertions: Sequence[AnimationAssertion],
        frame_observations: Sequence[VideoFrameObservation],
        source_evidence_ids: Optional[Sequence[str]] = None,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
    ) -> AnimationEvaluationResult:
        """
        Evaluate a sequence of animation assertions against video frame observations.
        """
        eval_id = new_animation_evaluation_id()
        results: list[AnimationAssertionResult] = []
        defects: list[TesterDefect] = []
        findings: list[TesterFinding] = []
        all_evidence = set(source_evidence_ids or [])

        for f in frame_observations:
            if f.source_video_evidence_id:
                all_evidence.add(f.source_video_evidence_id)
            if f.frame_evidence_id:
                all_evidence.add(f.frame_evidence_id)

        for assertion in assertions:
            # Subjective critique check
            is_subj, rec = self.filter_subjective_motion_critique(
                critique_text=assertion.expectation.expected_final_state or assertion.metadata.get("critique", ""),
                test_case_id=test_case_id,
            )
            if is_subj:
                if rec:
                    findings.append(rec)
                results.append(AnimationAssertionResult(
                    assertion_id=assertion.assertion_id,
                    check_type=AnimationCheckType.SUBJECTIVE_MOTION,
                    status=AnimationEvaluationStatus.SKIPPED,
                    target_element_id=assertion.target_element_id,
                    description="Subjective motion critique skipped from defect classification.",
                    recommendation=rec,
                    test_case_id=test_case_id,
                    test_step_id=test_step_id,
                ))
                continue

            res = self.evaluate_transition(
                expectation=assertion.expectation,
                frame_observations=frame_observations,
                source_evidence_ids=source_evidence_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )
            results.append(res)
            if res.defect:
                defects.append(res.defect)

        has_fail = any(r.is_fail for r in results)
        has_blocked = any(r.is_blocked for r in results)
        has_not_verified = any(r.is_not_verified for r in results)

        if has_fail:
            overall_status = AnimationEvaluationStatus.FAIL
        elif has_blocked:
            overall_status = AnimationEvaluationStatus.BLOCKED
        elif has_not_verified:
            overall_status = AnimationEvaluationStatus.NOT_VERIFIED
        else:
            overall_status = AnimationEvaluationStatus.PASS

        return AnimationEvaluationResult(
            evaluation_id=eval_id,
            status=overall_status,
            assertion_results=results,
            defects=defects,
            findings=findings,
            total_frames_evaluated=min(len(frame_observations), self.max_evaluation_frames),
            evidence_ids=sorted(list(all_evidence)),
            test_case_id=test_case_id,
            test_step_id=test_step_id,
            evaluated_at=utc_now(),
            metadata={
                "project_id": self.project_id,
                "execution_id": self.execution_id,
                "work_order_id": self.work_order_id,
            },
        )

    # ---------------------------------------------------------------------------
    # Frame Sampling & State Extraction Helpers
    # ---------------------------------------------------------------------------

    def _sample_bounded_frames(
        self,
        frames: Sequence[VideoFrameObservation],
    ) -> list[VideoFrameObservation]:
        """
        Sample at most MAX_EVALUATION_FRAMES frames from the input sequence.
        Always includes the first and last frames, with evenly spaced intermediate samples.
        """
        n = len(frames)
        if n <= self.max_evaluation_frames:
            return list(frames)

        # Pick first, evenly spaced intermediates, and last
        k = self.max_evaluation_frames
        indices = [int(i * (n - 1) / (k - 1)) for i in range(k)]
        # Deduplicate preserving order
        seen = set()
        sampled = []
        for idx in indices:
            if idx not in seen:
                seen.add(idx)
                sampled.append(frames[idx])

        return sampled

    def _extract_state_from_frame(
        self,
        frame: VideoFrameObservation,
        element_id: str,
    ) -> str:
        """
        Extract observed state string for an element at a specific video frame.
        """
        meta = frame.observation_metadata or {}
        # 1. Look for explicit state key in metadata
        if "state" in meta:
            return str(meta["state"])
        if element_id in meta and isinstance(meta[element_id], dict) and "state" in meta[element_id]:
            return str(meta[element_id]["state"])
        if "element_state" in meta:
            return str(meta["element_state"])
        # 2. Look in description
        if frame.description:
            return frame.description
        return "unknown"

    # ---------------------------------------------------------------------------
    # Defect Factory & Lineage Helpers
    # ---------------------------------------------------------------------------

    def _create_animation_defect(
        self,
        title: str,
        description: str,
        severity: DefectSeverity,
        check_type: AnimationCheckType,
        expected_behavior: str,
        observed_behavior: str,
        reproduction_steps: list[str],
        affected_components: list[str],
        test_case_id: Optional[str] = None,
        evidence_ids: Optional[list[str]] = None,
    ) -> TesterDefect:
        """Construct an authoritative TesterDefect for animation / transition failures."""
        ev_list = list(evidence_ids or [])
        if not ev_list:
            ev_list = [new_evidence_id()]

        return TesterDefect(
            defect_id=new_defect_id(),
            work_order_id=self.work_order_id or "two-default",
            title=title,
            description=description,
            severity=severity,
            defect_type=DefectType.ANIMATION,
            execution_id=self.execution_id,
            reproduction_steps=reproduction_steps,
            expected_behavior=expected_behavior,
            observed_behavior=observed_behavior,
            affected_components=affected_components,
            test_id=test_case_id,
            evidence_ids=ev_list,
            confidence=1.0,
            provenance={
                "evaluator": "AnimationEvaluator",
                "phase": "Phase 6.5",
                "check_type": check_type.value,
                "affected_surface": TestSurface.ANIMATION.value,
            },
            metadata={
                "check_type": check_type.value,
            },
        )

    def _collect_evidence_ids(
        self,
        frames: Sequence[VideoFrameObservation],
        source_evidence_ids: Optional[Sequence[str]] = None,
    ) -> list[str]:
        """Aggregate unique evidence IDs supporting an animation check."""
        ev_set = set()
        if source_evidence_ids:
            ev_set.update(source_evidence_ids)

        for f in frames:
            if f.source_video_evidence_id:
                ev_set.add(f.source_video_evidence_id)
            if f.frame_evidence_id:
                ev_set.add(f.frame_evidence_id)

        if not ev_set:
            ev_set.add(new_evidence_id())

        return sorted(list(ev_set))

    def _validate_frame_lineage(self, frame: VideoFrameObservation) -> None:
        """Validate execution_id and project_id lineage on VideoFrameObservation."""
        if self.execution_id and frame.execution_id and frame.execution_id != self.execution_id:
            raise TesterLineageError(
                f"VideoFrameObservation execution_id ('{frame.execution_id}') does not match evaluator ('{self.execution_id}')."
            )
        if self.project_id and frame.project_id and frame.project_id != self.project_id:
            raise TesterBoundaryViolationError(
                action="ANIMATION_PROJECT_ISOLATION",
                reason=f"VideoFrameObservation project_id ('{frame.project_id}') does not match evaluator ('{self.project_id}').",
            )

    def apply_fix(self, *args, **kwargs) -> Any:
        """Zero fixing guard: Animation evaluator does NOT modify product source or attempt automated fixes."""
        raise TesterBoundaryViolationError(
            action="ANIMATION_AUTO_FIX",
            reason=(
                "AnimationEvaluator is strictly an evaluation component. "
                "Tester does not perform automatic fixes on product or source code."
            ),
        )

    def auto_fix(self, *args, **kwargs) -> Any:
        return self.apply_fix(*args, **kwargs)
