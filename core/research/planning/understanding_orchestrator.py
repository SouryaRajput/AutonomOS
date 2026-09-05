from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import re
from typing import Any, Callable, Optional
import uuid

from core.errors import AutonomOSError
from core.events.model import Event, new_event_id
from core.events.types import EventSource, EventType
from core.research.contracts.intent import (
    Ambiguity,
    ClarificationQuestion,
    EvidenceRequirement,
    IntentConfidence,
    ResearchIntent,
    TemporalScope,
    VersionScope,
)
from core.research.contracts.request import ResearchRequest
from core.research.planning.extractor import (
    DeterministicRequestExtractor,
    ExtractedRequestElements,
)
from core.research.planning.intent_classifier import (
    DeterministicIntentClassifier,
    IntentClassificationResult,
)
from core.research.planning.normalizer import (
    NormalizedResearchRequest,
    ResearchRequestNormalizer,
)
from core.research.planning.understanding_engine import (
    LLMUnderstandingEngine,
    ProposalValidationResult,
    ProposalValidator,
    ResearchIntentProposal,
    UnderstandingError,
)
from core.research.types import (
    DesiredOutput,
    FreshnessRequirement,
    IntentType,
    SourceType,
    UnderstandingStatus,
)

logger = logging.getLogger("AutonomOS.Research.RequestUnderstandingOrchestrator")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# =============================================================================
# 1. Provenance & Result Data Models
# =============================================================================

@dataclass
class UnderstandingProvenance:
    """
    Immutable provenance record tracking all components, inputs, models, and providers
    involved in producing a RequestUnderstandingResult from a ResearchRequest.
    """
    request_id: str
    project_id: str
    task_id: str
    correlation_id: str
    normalizer_applied: bool = False
    classifier_applied: bool = False
    extractor_applied: bool = False
    llm_applied: bool = False
    validator_applied: bool = False
    model_used: Optional[str] = None
    provider_used: Optional[str] = None
    timestamp: str = field(default_factory=utc_now)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "correlation_id": self.correlation_id,
            "normalizer_applied": self.normalizer_applied,
            "classifier_applied": self.classifier_applied,
            "extractor_applied": self.extractor_applied,
            "llm_applied": self.llm_applied,
            "validator_applied": self.validator_applied,
            "model_used": self.model_used,
            "provider_used": self.provider_used,
            "timestamp": self.timestamp,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UnderstandingProvenance:
        return cls(
            request_id=str(data.get("request_id", "")),
            project_id=str(data.get("project_id", "")),
            task_id=str(data.get("task_id", "")),
            correlation_id=str(data.get("correlation_id", "")),
            normalizer_applied=bool(data.get("normalizer_applied", False)),
            classifier_applied=bool(data.get("classifier_applied", False)),
            extractor_applied=bool(data.get("extractor_applied", False)),
            llm_applied=bool(data.get("llm_applied", False)),
            validator_applied=bool(data.get("validator_applied", False)),
            model_used=data.get("model_used"),
            provider_used=data.get("provider_used"),
            timestamp=str(data.get("timestamp", utc_now())),
            details=dict(data.get("details", {})),
        )


@dataclass
class RequestUnderstandingResult:
    """
    Authoritative deliverable produced by RequestUnderstandingOrchestrator.
    Encapsulates the validated ResearchIntent, understanding status, clarification
    questions, validation issues, trace, and audit provenance.
    """
    result_id: str
    status: UnderstandingStatus
    intent: Optional[ResearchIntent]
    clarification_required: bool
    clarification_questions: list[ClarificationQuestion] = field(default_factory=list)
    validation_issues: list[str] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)
    provenance: Optional[UnderstandingProvenance] = None
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "status": self.status.value if isinstance(self.status, UnderstandingStatus) else str(self.status),
            "intent": self.intent.to_dict() if self.intent is not None else None,
            "clarification_required": self.clarification_required,
            "clarification_questions": [q.to_dict() for q in self.clarification_questions],
            "validation_issues": list(self.validation_issues),
            "trace": list(self.trace),
            "provenance": self.provenance.to_dict() if self.provenance is not None else None,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RequestUnderstandingResult:
        st_raw = data.get("status", UnderstandingStatus.FAILED.value)
        try:
            status = UnderstandingStatus(st_raw)
        except (ValueError, TypeError):
            status = UnderstandingStatus.FAILED

        intent = (
            ResearchIntent.from_dict(data["intent"])
            if data.get("intent") is not None
            else None
        )
        questions = [
            ClarificationQuestion.from_dict(q)
            for q in data.get("clarification_questions", [])
        ]
        prov = (
            UnderstandingProvenance.from_dict(data["provenance"])
            if data.get("provenance") is not None
            else None
        )

        return cls(
            result_id=str(data.get("result_id", f"und-res-{uuid.uuid4().hex[:8]}")),
            status=status,
            intent=intent,
            clarification_required=bool(data.get("clarification_required", False)),
            clarification_questions=questions,
            validation_issues=list(data.get("validation_issues", [])),
            trace=list(data.get("trace", [])),
            provenance=prov,
            created_at=str(data.get("created_at", utc_now())),
            metadata=dict(data.get("metadata", {})),
        )


# =============================================================================
# 2. Request Understanding Orchestrator
# =============================================================================

class RequestUnderstandingOrchestrator:
    """
    Bounded orchestrator coordinating the sequential Request Understanding pipeline:
    1. Pre-checks for insufficiency and cancellation.
    2. Deterministic request normalization (2.1.2).
    3. Deterministic intent classification (2.1.3).
    4. Deterministic entity/constraint/dimension extraction (2.1.4).
    5. Conflict detection across requirements, scopes, and constraints.
    6. Analytical LLM understanding proposal generation (2.1.5).
    7. Deterministic validation, ground truth fusion, and status synthesis.

    Safety & Architecture Guarantees:
    - Zero crawler execution, tool calling, query execution, or evidence synthesis.
    - Manager subsystem state is never mutated.
    - LLM output remains an untrusted proposal until gated by ProposalValidator.
    - Complete provenance and trace linkage to the source ResearchRequest.
    """

    def __init__(
        self,
        normalizer: Optional[ResearchRequestNormalizer] = None,
        classifier: Optional[DeterministicIntentClassifier] = None,
        extractor: Optional[DeterministicRequestExtractor] = None,
        llm_engine: Optional[LLMUnderstandingEngine] = None,
        validator: Optional[ProposalValidator] = None,
        event_logger: Optional[Callable[..., Any]] = None,
    ):
        self.normalizer = normalizer or ResearchRequestNormalizer()
        self.classifier = classifier or DeterministicIntentClassifier()
        self.extractor = extractor or DeterministicRequestExtractor()
        self.validator = validator or ProposalValidator()
        self.llm_engine = llm_engine or LLMUnderstandingEngine(validator=self.validator)
        self.event_logger = event_logger

    def understand(self, request: ResearchRequest) -> RequestUnderstandingResult:
        """
        Execute the full Request Understanding sequence on a ResearchRequest.
        Always returns a structured RequestUnderstandingResult with an authoritative status.
        """
        result_id = f"und-res-{uuid.uuid4().hex[:8]}"
        trace: list[str] = []
        validation_issues: list[str] = []
        clarification_questions: list[ClarificationQuestion] = []

        provenance = UnderstandingProvenance(
            request_id=request.request_id if hasattr(request, "request_id") else "",
            project_id=request.project_id if hasattr(request, "project_id") else "",
            task_id=request.task_id if hasattr(request, "task_id") else "",
            correlation_id=request.correlation_id if hasattr(request, "correlation_id") else str(uuid.uuid4()),
        )

        trace.append(f"Started Request Understanding pipeline for request: {provenance.request_id}")
        self._emit_event(
            event_type=EventType.RESEARCH_STARTED,
            payload={"request_id": provenance.request_id, "project_id": provenance.project_id},
            project_id=provenance.project_id,
            task_id=provenance.task_id,
            correlation_id=provenance.correlation_id,
        )

        # ---------------------------------------------------------------------
        # Step 0: Cancellation & Type Sanity Checks
        # ---------------------------------------------------------------------
        if not isinstance(request, ResearchRequest):
            err = f"Invalid request type: expected ResearchRequest, got {type(request).__name__}"
            trace.append(f"Pipeline error: {err}")
            return self._build_result(
                result_id=result_id,
                status=UnderstandingStatus.FAILED,
                intent=None,
                clarification_required=False,
                clarification_questions=[],
                validation_issues=[err],
                trace=trace,
                provenance=provenance,
            )

        if request.metadata.get("cancelled", False):
            msg = "Research request has been cancelled prior to understanding."
            trace.append(msg)
            return self._build_result(
                result_id=result_id,
                status=UnderstandingStatus.FAILED,
                intent=None,
                clarification_required=False,
                clarification_questions=[],
                validation_issues=[msg],
                trace=trace,
                provenance=provenance,
            )

        # ---------------------------------------------------------------------
        # Step 1: Pre-check for Insufficiency
        # ---------------------------------------------------------------------
        raw_obj = (request.objective or "").strip()
        if not raw_obj:
            msg = "Research request objective is empty or whitespace-only."
            trace.append(f"Insufficiency detected: {msg}")
            cq = ClarificationQuestion(
                question_text="The research objective is empty. What topic, question, or goal should be researched?",
                target_ambiguity_id=None,
                options=[],
                default_assumption=None,
            )
            return self._build_result(
                result_id=result_id,
                status=UnderstandingStatus.INSUFFICIENT,
                intent=None,
                clarification_required=True,
                clarification_questions=[cq],
                validation_issues=[msg],
                trace=trace,
                provenance=provenance,
            )

        # Check for vague trivial objective lacking any questions or context
        trivial_tokens = {"research", "do research", "info", "test", "help", "look into it", "find stuff", "check"}
        if raw_obj.lower() in trivial_tokens and not request.questions and not request.context_references:
            msg = f"Research objective '{raw_obj}' is too brief and unspecific to formulate a research plan."
            trace.append(f"Insufficiency detected: {msg}")
            cq = ClarificationQuestion(
                question_text=f"The objective '{raw_obj}' lacks specific targets or criteria. What specific subject, systems, or questions need investigation?",
                target_ambiguity_id=None,
                options=[],
                default_assumption=None,
            )
            return self._build_result(
                result_id=result_id,
                status=UnderstandingStatus.INSUFFICIENT,
                intent=None,
                clarification_required=True,
                clarification_questions=[cq],
                validation_issues=[msg],
                trace=trace,
                provenance=provenance,
            )

        # ---------------------------------------------------------------------
        # Step 2: Deterministic Request Normalization
        # ---------------------------------------------------------------------
        trace.append("Executing deterministic request normalization (2.1.2)")
        try:
            normalized_req = self.normalizer.normalize(request)
            provenance.normalizer_applied = True
            trace.append("Request normalization completed successfully")
        except Exception as e:
            err = f"Deterministic normalization failed: {e}"
            logger.exception(err)
            trace.append(err)
            return self._build_result(
                result_id=result_id,
                status=UnderstandingStatus.FAILED,
                intent=None,
                clarification_required=False,
                clarification_questions=[],
                validation_issues=[err],
                trace=trace,
                provenance=provenance,
            )

        # ---------------------------------------------------------------------
        # Step 3: Deterministic Intent Classification
        # ---------------------------------------------------------------------
        trace.append("Executing deterministic intent classification (2.1.3)")
        try:
            intent_signals = self.classifier.classify(normalized_req)
            provenance.classifier_applied = True
            primary_str = intent_signals.primary_intent.value if intent_signals.primary_intent else "NONE"
            trace.append(
                f"Intent classification detected {len(intent_signals.signals)} signals "
                f"(primary: {primary_str})"
            )
        except Exception as e:
            err = f"Deterministic intent classification failed: {e}"
            logger.exception(err)
            trace.append(err)
            return self._build_result(
                result_id=result_id,
                status=UnderstandingStatus.FAILED,
                intent=None,
                clarification_required=False,
                clarification_questions=[],
                validation_issues=[err],
                trace=trace,
                provenance=provenance,
            )

        # ---------------------------------------------------------------------
        # Step 4: Deterministic Entity/Constraint/Dimension Extraction
        # ---------------------------------------------------------------------
        trace.append("Executing deterministic structured extraction (2.1.4)")
        try:
            extracted_elements = self.extractor.extract(normalized_req)
            provenance.extractor_applied = True
            trace.append(
                f"Extraction completed: {len(extracted_elements.subjects)} subjects, "
                f"{len(extracted_elements.entities)} entities, "
                f"{len(extracted_elements.explicit_constraints)} explicit constraints"
            )
        except Exception as e:
            err = f"Deterministic extraction failed: {e}"
            logger.exception(err)
            trace.append(err)
            return self._build_result(
                result_id=result_id,
                status=UnderstandingStatus.FAILED,
                intent=None,
                clarification_required=False,
                clarification_questions=[],
                validation_issues=[err],
                trace=trace,
                provenance=provenance,
            )

        # ---------------------------------------------------------------------
        # Step 5: Conflict Detection (Pre-LLM)
        # ---------------------------------------------------------------------
        trace.append("Checking for conflicting requirements and scope contradictions")
        conflicts = self._detect_conflicts(request, normalized_req, extracted_elements)
        if conflicts:
            trace.append(f"Detected {len(conflicts)} conflicting requirements: {conflicts}")
            baseline_intent = self._build_baseline_intent(normalized_req, intent_signals, extracted_elements)
            conflict_questions: list[ClarificationQuestion] = []
            for idx, c in enumerate(conflicts):
                amb = baseline_intent.add_ambiguity(
                    description=f"Conflicting requirement: {c}",
                    impact="Blocks coherent research planning due to mutually contradictory constraints.",
                    blocking=True,
                )
                cq = baseline_intent.add_clarification_question(
                    question_text=f"Conflicting requirements detected: {c}. Which requirement should take precedence?",
                    target_ambiguity_id=amb.ambiguity_id,
                )
                conflict_questions.append(cq)

            return self._build_result(
                result_id=result_id,
                status=UnderstandingStatus.CONFLICTING,
                intent=baseline_intent,
                clarification_required=True,
                clarification_questions=conflict_questions,
                validation_issues=conflicts,
                trace=trace,
                provenance=provenance,
            )

        # ---------------------------------------------------------------------
        # Step 6: Analytical LLM Understanding
        # ---------------------------------------------------------------------
        trace.append("Invoking LLM Understanding Engine (2.1.5)")
        try:
            proposal, val_result = self.llm_engine.understand(
                request=normalized_req,
                deterministic_signals=intent_signals,
                extracted_elements=extracted_elements,
            )
            provenance.llm_applied = True
            provenance.model_used = proposal.model_used
            provenance.provider_used = proposal.provider_used
            provenance.validator_applied = True
            trace.append(
                f"LLM proposal received (model: {proposal.model_used}, "
                f"provider: {proposal.provider_used}, valid: {val_result.is_valid})"
            )
        except UnderstandingError as e:
            err = f"LLM understanding engine failed: {e.message}"
            logger.warning(err)
            trace.append(err)
            return self._build_result(
                result_id=result_id,
                status=UnderstandingStatus.FAILED,
                intent=None,
                clarification_required=False,
                clarification_questions=[],
                validation_issues=[err],
                trace=trace,
                provenance=provenance,
            )
        except Exception as e:
            err = f"Unexpected failure in LLM understanding engine: {e}"
            logger.exception(err)
            trace.append(err)
            return self._build_result(
                result_id=result_id,
                status=UnderstandingStatus.FAILED,
                intent=None,
                clarification_required=False,
                clarification_questions=[],
                validation_issues=[err],
                trace=trace,
                provenance=provenance,
            )

        # ---------------------------------------------------------------------
        # Step 7: Proposal Validation Evaluation
        # ---------------------------------------------------------------------
        if not val_result.is_valid:
            trace.append(f"Proposal validation rejected LLM proposal: {val_result.errors}")
            return self._build_result(
                result_id=result_id,
                status=UnderstandingStatus.FAILED,
                intent=None,
                clarification_required=False,
                clarification_questions=[],
                validation_issues=val_result.errors,
                trace=trace,
                provenance=provenance,
            )

        # Validated intent synthesized by ProposalValidator
        intent = val_result.validated_intent
        if intent is None:
            err = "ProposalValidator indicated valid proposal but produced no validated ResearchIntent."
            trace.append(err)
            return self._build_result(
                result_id=result_id,
                status=UnderstandingStatus.FAILED,
                intent=None,
                clarification_required=False,
                clarification_questions=[],
                validation_issues=[err],
                trace=trace,
                provenance=provenance,
            )

        # Merge validation warnings and update trace in intent
        validation_issues.extend(val_result.warnings)
        intent.understanding_trace.extend(trace)

        # Check proposal for newly identified conflicting requirements
        proposal_conflicts = [
            amb.get("description", "")
            for amb in proposal.ambiguities
            if "conflict" in amb.get("description", "").lower()
            or "contradict" in amb.get("description", "").lower()
            or amb.get("ambiguity_type") == "CONFLICTING"
        ]
        if proposal_conflicts:
            trace.append(f"LLM proposal surfaced conflicting requirements: {proposal_conflicts}")
            for pc in proposal_conflicts:
                validation_issues.append(f"Surfaced conflict: {pc}")
            return self._build_result(
                result_id=result_id,
                status=UnderstandingStatus.CONFLICTING,
                intent=intent,
                clarification_required=True,
                clarification_questions=list(intent.clarification_questions),
                validation_issues=validation_issues,
                trace=trace,
                provenance=provenance,
            )

        # ---------------------------------------------------------------------
        # Step 8: Final Status Synthesis
        # ---------------------------------------------------------------------
        # A. Material Ambiguity Check
        if intent.clarification_required or intent.has_blocking_ambiguity():
            trace.append("Material ambiguity detected; clarification required before research planning.")
            return self._build_result(
                result_id=result_id,
                status=UnderstandingStatus.AMBIGUOUS,
                intent=intent,
                clarification_required=True,
                clarification_questions=list(intent.clarification_questions),
                validation_issues=validation_issues,
                trace=trace,
                provenance=provenance,
            )

        # B. Resolvable with Inference Check
        has_inferences = (
            bool(intent.inferred_constraints)
            or bool(intent.assumptions)
            or any(not amb.blocking for amb in intent.ambiguities)
        )
        if has_inferences:
            trace.append(
                f"Resolved with explicit inferences: {len(intent.inferred_constraints)} inferred constraints, "
                f"{len(intent.assumptions)} assumptions, {len(intent.ambiguities)} non-blocking ambiguities."
            )
            return self._build_result(
                result_id=result_id,
                status=UnderstandingStatus.RESOLVABLE_WITH_INFERENCE,
                intent=intent,
                clarification_required=False,
                clarification_questions=[],
                validation_issues=validation_issues,
                trace=trace,
                provenance=provenance,
            )

        # C. Straightforward Resolved Check
        trace.append("Request understanding fully resolved with sufficient explicit information.")
        return self._build_result(
            result_id=result_id,
            status=UnderstandingStatus.RESOLVED,
            intent=intent,
            clarification_required=False,
            clarification_questions=[],
            validation_issues=validation_issues,
            trace=trace,
            provenance=provenance,
        )

    # -------------------------------------------------------------------------
    # Internal Helpers
    # -------------------------------------------------------------------------

    def _detect_conflicts(
        self,
        raw_request: ResearchRequest,
        normalized_req: NormalizedResearchRequest,
        extracted: ExtractedRequestElements,
    ) -> list[str]:
        """Detect mutually contradictory constraints, domain scopes, or directives."""
        conflicts: list[str] = []

        # 1. Domain scope contradiction (allowed domain is also excluded)
        raw_allowed = set(d.lower() for d in getattr(raw_request.scope, "allowed_domains", []))
        raw_excluded = set(d.lower() for d in getattr(raw_request.scope, "excluded_domains", []))
        overlap = raw_allowed.intersection(raw_excluded)
        if not overlap:
            allowed = set(d.lower() for d in normalized_req.scope.allowed_domains)
            excluded = set(d.lower() for d in normalized_req.scope.excluded_domains)
            overlap = allowed.intersection(excluded)

        if overlap:
            conflicts.append(f"Domain(s) {list(overlap)} are both allowed and prohibited in scope.")
        elif "resolved_domain_conflict_in_favor_of_exclusion" in normalized_req.normalization_actions:
            conflicts.append("Domain scope contradiction: allowed domains directly conflict with excluded domains.")

        # 2. Constraints direct negation patterns
        constraints = list(raw_request.constraints) + list(normalized_req.constraints) + [str(c) for c in extracted.explicit_constraints]
        cleaned_c = list(dict.fromkeys(c.strip() for c in constraints if c.strip()))

        for i in range(len(cleaned_c)):
            for j in range(i + 1, len(cleaned_c)):
                c1, c2 = cleaned_c[i].lower(), cleaned_c[j].lower()
                # Check for include X vs do not include X / exclude X
                if self._is_negation_pair(c1, c2):
                    conflicts.append(f"Contradictory constraints: '{cleaned_c[i]}' vs '{cleaned_c[j]}'")

        # 3. Objective vs Constraint contradiction
        # e.g., objective asks to compare A and B, but constraint says do not evaluate B
        if extracted.comparison_targets:
            for target in extracted.comparison_targets:
                t_lower = target.lower()
                for c in cleaned_c:
                    c_lower = c.lower()
                    if (
                        ("exclude" in c_lower or "do not" in c_lower or "must not" in c_lower or "prohibit" in c_lower)
                        and t_lower in c_lower
                    ):
                        conflicts.append(
                            f"Objective targets comparison with '{target}', but constraint contradicts: '{c}'"
                        )

        return conflicts

    def _is_negation_pair(self, c1: str, c2: str) -> bool:
        """Check if two normalized constraint strings directly negate each other."""
        pos_markers = ["must use ", "require ", "include ", "only use ", "strictly use "]
        neg_markers = ["must not use ", "prohibit ", "exclude ", "do not use ", "never use "]

        for p in pos_markers:
            if c1.startswith(p):
                subject = c1[len(p):].strip()
                for n in neg_markers:
                    if c2.startswith(n) and subject in c2:
                        return True
            if c2.startswith(p):
                subject = c2[len(p):].strip()
                for n in neg_markers:
                    if c1.startswith(n) and subject in c1:
                        return True

        # Check exact opposite wording: 'only X' vs 'only Y'
        if c1.startswith("only ") and c2.startswith("only ") and c1 != c2:
            return True

        return False

    def _build_baseline_intent(
        self,
        request: NormalizedResearchRequest,
        signals: IntentClassificationResult,
        extracted: ExtractedRequestElements,
    ) -> ResearchIntent:
        intents = []
        if signals.primary_intent:
            intents.append(signals.primary_intent)
        intents.extend(signals.secondary_intents)
        if not intents:
            intents = [IntentType.DESCRIPTIVE]

        intent = ResearchIntent(
            objective=request.objective,
            intent_types=intents,
            source_request_id=request.request_id,
            correlation_id=request.correlation_id,
        )
        return extracted.apply_to_intent(intent)

    def _build_result(
        self,
        result_id: str,
        status: UnderstandingStatus,
        intent: Optional[ResearchIntent],
        clarification_required: bool,
        clarification_questions: list[ClarificationQuestion],
        validation_issues: list[str],
        trace: list[str],
        provenance: UnderstandingProvenance,
    ) -> RequestUnderstandingResult:
        """Assemble RequestUnderstandingResult and emit completion events."""
        trace.append(f"Request Understanding completed with status: {status.value}")

        if status == UnderstandingStatus.FAILED:
            self._emit_event(
                event_type=EventType.RESEARCH_FAILED,
                payload={"request_id": provenance.request_id, "issues": validation_issues},
                project_id=provenance.project_id,
                task_id=provenance.task_id,
                correlation_id=provenance.correlation_id,
            )

        return RequestUnderstandingResult(
            result_id=result_id,
            status=status,
            intent=intent,
            clarification_required=clarification_required,
            clarification_questions=clarification_questions,
            validation_issues=validation_issues,
            trace=trace,
            provenance=provenance,
            metadata={
                "request_id": provenance.request_id,
                "project_id": provenance.project_id,
            },
        )

    def _emit_event(
        self,
        event_type: EventType,
        payload: dict[str, Any],
        project_id: str,
        task_id: str,
        correlation_id: str,
    ) -> None:
        """Emit telemetry event via event_logger if configured."""
        if self.event_logger is None:
            return

        try:
            self.event_logger(
                event_type=event_type,
                payload=payload,
                source=EventSource.WORKER,
                project_id=project_id,
                task_id=task_id,
                correlation_id=correlation_id,
            )
        except TypeError:
            evt = Event(
                event_id=new_event_id(),
                event_type=event_type,
                source=EventSource.WORKER,
                payload=payload,
                project_id=project_id,
                task_id=task_id,
                correlation_id=correlation_id,
                timestamp=utc_now(),
            )
            try:
                self.event_logger(evt)
            except Exception as e:
                logger.debug(f"Could not emit event via logger: {e}")
        except Exception as e:
            logger.debug(f"Event emission exception: {e}")
