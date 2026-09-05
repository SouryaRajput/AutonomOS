from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Optional

from core.research.contracts.request import ResearchRequest
from core.research.types import IntentType


@dataclass
class IntentSignal:
    """
    An individual deterministic signal pointing toward a specific research intent.
    Carries the detected rule, confidence strength, matched cues, and rationale.
    """
    intent_type: IntentType
    confidence: float
    rule_name: str
    matched_cues: list[str] = field(default_factory=list)
    rationale: str = ""
    source_field: str = "objective"

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_type": (
                self.intent_type.value
                if isinstance(self.intent_type, IntentType)
                else str(self.intent_type)
            ),
            "confidence": round(self.confidence, 4),
            "rule_name": self.rule_name,
            "matched_cues": list(self.matched_cues),
            "rationale": self.rationale,
            "source_field": self.source_field,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IntentSignal:
        raw_it = data.get("intent_type", IntentType.DESCRIPTIVE.value)
        try:
            it = IntentType(raw_it)
        except (ValueError, TypeError):
            it = IntentType.DESCRIPTIVE

        return cls(
            intent_type=it,
            confidence=float(data.get("confidence", 0.5)),
            rule_name=str(data.get("rule_name", "unknown_rule")),
            matched_cues=list(data.get("matched_cues", [])),
            rationale=str(data.get("rationale", "")),
            source_field=str(data.get("source_field", "objective")),
        )


@dataclass
class IntentClassificationResult:
    """
    Result of deterministic intent classification on a ResearchRequest.
    Distinguishes individual signals, aggregate confidences, primary/secondary
    classifications, and explicit ambiguity flags.
    """
    signals: list[IntentSignal] = field(default_factory=list)
    primary_intent: Optional[IntentType] = None
    secondary_intents: list[IntentType] = field(default_factory=list)
    aggregate_confidence: dict[IntentType, float] = field(default_factory=dict)
    is_ambiguous: bool = False
    ambiguity_reason: Optional[str] = None
    classification_trace: list[str] = field(default_factory=list)

    def has_intent(self, intent_type: IntentType | str, min_confidence: float = 0.5) -> bool:
        """Return True if the specified intent was detected with confidence >= min_confidence."""
        match_val = intent_type.value if isinstance(intent_type, IntentType) else str(intent_type)
        for it, score in self.aggregate_confidence.items():
            it_val = it.value if isinstance(it, IntentType) else str(it)
            if it_val == match_val and score >= min_confidence:
                return True
        return False

    def get_confidence(self, intent_type: IntentType | str) -> float:
        """Return aggregate confidence score for an intent type (0.0 if not detected)."""
        match_val = intent_type.value if isinstance(intent_type, IntentType) else str(intent_type)
        for it, score in self.aggregate_confidence.items():
            it_val = it.value if isinstance(it, IntentType) else str(it)
            if it_val == match_val:
                return score
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_intent": (
                self.primary_intent.value
                if isinstance(self.primary_intent, IntentType)
                else (str(self.primary_intent) if self.primary_intent else None)
            ),
            "secondary_intents": [
                it.value if isinstance(it, IntentType) else str(it)
                for it in self.secondary_intents
            ],
            "aggregate_confidence": {
                (k.value if isinstance(k, IntentType) else str(k)): round(v, 4)
                for k, v in self.aggregate_confidence.items()
            },
            "is_ambiguous": self.is_ambiguous,
            "ambiguity_reason": self.ambiguity_reason,
            "signals": [s.to_dict() for s in self.signals],
            "classification_trace": list(self.classification_trace),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IntentClassificationResult:
        pi_raw = data.get("primary_intent")
        primary = None
        if pi_raw:
            try:
                primary = IntentType(pi_raw)
            except (ValueError, TypeError):
                primary = None

        sec_list: list[IntentType] = []
        for s in data.get("secondary_intents", []):
            try:
                sec_list.append(IntentType(s))
            except (ValueError, TypeError):
                pass

        agg: dict[IntentType, float] = {}
        for k, v in data.get("aggregate_confidence", {}).items():
            try:
                agg[IntentType(k)] = float(v)
            except (ValueError, TypeError):
                pass

        signals = [
            IntentSignal.from_dict(sd) if isinstance(sd, dict) else sd
            for sd in data.get("signals", [])
        ]

        return cls(
            signals=signals,
            primary_intent=primary,
            secondary_intents=sec_list,
            aggregate_confidence=agg,
            is_ambiguous=bool(data.get("is_ambiguous", False)),
            ambiguity_reason=data.get("ambiguity_reason"),
            classification_trace=list(data.get("classification_trace", [])),
        )


class DeterministicIntentClassifier:
    """
    Deterministic rule-based intent classifier for ResearchRequest inputs.
    Identifies likely IntentType categories from linguistic framing, sentence structure,
    syntax patterns, and explicit cues without calling an LLM or inferring hidden semantics.
    """

    # Maximum confidence score assigned by deterministic rules (never 1.0 absolute certainty)
    MAX_DETERMINISTIC_CONFIDENCE = 0.98

    def classify(self, request: ResearchRequest) -> IntentClassificationResult:
        """
        Classify a ResearchRequest to detect likely IntentType categories.
        Aggregates signals from objective, questions, constraints, and metadata.
        """
        if not request:
            return IntentClassificationResult(
                is_ambiguous=True,
                ambiguity_reason="Empty or null request provided",
                classification_trace=["Encountered null request; aborted classification."],
            )

        signals: list[IntentSignal] = []
        trace: list[str] = []

        # 1. Evaluate Objective (Weight: 1.0)
        obj_text = request.objective or ""
        if obj_text.strip():
            obj_signals = self._scan_text(obj_text, source_field="objective", base_weight=1.0)
            signals.extend(obj_signals)
            trace.append(f"Scanned objective: detected {len(obj_signals)} signal(s).")
        else:
            trace.append("Objective is empty or whitespace.")

        # 2. Evaluate Questions (Weight: 0.85 per question)
        questions = request.questions or []
        for idx, q in enumerate(questions):
            q_signals = self._scan_text(q, source_field=f"question[{idx}]", base_weight=0.85)
            signals.extend(q_signals)
            if q_signals:
                trace.append(f"Scanned question[{idx}]: detected {len(q_signals)} signal(s).")

        # 3. Evaluate Constraints (Weight: 0.60)
        constraints = request.constraints or []
        for idx, c in enumerate(constraints):
            c_signals = self._scan_text(c, source_field=f"constraint[{idx}]", base_weight=0.60)
            signals.extend(c_signals)

        # 4. Evaluate Metadata hints (Weight: 0.75)
        meta_signals = self._scan_metadata(request.metadata)
        signals.extend(meta_signals)
        if meta_signals:
            trace.append(f"Scanned metadata: detected {len(meta_signals)} signal(s).")

        # 5. Aggregate Signals per IntentType using Noisy-OR
        aggregate = self._aggregate_signals(signals)
        trace.append(f"Aggregated {len(signals)} raw signal(s) into {len(aggregate)} distinct intent(s).")

        # 6. Rank Intents and Determine Primary & Secondary
        sorted_intents = sorted(aggregate.items(), key=lambda x: x[1], reverse=True)

        primary_intent: Optional[IntentType] = None
        secondary_intents: list[IntentType] = []
        is_ambiguous = False
        ambiguity_reason: Optional[str] = None

        if not sorted_intents or sorted_intents[0][1] < 0.40:
            is_ambiguous = True
            ambiguity_reason = "No strong intent signals detected from request structure"
            trace.append("Flagged as ambiguous: maximum signal confidence < 0.40.")
            if sorted_intents:
                primary_intent = sorted_intents[0][0]
        else:
            primary_intent = sorted_intents[0][0]
            top_score = sorted_intents[0][1]

            # Collect secondary confident intents (score >= 0.50)
            for it, score in sorted_intents[1:]:
                if score >= 0.50:
                    secondary_intents.append(it)

            # Check for competing / tied intents
            if len(sorted_intents) >= 2:
                second_it, second_score = sorted_intents[1]
                # If top two are close (diff <= 0.05) and neither is overwhelmingly dominant (top < 0.80)
                if abs(top_score - second_score) <= 0.05 and top_score < 0.80:
                    is_ambiguous = True
                    ambiguity_reason = (
                        f"Competing intent signals with similar confidence: "
                        f"{primary_intent.value} ({top_score:.2f}) vs {second_it.value} ({second_score:.2f})"
                    )
                    trace.append(f"Flagged as ambiguous: {ambiguity_reason}.")

        return IntentClassificationResult(
            signals=signals,
            primary_intent=primary_intent,
            secondary_intents=secondary_intents,
            aggregate_confidence=aggregate,
            is_ambiguous=is_ambiguous,
            ambiguity_reason=ambiguity_reason,
            classification_trace=trace,
        )

    # -------------------------------------------------------------------------
    # Text Scanner & Rule Matching
    # -------------------------------------------------------------------------

    def _scan_text(self, text: str, source_field: str, base_weight: float) -> list[IntentSignal]:
        """Scan a string for structural patterns and cues across all 10 intent categories."""
        signals: list[IntentSignal] = []
        clean_text = text.strip()
        lower_text = clean_text.lower()

        # 1. COMPARATIVE Patterns
        comp_signals = self._match_comparative(clean_text, lower_text, source_field, base_weight)
        signals.extend(comp_signals)

        # 2. DIAGNOSTIC Patterns
        diag_signals = self._match_diagnostic(clean_text, lower_text, source_field, base_weight)
        signals.extend(diag_signals)

        # 3. IMPLEMENTATION Patterns
        impl_signals = self._match_implementation(clean_text, lower_text, source_field, base_weight)
        signals.extend(impl_signals)

        # 4. VERIFICATION Patterns
        veri_signals = self._match_verification(clean_text, lower_text, source_field, base_weight)
        signals.extend(veri_signals)

        # 5. DESCRIPTIVE Patterns
        desc_signals = self._match_descriptive(clean_text, lower_text, source_field, base_weight)
        signals.extend(desc_signals)

        # 6. EVALUATIVE Patterns
        eval_signals = self._match_evaluative(clean_text, lower_text, source_field, base_weight)
        signals.extend(eval_signals)

        # 7. TECHNICAL Patterns
        tech_signals = self._match_technical(clean_text, lower_text, source_field, base_weight)
        signals.extend(tech_signals)

        # 8. DECISION_SUPPORT Patterns
        dec_signals = self._match_decision_support(clean_text, lower_text, source_field, base_weight)
        signals.extend(dec_signals)

        # 9. HISTORICAL Patterns
        hist_signals = self._match_historical(clean_text, lower_text, source_field, base_weight)
        signals.extend(hist_signals)

        # 10. EXPLORATORY Patterns
        expl_signals = self._match_exploratory(clean_text, lower_text, source_field, base_weight)
        signals.extend(expl_signals)

        return signals

    # -------------------------------------------------------------------------
    # Intent-Specific Rule Evaluators
    # -------------------------------------------------------------------------

    def _match_comparative(self, text: str, lower: str, source: str, weight: float) -> list[IntentSignal]:
        signals = []
        # Pattern 1: compare X with/to/and/versus Y
        if re.search(r"\bcompare\s+.+?\s+(?:with|to|and|versus|vs\.?)\s+.+", lower):
            signals.append(
                IntentSignal(
                    intent_type=IntentType.COMPARATIVE,
                    confidence=min(0.92 * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="comparative_syntax_pattern",
                    matched_cues=["compare ... with/to/vs"],
                    rationale="Explicit comparison between multiple entities",
                    source_field=source,
                )
            )
        # Pattern 2: tradeoffs or differences between X and Y
        elif re.search(r"\b(?:differences?|distinctions?|tradeoffs?|pros\s+and\s+cons)\s+between\b", lower):
            signals.append(
                IntentSignal(
                    intent_type=IntentType.COMPARATIVE,
                    confidence=min(0.88 * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="comparative_difference_between_pattern",
                    matched_cues=["difference/tradeoff between"],
                    rationale="Explicit contrastive inquiry between targets",
                    source_field=source,
                )
            )
        # Pattern 3: X vs Y
        elif re.search(r"\b[A-Za-z0-9_.-]+\s+(?:vs\.?|versus)\s+[A-Za-z0-9_.-]+\b", lower):
            signals.append(
                IntentSignal(
                    intent_type=IntentType.COMPARATIVE,
                    confidence=min(0.85 * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="comparative_versus_pattern",
                    matched_cues=["vs / versus"],
                    rationale="Direct versus comparison construct",
                    source_field=source,
                )
            )
        # Pattern 4: A or B in choice/comparison context
        elif re.search(r"\b[A-Za-z0-9_.-]+\s+or\s+[A-Za-z0-9_.-]+\b", lower) and any(
            w in lower for w in ["choose", "pick", "prefer", "which", "versus", "vs", "better", "difference"]
        ):
            signals.append(
                IntentSignal(
                    intent_type=IntentType.COMPARATIVE,
                    confidence=min(0.75 * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="comparative_choice_options_pattern",
                    matched_cues=["A or B choice"],
                    rationale="Choice between alternative options implies comparative analysis",
                    source_field=source,
                )
            )
        elif any(w in lower for w in ["compare", "comparative", "comparison"]):
            signals.append(
                IntentSignal(
                    intent_type=IntentType.COMPARATIVE,
                    confidence=min(0.60 * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="comparative_keyword_cue",
                    matched_cues=["compare keyword"],
                    rationale="Contains comparative lexical token",
                    source_field=source,
                )
            )
        return signals

    def _match_diagnostic(self, text: str, lower: str, source: str, weight: float) -> list[IntentSignal]:
        signals = []
        # Pattern 1: why is X failing/broken/crashing/slow
        if re.search(r"\bwhy\s+(?:is|are|does|did|was|were)\s+.+?\s+(?:fail(?:ing|ed|s)?|crash(?:ing|ed|es)?|broken|leak(?:ing|ed|s)?|slow|drop(?:ping|ped)?|throw(?:ing|s|n)?|timeout|timing\s*out)\b", lower):
            signals.append(
                IntentSignal(
                    intent_type=IntentType.DIAGNOSTIC,
                    confidence=min(0.92 * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="diagnostic_why_failing_pattern",
                    matched_cues=["why is ... failing/crashing/broken"],
                    rationale="Interrogative diagnosis of runtime failure or regression",
                    source_field=source,
                )
            )
        # Pattern 2: root cause or troubleshoot
        elif re.search(r"\b(?:root\s+cause|troubleshoot|diagnose|debug|how\s+to\s+fix|investigate\s+(?:bug|issue|crash|failure|regression|leak))\b", lower):
            signals.append(
                IntentSignal(
                    intent_type=IntentType.DIAGNOSTIC,
                    confidence=min(0.88 * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="diagnostic_root_cause_pattern",
                    matched_cues=["root cause / troubleshoot / debug"],
                    rationale="Explicit diagnostic investigation framing",
                    source_field=source,
                )
            )
        elif any(w in lower for w in ["traceback", "stacktrace", "exception", "memory leak", "deadlock", "segfault", "error code"]):
            signals.append(
                IntentSignal(
                    intent_type=IntentType.DIAGNOSTIC,
                    confidence=min(0.70 * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="diagnostic_error_cue",
                    matched_cues=["error/exception cue"],
                    rationale="Contains runtime failure terminology",
                    source_field=source,
                )
            )
        return signals

    def _match_implementation(self, text: str, lower: str, source: str, weight: float) -> list[IntentSignal]:
        signals = []
        # Pattern 1: how to implement/build/create/configure
        if re.search(r"\bhow\s+(?:do\s+(?:i|we)|can\s+(?:i|we)|to)\s+(?:implement|build|create|setup|configure|write|wire|integrate|deploy|migrate)\b", lower):
            signals.append(
                IntentSignal(
                    intent_type=IntentType.IMPLEMENTATION,
                    confidence=min(0.90 * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="implementation_how_to_pattern",
                    matched_cues=["how to implement/build/configure"],
                    rationale="Actionable guidance on implementing a software solution",
                    source_field=source,
                )
            )
        # Pattern 2: step-by-step or code example
        elif re.search(r"\b(?:step-by-step\s+(?:guide|instructions?)|code\s+example|walkthrough|sample\s+code)\b", lower):
            signals.append(
                IntentSignal(
                    intent_type=IntentType.IMPLEMENTATION,
                    confidence=min(0.85 * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="implementation_guide_pattern",
                    matched_cues=["step-by-step / code example"],
                    rationale="Requests practical code examples or setup walkthrough",
                    source_field=source,
                )
            )
        elif any(w in lower for w in ["implement", "implementation", "scaffolding"]):
            signals.append(
                IntentSignal(
                    intent_type=IntentType.IMPLEMENTATION,
                    confidence=min(0.60 * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="implementation_keyword_cue",
                    matched_cues=["implement keyword"],
                    rationale="Mentions implementation construct",
                    source_field=source,
                )
            )
        return signals

    def _match_verification(self, text: str, lower: str, source: str, weight: float) -> list[IntentSignal]:
        signals = []
        # Pattern 1: is X true / supported / compatible
        if re.search(r"^(?:is|are|does|do|can|will|has|have|was|were)\s+.+?\s+(?:true|supported|compatible|deprecated|safe|valid|working|available)\b", lower):
            signals.append(
                IntentSignal(
                    intent_type=IntentType.VERIFICATION,
                    confidence=min(0.88 * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="verification_polar_interrogative",
                    matched_cues=["is ... true/supported/compatible"],
                    rationale="Polar interrogative seeking fact verification",
                    source_field=source,
                )
            )
        # Pattern 2: verify / check if / confirm that
        elif re.search(r"\b(?:verify\s+(?:whether|that|if)|check\s+(?:if|whether)|confirm\s+(?:that|whether)|validate\s+(?:that|whether))\b", lower):
            signals.append(
                IntentSignal(
                    intent_type=IntentType.VERIFICATION,
                    confidence=min(0.85 * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="verification_action_pattern",
                    matched_cues=["verify / check / confirm"],
                    rationale="Explicit directive to verify a claim or condition",
                    source_field=source,
                )
            )
        return signals

    def _match_descriptive(self, text: str, lower: str, source: str, weight: float) -> list[IntentSignal]:
        signals = []
        # Pattern 1: what is X / who is X / define X
        if re.search(r"^(?:what|who)\s+(?:is|are|was|were)\s+(.+)", lower) or re.search(r"^(?:define|describe)\s+(.+)", lower):
            signals.append(
                IntentSignal(
                    intent_type=IntentType.DESCRIPTIVE,
                    confidence=min(0.85 * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="descriptive_what_is_pattern",
                    matched_cues=["what is / define / describe"],
                    rationale="Requests definitional or descriptive explanation of a concept",
                    source_field=source,
                )
            )
        elif re.search(r"\b(?:overview\s+(?:of|for)|summary\s+(?:of|for)|background\s+(?:on|of)|explain\s+what\b)", lower):
            signals.append(
                IntentSignal(
                    intent_type=IntentType.DESCRIPTIVE,
                    confidence=min(0.80 * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="descriptive_overview_pattern",
                    matched_cues=["overview of / summary of / background"],
                    rationale="Requests overview or summary description",
                    source_field=source,
                )
            )
        return signals

    def _match_evaluative(self, text: str, lower: str, source: str, weight: float) -> list[IntentSignal]:
        signals = []
        # Pattern 1: evaluate / assess / review
        if re.search(r"\b(?:evaluate|assess|benchmark(?:s|\s+results)?\s+(?:of|for)|performance\s+(?:analysis|evaluation)|security\s+audit|feasibility\s+(?:of|study))\b", lower):
            signals.append(
                IntentSignal(
                    intent_type=IntentType.EVALUATIVE,
                    confidence=min(0.88 * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="evaluative_assessment_pattern",
                    matched_cues=["evaluate / assess / benchmark / audit"],
                    rationale="Formal performance, security, or feasibility assessment",
                    source_field=source,
                )
            )
        elif re.search(r"\bhow\s+good\s+is\b", lower):
            signals.append(
                IntentSignal(
                    intent_type=IntentType.EVALUATIVE,
                    confidence=min(0.70 * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="evaluative_colloquial_pattern",
                    matched_cues=["how good is"],
                    rationale="Colloquial evaluation query",
                    source_field=source,
                )
            )
        return signals

    def _match_technical(self, text: str, lower: str, source: str, weight: float) -> list[IntentSignal]:
        signals = []
        # Pattern 1: explicit standard / RFC / PEP reference
        pep_match = re.search(r"\b(?:RFC|PEP|ECMA|ISO|IEEE)\s*\d+\b", text, re.IGNORECASE)
        if pep_match:
            signals.append(
                IntentSignal(
                    intent_type=IntentType.TECHNICAL,
                    confidence=min(0.90 * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="technical_spec_reference_pattern",
                    matched_cues=[pep_match.group(0)],
                    rationale="Direct reference to formal technical specification or RFC/PEP",
                    source_field=source,
                )
            )
        # Pattern 2: deep technical terminology
        tech_terms = [
            "subinterpreter", "ast", "bytecode", "garbage collection", "memory layout",
            "concurrency primitive", "virtual memory", "ipc", "abi", "api contract",
            "per-interpreter gil", "syscall", "compilation target"
        ]
        matched_terms = [t for t in tech_terms if t in lower]
        if matched_terms:
            signals.append(
                IntentSignal(
                    intent_type=IntentType.TECHNICAL,
                    confidence=min((0.75 + 0.05 * len(matched_terms)) * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="technical_domain_cues",
                    matched_cues=matched_terms,
                    rationale="Involves low-level runtime, compiler, or architectural primitives",
                    source_field=source,
                )
            )
        return signals

    def _match_decision_support(self, text: str, lower: str, source: str, weight: float) -> list[IntentSignal]:
        signals = []
        # Pattern 1: which should we choose / pick / adopt
        if re.search(r"\bwhich\s+(?:should|would)\s+(?:we|i|the\s+team)\s+(?:choose|pick|use|adopt|select)\b", lower):
            signals.append(
                IntentSignal(
                    intent_type=IntentType.DECISION_SUPPORT,
                    confidence=min(0.92 * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="decision_which_should_we_choose_pattern",
                    matched_cues=["which should we choose/pick"],
                    rationale="Direct decision selection inquiry",
                    source_field=source,
                )
            )
        # Pattern 2: recommendation for / should we use X or Y
        elif re.search(r"\b(?:recommendation\s+(?:for|on)|should\s+(?:we|i)\s+use\s+.+?\s+or\s+.+|decision\s+(?:matrix|brief)|build\s+vs\s+buy)\b", lower):
            signals.append(
                IntentSignal(
                    intent_type=IntentType.DECISION_SUPPORT,
                    confidence=min(0.88 * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="decision_recommendation_pattern",
                    matched_cues=["recommendation / decision matrix / build vs buy"],
                    rationale="Framed as actionable architectural recommendation or tradeoff decision",
                    source_field=source,
                )
            )
        return signals

    def _match_historical(self, text: str, lower: str, source: str, weight: float) -> list[IntentSignal]:
        signals = []
        # Pattern 1: history of / evolution of / timeline of
        if re.search(r"\b(?:history\s+(?:of|behind)|evolution\s+(?:of|across)|timeline\s+(?:of|for)|how\s+has\s+.+?\s+changed\s+(?:over\s+time|since|historically)|changelog\s+history)\b", lower):
            signals.append(
                IntentSignal(
                    intent_type=IntentType.HISTORICAL,
                    confidence=min(0.90 * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="historical_evolution_pattern",
                    matched_cues=["history of / evolution of / timeline"],
                    rationale="Chronological or historical inquiry tracking changes over time",
                    source_field=source,
                )
            )
        elif any(w in lower for w in ["chronology", "origin of", "past decisions"]):
            signals.append(
                IntentSignal(
                    intent_type=IntentType.HISTORICAL,
                    confidence=min(0.65 * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="historical_cue",
                    matched_cues=["chronology / origin"],
                    rationale="Mentions historical context",
                    source_field=source,
                )
            )
        return signals

    def _match_exploratory(self, text: str, lower: str, source: str, weight: float) -> list[IntentSignal]:
        signals = []
        # Pattern 1: explore / survey of / landscape of
        if re.search(r"\b(?:explore\s+|survey\s+(?:of|for)|landscape\s+(?:of|for)|state\s+of\s+the\s+art|what\s+options\s+exist\s+(?:for|to)|emerging\s+trends?\b)", lower):
            signals.append(
                IntentSignal(
                    intent_type=IntentType.EXPLORATORY,
                    confidence=min(0.85 * weight, self.MAX_DETERMINISTIC_CONFIDENCE),
                    rule_name="exploratory_landscape_pattern",
                    matched_cues=["explore / survey / landscape / state of the art"],
                    rationale="Open-ended landscape discovery or exploratory survey",
                    source_field=source,
                )
            )
        return signals

    # -------------------------------------------------------------------------
    # Metadata Scanner
    # -------------------------------------------------------------------------

    def _scan_metadata(self, metadata: dict[str, Any]) -> list[IntentSignal]:
        """Scan request metadata for explicit format or intent hints."""
        signals: list[IntentSignal] = []
        if not isinstance(metadata, dict):
            return signals

        # Check desired_output hint if present in metadata
        do_val = str(metadata.get("desired_output", "")).upper()
        if "COMPARISON" in do_val:
            signals.append(
                IntentSignal(
                    intent_type=IntentType.COMPARATIVE,
                    confidence=0.75,
                    rule_name="metadata_desired_output_hint",
                    matched_cues=[f"desired_output={do_val}"],
                    rationale="Metadata specifies COMPARISON output format",
                    source_field="metadata",
                )
            )
        elif "DECISION" in do_val:
            signals.append(
                IntentSignal(
                    intent_type=IntentType.DECISION_SUPPORT,
                    confidence=0.75,
                    rule_name="metadata_desired_output_hint",
                    matched_cues=[f"desired_output={do_val}"],
                    rationale="Metadata specifies DECISION_BRIEF output format",
                    source_field="metadata",
                )
            )
        elif "ROOT_CAUSE" in do_val:
            signals.append(
                IntentSignal(
                    intent_type=IntentType.DIAGNOSTIC,
                    confidence=0.75,
                    rule_name="metadata_desired_output_hint",
                    matched_cues=[f"desired_output={do_val}"],
                    rationale="Metadata specifies ROOT_CAUSE output format",
                    source_field="metadata",
                )
            )
        elif "IMPLEMENTATION" in do_val:
            signals.append(
                IntentSignal(
                    intent_type=IntentType.IMPLEMENTATION,
                    confidence=0.75,
                    rule_name="metadata_desired_output_hint",
                    matched_cues=[f"desired_output={do_val}"],
                    rationale="Metadata specifies IMPLEMENTATION_GUIDANCE output format",
                    source_field="metadata",
                )
            )

        return signals

    # -------------------------------------------------------------------------
    # Multi-Signal Aggregation (Noisy-OR)
    # -------------------------------------------------------------------------

    def _aggregate_signals(self, signals: list[IntentSignal]) -> dict[IntentType, float]:
        """
        Aggregate multiple signals for each IntentType using Noisy-OR combination:
        C_agg = 1 - prod(1 - c_i), capped at MAX_DETERMINISTIC_CONFIDENCE.
        """
        grouped: dict[IntentType, list[float]] = {}
        for s in signals:
            grouped.setdefault(s.intent_type, []).append(s.confidence)

        aggregate: dict[IntentType, float] = {}
        for it, confidences in grouped.items():
            if len(confidences) == 1:
                combined = confidences[0]
            else:
                # Noisy-OR formula: 1 - prod(1 - c)
                prod = 1.0
                for c in confidences:
                    prod *= (1.0 - c)
                combined = 1.0 - prod

            aggregate[it] = min(round(combined, 4), self.MAX_DETERMINISTIC_CONFIDENCE)

        return aggregate
