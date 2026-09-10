from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import math
import time
from typing import Any, Callable, Optional, Sequence, Union

from core.tester.contracts.finding import TesterDefect, TesterEvidence, TesterFinding
from core.tester.contracts.geometry import (
    GeometryObservation,
    GeometryStatus,
    Rectangle,
    ViewportDimensions,
)
from core.tester.contracts.identifiers import (
    new_defect_id,
    new_evidence_id,
    new_finding_id,
    new_scroll_evaluation_id,
    new_trace_id,
    validate_execution_id,
)
from core.tester.contracts.scroll import (
    ScrollAssertion,
    ScrollBudget,
    ScrollEvaluationResult,
    ScrollPosition,
)
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    DefectSeverity,
    DefectType,
    FindingCategory,
    ScrollDirection,
    ScrollEvaluationStatus,
    VisibilityState,
)

logger = logging.getLogger("AutonomOS.Tester.ScrollEvaluator")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# Keywords indicating containers that are intentionally scrollable horizontally
INTENTIONAL_HORIZONTAL_KEYWORDS = {
    "carousel",
    "table",
    "data_table",
    "code",
    "pre",
    "tabs",
    "tab_list",
    "horizontal_scroll",
    "slider",
    "gallery",
    "strip",
}


class ScrollEvaluator:
    """
    Phase 6.2: Deterministic Scrolling & Overflow Evaluation Engine.

    Evaluates vertical and horizontal scrolling behavior, scroll position changes,
    content accessibility, and layout overflow.

    Guarantees:
    1. Bounded Scrolling: Never performs unlimited scrolling; strictly enforces
       maximum scroll actions, maximum distance, and maximum duration budgets.
    2. Intentional vs Broken Scroll Separation: Distinguishes intentionally scrollable
       pages/containers from unintended scroll locks and broken overflow.
    3. Intentional Overflow Awareness: Horizontally scrollable carousels, tables,
       code blocks, and galleries are NOT treated as defects.
    4. Zero Performance Judgments: Does not measure or evaluate FPS, memory, CPU,
       or rendering speeds (deferred to Phase 7).
    5. Zero Automatic Fixing: Does not modify product source or attempt automated fixes.
    6. Strict Provenance & Isolation: Enforces project_id and execution_id lineage;
       binds verified evidence to all defects.
    """
    __test__ = False

    def __init__(
        self,
        execution_id: Optional[str] = None,
        project_id: Optional[str] = None,
        work_order_id: Optional[str] = None,
    ) -> None:
        self.execution_id = execution_id
        self.project_id = project_id
        self.work_order_id = work_order_id

    # ---------------------------------------------------------------------------
    # Intentional Horizontal Overflow Helper
    # ---------------------------------------------------------------------------

    def is_intentional_horizontal_container(
        self,
        container_obs: Optional[GeometryObservation] = None,
        container_id: Optional[str] = None,
        is_intentional: bool = False,
    ) -> bool:
        """Return True if the container is an intentionally scrollable horizontal component."""
        if is_intentional:
            return True

        elem_id = ""
        metadata: dict[str, Any] = {}
        if container_obs is not None:
            elem_id = str(container_obs.element_id or "").lower()
            metadata = container_obs.metadata or {}
        elif container_id is not None:
            elem_id = str(container_id).lower()

        if metadata.get("is_carousel") or metadata.get("is_table") or metadata.get("allow_horizontal_scroll"):
            return True

        role = str(metadata.get("role", "")).lower()
        tag = str(metadata.get("tag", "")).lower()
        classes = str(metadata.get("class", "")).lower()

        for kw in INTENTIONAL_HORIZONTAL_KEYWORDS:
            if kw in elem_id or kw in role or kw in tag or kw in classes:
                return True

        return False

    # ---------------------------------------------------------------------------
    # Check 1: Vertical Scroll Evaluation
    # ---------------------------------------------------------------------------

    def evaluate_vertical_scroll(
        self,
        initial_position: ScrollPosition,
        post_position: ScrollPosition,
        expected_scrollable: bool = True,
        target_obs: Optional[GeometryObservation] = None,
        viewport: Optional[ViewportDimensions] = None,
        source_evidence_ids: Optional[Sequence[str]] = None,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
    ) -> ScrollEvaluationResult:
        """
        Evaluate vertical scroll position change and content accessibility.
        Detects unintended scroll locking and unreachable content.
        """
        eval_id = new_scroll_evaluation_id()
        ev_ids = self._collect_evidence_ids(source_evidence_ids, target_obs)

        delta_x, delta_y = initial_position.delta_to(post_position)
        dist = initial_position.distance_to(post_position)
        vp = viewport or ViewportDimensions(
            width=initial_position.viewport_width,
            height=initial_position.viewport_height,
        )

        # Case 1: Expected scrollable, but scroll did not move at all
        if expected_scrollable and abs(delta_y) < 1.0:
            defect_title = "Scroll Failure: Page or container is unintentionally locked from scrolling"
            defect_desc = (
                f"Vertical scroll was requested, but scroll position did not change "
                f"(initial_y: {initial_position.scroll_y:.0f}px, final_y: {post_position.scroll_y:.0f}px, delta_y: {delta_y:.0f}px)."
            )

            defect = self._create_scroll_defect(
                title=defect_title,
                description=defect_desc,
                severity=DefectSeverity.HIGH,
                expected_behavior="Vertical scroll position should change when scrolling down or up.",
                observed_behavior=defect_desc,
                reproduction_steps=[
                    "Navigate to active page.",
                    "Execute vertical scroll action.",
                    "Observe scroll position remains stationary (0px delta).",
                ],
                affected_components=["page_scroll"],
                test_case_id=test_case_id,
                evidence_ids=ev_ids,
            )

            return ScrollEvaluationResult(
                evaluation_id=eval_id,
                status=ScrollEvaluationStatus.FAIL,
                initial_position=initial_position,
                final_position=post_position,
                delta_x=delta_x,
                delta_y=delta_y,
                distance_scrolled_px=dist,
                description=defect_desc,
                defect=defect,
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        # Case 2: Expected locked/stationary, but position moved
        if not expected_scrollable and abs(delta_y) >= 1.0:
            defect_title = "Scroll Failure: Container scrolled when expected locked"
            defect_desc = (
                f"Container was expected to prevent scrolling, but moved by {delta_y:.0f}px."
            )

            defect = self._create_scroll_defect(
                title=defect_title,
                description=defect_desc,
                severity=DefectSeverity.MEDIUM,
                expected_behavior="Scroll should be locked.",
                observed_behavior=defect_desc,
                reproduction_steps=[
                    "Execute scroll action on locked container.",
                    f"Observe container scrolled by {delta_y:.0f}px.",
                ],
                affected_components=["page_scroll"],
                test_case_id=test_case_id,
                evidence_ids=ev_ids,
            )

            return ScrollEvaluationResult(
                evaluation_id=eval_id,
                status=ScrollEvaluationStatus.FAIL,
                initial_position=initial_position,
                final_position=post_position,
                delta_x=delta_x,
                delta_y=delta_y,
                distance_scrolled_px=dist,
                description=defect_desc,
                defect=defect,
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        # Case 3: Target content expected to be reached
        target_reached: Optional[bool] = None
        target_elem_id = target_obs.element_id if target_obs else None

        if target_obs is not None:
            if target_obs.status != GeometryStatus.AVAILABLE or target_obs.bounding_box is None:
                return ScrollEvaluationResult(
                    evaluation_id=eval_id,
                    status=ScrollEvaluationStatus.UNVERIFIED,
                    initial_position=initial_position,
                    final_position=post_position,
                    delta_x=delta_x,
                    delta_y=delta_y,
                    distance_scrolled_px=dist,
                    target_element_id=target_elem_id,
                    description=f"Insufficient geometry: bounding box unavailable for target '{target_elem_id}'.",
                    evidence_ids=ev_ids,
                    test_case_id=test_case_id,
                    test_step_id=test_step_id,
                )

            rect = target_obs.bounding_box
            is_visible_in_vp = (
                target_obs.visibility_state == VisibilityState.VISIBLE
                and rect.bottom > 0
                and rect.top < vp.height
                and rect.right > 0
                and rect.left < vp.width
            )
            target_reached = is_visible_in_vp

            if not target_reached:
                defect_title = f"Inaccessible Content: '{target_elem_id}' is unreachable via scrolling"
                defect_desc = (
                    f"Target content '{target_elem_id}' remains outside the visible viewport "
                    f"after scrolling {abs(delta_y):.0f}px (target bounds: top={rect.top:.0f}px, "
                    f"bottom={rect.bottom:.0f}px, viewport height: {vp.height:.0f}px)."
                )

                is_cta = any(kw in str(target_elem_id).lower() for kw in ("checkout", "submit", "pay", "buy"))
                severity = DefectSeverity.CRITICAL if is_cta else DefectSeverity.HIGH

                defect = self._create_scroll_defect(
                    title=defect_title,
                    description=defect_desc,
                    severity=severity,
                    expected_behavior=f"Target element '{target_elem_id}' should become visible in the viewport after scrolling.",
                    observed_behavior=defect_desc,
                    reproduction_steps=[
                        f"Scroll toward target element '{target_elem_id}'.",
                        f"Observe target remains outside visible viewport at top={rect.top:.0f}px.",
                    ],
                    affected_components=[target_elem_id or "target_element"],
                    test_case_id=test_case_id,
                    evidence_ids=ev_ids,
                )

                return ScrollEvaluationResult(
                    evaluation_id=eval_id,
                    status=ScrollEvaluationStatus.FAIL,
                    initial_position=initial_position,
                    final_position=post_position,
                    delta_x=delta_x,
                    delta_y=delta_y,
                    distance_scrolled_px=dist,
                    target_reached=False,
                    target_element_id=target_elem_id,
                    description=defect_desc,
                    defect=defect,
                    evidence_ids=ev_ids,
                    test_case_id=test_case_id,
                    test_step_id=test_step_id,
                )

        # Successful vertical scroll
        desc = f"Vertical scroll succeeded by {delta_y:.0f}px."
        if target_reached:
            desc += f" Target '{target_elem_id}' reached in viewport."

        return ScrollEvaluationResult(
            evaluation_id=eval_id,
            status=ScrollEvaluationStatus.PASS,
            initial_position=initial_position,
            final_position=post_position,
            delta_x=delta_x,
            delta_y=delta_y,
            distance_scrolled_px=dist,
            target_reached=target_reached,
            target_element_id=target_elem_id,
            description=desc,
            evidence_ids=ev_ids,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
        )

    # ---------------------------------------------------------------------------
    # Check 2: Horizontal Scroll & Intentional Overflow Evaluation
    # ---------------------------------------------------------------------------

    def evaluate_horizontal_scroll(
        self,
        initial_position: ScrollPosition,
        post_position: ScrollPosition,
        container_obs: Optional[GeometryObservation] = None,
        is_intentional: bool = False,
        source_evidence_ids: Optional[Sequence[str]] = None,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
    ) -> ScrollEvaluationResult:
        """
        Evaluate horizontal scrolling, distinguishing intentional horizontal scroll
        (tables, carousels, code blocks) from unexpected horizontal overflow defects.
        """
        eval_id = new_scroll_evaluation_id()
        ev_ids = self._collect_evidence_ids(source_evidence_ids, container_obs)

        delta_x, delta_y = initial_position.delta_to(post_position)
        dist = initial_position.distance_to(post_position)
        c_name = container_obs.element_id if container_obs else "page_root"

        is_intentional_scroll = self.is_intentional_horizontal_container(
            container_obs=container_obs,
            is_intentional=is_intentional,
        )

        # Case A: Intentional horizontal scrolling
        if is_intentional_scroll:
            if abs(delta_x) >= 1.0:
                return ScrollEvaluationResult(
                    evaluation_id=eval_id,
                    status=ScrollEvaluationStatus.PASS,
                    initial_position=initial_position,
                    final_position=post_position,
                    delta_x=delta_x,
                    delta_y=delta_y,
                    distance_scrolled_px=dist,
                    has_unexpected_overflow=False,
                    description=f"Intentional horizontal scroll succeeded on '{c_name}' by {delta_x:.0f}px.",
                    evidence_ids=ev_ids,
                    test_case_id=test_case_id,
                    test_step_id=test_step_id,
                )
            else:
                return ScrollEvaluationResult(
                    evaluation_id=eval_id,
                    status=ScrollEvaluationStatus.PASS,
                    initial_position=initial_position,
                    final_position=post_position,
                    delta_x=0.0,
                    delta_y=delta_y,
                    distance_scrolled_px=dist,
                    has_unexpected_overflow=False,
                    description=f"Intentional horizontal scroll container '{c_name}' evaluated; scroll delta is 0px.",
                    evidence_ids=ev_ids,
                    test_case_id=test_case_id,
                    test_step_id=test_step_id,
                )

        # Case B: Unintended horizontal scroll / overflow on page root
        if abs(delta_x) >= 1.0 or post_position.max_scroll_x > 0:
            defect_title = "Unexpected Horizontal Overflow: Layout overflows viewport horizontally"
            defect_desc = (
                f"Unexpected horizontal scrolling occurred on '{c_name}' by {delta_x:.0f}px "
                f"(max_scroll_x: {post_position.max_scroll_x:.0f}px). Page is not designated for horizontal scroll."
            )

            defect = self._create_scroll_defect(
                title=defect_title,
                description=defect_desc,
                severity=DefectSeverity.HIGH if abs(delta_x) >= 50.0 else DefectSeverity.MEDIUM,
                expected_behavior="Layout should fit within viewport width without unexpected horizontal overflow.",
                observed_behavior=defect_desc,
                reproduction_steps=[
                    f"View '{c_name}' at viewport width {initial_position.viewport_width:.0f}px.",
                    f"Observe horizontal scroll delta of {delta_x:.0f}px (overflow: {post_position.max_scroll_x:.0f}px).",
                ],
                affected_components=[c_name],
                test_case_id=test_case_id,
                evidence_ids=ev_ids,
            )

            return ScrollEvaluationResult(
                evaluation_id=eval_id,
                status=ScrollEvaluationStatus.FAIL,
                initial_position=initial_position,
                final_position=post_position,
                delta_x=delta_x,
                delta_y=delta_y,
                distance_scrolled_px=dist,
                has_unexpected_overflow=True,
                description=defect_desc,
                defect=defect,
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        return ScrollEvaluationResult(
            evaluation_id=eval_id,
            status=ScrollEvaluationStatus.PASS,
            initial_position=initial_position,
            final_position=post_position,
            delta_x=0.0,
            delta_y=0.0,
            distance_scrolled_px=0.0,
            has_unexpected_overflow=False,
            description="No unexpected horizontal overflow observed.",
            evidence_ids=ev_ids,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
        )

    # ---------------------------------------------------------------------------
    # Check 3: Bounded Active Scroll Execution Toward Element
    # ---------------------------------------------------------------------------

    def evaluate_scroll_to_element(
        self,
        target_element_id: str,
        get_element_geometry_fn: Callable[[], Optional[GeometryObservation]],
        get_scroll_position_fn: Callable[[], ScrollPosition],
        scroll_fn: Callable[[str, int], tuple[bool, Optional[str]]],
        budget: Optional[ScrollBudget] = None,
        direction: str = "down",
        viewport: Optional[ViewportDimensions] = None,
        source_evidence_ids: Optional[Sequence[str]] = None,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
    ) -> ScrollEvaluationResult:
        """
        Execute finite, bounded scrolling actions to bring target element into the viewport.
        Strictly enforces finite budget limits to guarantee infinite scrolling is impossible.
        """
        eval_id = new_scroll_evaluation_id()
        scroll_budget = budget or ScrollBudget()
        ev_ids = list(source_evidence_ids or [])

        init_pos = get_scroll_position_fn()
        vp = viewport or ViewportDimensions(
            width=init_pos.viewport_width,
            height=init_pos.viewport_height,
        )

        actions_performed = 0
        total_distance = 0.0
        start_time = time.monotonic()
        curr_pos = init_pos
        runtime_err: Optional[str] = None

        while True:
            # 1. Budget guards against infinite scrolling
            if actions_performed >= scroll_budget.max_actions:
                logger.info(f"Scroll budget actions limit reached ({actions_performed}/{scroll_budget.max_actions}).")
                break
            if total_distance >= scroll_budget.max_distance_px:
                logger.info(f"Scroll budget distance limit reached ({total_distance:.0f}/{scroll_budget.max_distance_px}px).")
                break
            if (time.monotonic() - start_time) >= scroll_budget.max_duration_seconds:
                logger.info("Scroll budget duration limit reached.")
                break

            # 2. Check if target element is already visible in viewport
            target_geom = get_element_geometry_fn()
            if target_geom and target_geom.status == GeometryStatus.AVAILABLE and target_geom.bounding_box:
                rect = target_geom.bounding_box
                if (
                    target_geom.visibility_state == VisibilityState.VISIBLE
                    and rect.bottom > 0
                    and rect.top < vp.height
                    and rect.right > 0
                    and rect.left < vp.width
                ):
                    # Target reached!
                    duration = time.monotonic() - start_time
                    delta_x, delta_y = init_pos.delta_to(curr_pos)
                    return ScrollEvaluationResult(
                        evaluation_id=eval_id,
                        status=ScrollEvaluationStatus.PASS,
                        initial_position=init_pos,
                        final_position=curr_pos,
                        delta_x=delta_x,
                        delta_y=delta_y,
                        actions_performed=actions_performed,
                        distance_scrolled_px=total_distance,
                        duration_seconds=duration,
                        target_reached=True,
                        target_element_id=target_element_id,
                        description=(
                            f"Target '{target_element_id}' successfully reached in viewport "
                            f"after {actions_performed} scroll actions ({total_distance:.0f}px scrolled)."
                        ),
                        evidence_ids=ev_ids,
                        test_case_id=test_case_id,
                        test_step_id=test_step_id,
                    )

            # 3. Perform next bounded scroll action
            prev_pos = curr_pos
            success, err = scroll_fn(direction, int(scroll_budget.step_size_px))
            if not success:
                runtime_err = err or "Scroll action failed in runtime"
                logger.warning(f"Scroll runtime failure: {runtime_err}")
                break

            actions_performed += 1
            curr_pos = get_scroll_position_fn()
            step_delta_x, step_delta_y = prev_pos.delta_to(curr_pos)
            step_dist = math.hypot(step_delta_x, step_delta_y)
            total_distance += step_dist

            # 4. Anti-loop guard: If scroll position did not move at all, break immediately
            if step_dist < 1.0:
                logger.info("Scroll position stalled (end of scrollable area or locked). Halting.")
                break

        duration = time.monotonic() - start_time
        delta_x, delta_y = init_pos.delta_to(curr_pos)

        # Check for runtime error
        if runtime_err:
            return ScrollEvaluationResult(
                evaluation_id=eval_id,
                status=ScrollEvaluationStatus.BLOCKED,
                initial_position=init_pos,
                final_position=curr_pos,
                delta_x=delta_x,
                delta_y=delta_y,
                actions_performed=actions_performed,
                distance_scrolled_px=total_distance,
                duration_seconds=duration,
                target_reached=False,
                target_element_id=target_element_id,
                description=f"Runtime error during scrolling: {runtime_err}",
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        # Final check on target element
        final_geom = get_element_geometry_fn()
        target_in_vp = False
        if final_geom and final_geom.status == GeometryStatus.AVAILABLE and final_geom.bounding_box:
            rect = final_geom.bounding_box
            target_in_vp = (
                final_geom.visibility_state == VisibilityState.VISIBLE
                and rect.bottom > 0
                and rect.top < vp.height
                and rect.right > 0
                and rect.left < vp.width
            )

        if target_in_vp:
            return ScrollEvaluationResult(
                evaluation_id=eval_id,
                status=ScrollEvaluationStatus.PASS,
                initial_position=init_pos,
                final_position=curr_pos,
                delta_x=delta_x,
                delta_y=delta_y,
                actions_performed=actions_performed,
                distance_scrolled_px=total_distance,
                duration_seconds=duration,
                target_reached=True,
                target_element_id=target_element_id,
                description=f"Target '{target_element_id}' reached in viewport.",
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        # Target was not reached
        if actions_performed > 0 and total_distance < 1.0:
            defect_title = f"Scroll Failure: Page locked when attempting to reach '{target_element_id}'"
            defect_desc = (
                f"Attempted {actions_performed} scroll actions toward '{target_element_id}', "
                f"but scroll position did not move (scroll locked)."
            )
            defect_sev = DefectSeverity.HIGH
        else:
            defect_title = f"Inaccessible Content: '{target_element_id}' not reached within scroll budget"
            defect_desc = (
                f"Target element '{target_element_id}' was not reached in viewport after "
                f"{actions_performed} scroll actions ({total_distance:.0f}px scrolled, budget: {scroll_budget.max_actions} actions)."
            )
            defect_sev = DefectSeverity.HIGH

        defect = self._create_scroll_defect(
            title=defect_title,
            description=defect_desc,
            severity=defect_sev,
            expected_behavior=f"Target element '{target_element_id}' should become accessible and visible via scrolling.",
            observed_behavior=defect_desc,
            reproduction_steps=[
                f"Scroll toward target element '{target_element_id}'.",
                f"Observe target remains unreachable after {actions_performed} actions ({total_distance:.0f}px).",
            ],
            affected_components=[target_element_id],
            test_case_id=test_case_id,
            evidence_ids=ev_ids,
        )

        return ScrollEvaluationResult(
            evaluation_id=eval_id,
            status=ScrollEvaluationStatus.FAIL,
            initial_position=init_pos,
            final_position=curr_pos,
            delta_x=delta_x,
            delta_y=delta_y,
            actions_performed=actions_performed,
            distance_scrolled_px=total_distance,
            duration_seconds=duration,
            target_reached=False,
            target_element_id=target_element_id,
            description=defect_desc,
            defect=defect,
            evidence_ids=ev_ids,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
        )

    # ---------------------------------------------------------------------------
    # Check 4: Unexpected Horizontal Overflow on Viewport
    # ---------------------------------------------------------------------------

    def evaluate_unexpected_horizontal_overflow(
        self,
        page_width: float,
        viewport_width: float,
        has_horizontal_scrollbar: bool = False,
        intentional_containers: Optional[Sequence[str]] = None,
        tolerance_px: float = 1.0,
        source_evidence_ids: Optional[Sequence[str]] = None,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
    ) -> ScrollEvaluationResult:
        """
        Evaluate unexpected page-level horizontal overflow against viewport width.
        """
        eval_id = new_scroll_evaluation_id()
        ev_ids = list(source_evidence_ids or [])
        overflow_px = max(0.0, page_width - viewport_width)

        has_overflow = overflow_px > tolerance_px or has_horizontal_scrollbar
        has_intentional = bool(intentional_containers)

        if has_overflow and not has_intentional:
            defect_title = f"Unexpected Horizontal Overflow: Page width ({page_width:.0f}px) exceeds viewport ({viewport_width:.0f}px)"
            defect_desc = (
                f"Document layout overflows viewport width by {overflow_px:.1f}px "
                f"(page_width: {page_width:.0f}px, viewport_width: {viewport_width:.0f}px). "
                "Causes unintended horizontal scrollbar."
            )

            defect = self._create_scroll_defect(
                title=defect_title,
                description=defect_desc,
                severity=DefectSeverity.HIGH if overflow_px >= 50.0 else DefectSeverity.MEDIUM,
                expected_behavior=f"Document layout should not exceed viewport width ({viewport_width:.0f}px).",
                observed_behavior=defect_desc,
                reproduction_steps=[
                    f"Set viewport width to {viewport_width:.0f}px.",
                    f"Measure document width ({page_width:.0f}px).",
                    f"Observe horizontal overflow of {overflow_px:.1f}px.",
                ],
                affected_components=["document_layout"],
                test_case_id=test_case_id,
                evidence_ids=ev_ids,
            )

            return ScrollEvaluationResult(
                evaluation_id=eval_id,
                status=ScrollEvaluationStatus.FAIL,
                delta_x=overflow_px,
                has_unexpected_overflow=True,
                description=defect_desc,
                defect=defect,
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        return ScrollEvaluationResult(
            evaluation_id=eval_id,
            status=ScrollEvaluationStatus.PASS,
            delta_x=0.0,
            has_unexpected_overflow=False,
            description=f"Page width ({page_width:.0f}px) cleanly fits viewport ({viewport_width:.0f}px).",
            evidence_ids=ev_ids,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
        )

    # ---------------------------------------------------------------------------
    # Internal Helpers
    # ---------------------------------------------------------------------------

    def _collect_evidence_ids(
        self,
        source_evidence_ids: Optional[Sequence[str]] = None,
        target_obs: Optional[GeometryObservation] = None,
    ) -> list[str]:
        """Collect and aggregate unique evidence IDs."""
        ev_set = set()
        if source_evidence_ids:
            ev_set.update(source_evidence_ids)
        if target_obs:
            if target_obs.source_evidence_id:
                ev_set.add(target_obs.source_evidence_id)
            for eid in target_obs.metadata.get("evidence_ids", []):
                ev_set.add(eid)

        return sorted(list(ev_set))

    def _create_scroll_defect(
        self,
        title: str,
        description: str,
        severity: DefectSeverity,
        expected_behavior: str,
        observed_behavior: str,
        reproduction_steps: list[str],
        affected_components: list[str],
        test_case_id: Optional[str] = None,
        evidence_ids: Optional[list[str]] = None,
    ) -> TesterDefect:
        """Construct an authoritative TesterDefect grounded in scroll evidence."""
        ev_list = list(evidence_ids or [])
        if not ev_list:
            ev_list = [new_evidence_id()]

        return TesterDefect(
            defect_id=new_defect_id(),
            work_order_id=self.work_order_id or "two-default",
            title=title,
            description=description,
            severity=severity,
            defect_type=DefectType.SCROLL,
            execution_id=self.execution_id,
            reproduction_steps=reproduction_steps,
            expected_behavior=expected_behavior,
            observed_behavior=observed_behavior,
            affected_components=affected_components,
            test_id=test_case_id,
            evidence_ids=ev_list,
            confidence=1.0,
            provenance={
                "evaluator": "ScrollEvaluator",
                "phase": "Phase 6.2",
                "timestamp": utc_now(),
            },
        )
