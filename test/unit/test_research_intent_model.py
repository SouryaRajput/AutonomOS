from __future__ import annotations

import unittest

from core.research.contracts.intent import (
    Ambiguity,
    ClarificationQuestion,
    EvidenceRequirement,
    IntentConfidence,
    ResearchIntent,
    TemporalScope,
    VersionScope,
)
from core.research.types import (
    DesiredOutput,
    FreshnessRequirement,
    IntentType,
    SourceType,
)


class TestResearchIntentModel(unittest.TestCase):
    """Unit tests for Phase 2 Step 2.1.1 Research Intent Model."""

    # -------------------------------------------------------------------------
    # Enum Membership Tests
    # -------------------------------------------------------------------------

    def test_intent_types_enum(self):
        """Verify all 10 required IntentType values exist and have string representations."""
        expected = [
            "DESCRIPTIVE",
            "COMPARATIVE",
            "EVALUATIVE",
            "DIAGNOSTIC",
            "IMPLEMENTATION",
            "EXPLORATORY",
            "VERIFICATION",
            "HISTORICAL",
            "TECHNICAL",
            "DECISION_SUPPORT",
        ]
        self.assertEqual(len(IntentType), 10)
        for name in expected:
            self.assertTrue(hasattr(IntentType, name))
            self.assertEqual(getattr(IntentType, name).value, name)

    def test_freshness_requirements_enum(self):
        """Verify all 7 required FreshnessRequirement values exist."""
        expected = [
            "STATIC",
            "CURRENT",
            "RECENT",
            "TIME_RANGE",
            "POINT_IN_TIME",
            "VERSION_SPECIFIC",
            "UNKNOWN",
        ]
        self.assertEqual(len(FreshnessRequirement), 7)
        for name in expected:
            self.assertTrue(hasattr(FreshnessRequirement, name))
            self.assertEqual(getattr(FreshnessRequirement, name).value, name)

    def test_desired_output_enum(self):
        """Verify all 9 required DesiredOutput values exist."""
        expected = [
            "FACTUAL_ANSWER",
            "COMPARISON",
            "RECOMMENDATION",
            "TECHNICAL_EXPLANATION",
            "IMPLEMENTATION_GUIDANCE",
            "TIMELINE",
            "ROOT_CAUSE",
            "SUMMARY",
            "DECISION_BRIEF",
        ]
        self.assertEqual(len(DesiredOutput), 9)
        for name in expected:
            self.assertTrue(hasattr(DesiredOutput, name))
            self.assertEqual(getattr(DesiredOutput, name).value, name)

    # -------------------------------------------------------------------------
    # IntentConfidence Tests
    # -------------------------------------------------------------------------

    def test_intent_confidence_defaults(self):
        """Verify default confidence ratings and overall average."""
        conf = IntentConfidence()
        self.assertEqual(conf.objective, 1.0)
        self.assertEqual(conf.entities, 1.0)
        self.assertEqual(conf.dimensions, 1.0)
        self.assertEqual(conf.constraints, 1.0)
        self.assertEqual(conf.assumptions, 1.0)
        self.assertEqual(conf.overall, 1.0)
        self.assertTrue(conf.is_confident(0.7))
        self.assertEqual(conf.validate(), [])

    def test_intent_confidence_dimension_calculations(self):
        """Verify dimension-specific scores and arithmetic mean overall computation."""
        conf = IntentConfidence(
            objective=0.9,
            entities=0.8,
            dimensions=0.7,
            constraints=0.6,
            assumptions=0.5,
        )
        expected_mean = round((0.9 + 0.8 + 0.7 + 0.6 + 0.5) / 5, 4)
        self.assertEqual(conf.overall, expected_mean)
        self.assertFalse(conf.is_confident(0.7))  # constraints (0.6) and assumptions (0.5) < 0.7
        self.assertTrue(conf.is_confident(0.5))

    def test_intent_confidence_override(self):
        """Verify overall_override takes precedence over arithmetic mean."""
        conf = IntentConfidence(
            objective=1.0,
            entities=1.0,
            dimensions=1.0,
            constraints=1.0,
            assumptions=1.0,
            overall_override=0.42,
        )
        self.assertEqual(conf.overall, 0.42)

    def test_intent_confidence_validation(self):
        """Verify validation flags out-of-range confidence scores."""
        conf = IntentConfidence(objective=1.5, assumptions=-0.1)
        errors = conf.validate()
        self.assertEqual(len(errors), 2)
        self.assertIn("objective", errors[0])
        self.assertIn("assumptions", errors[1])

    def test_intent_confidence_serialization_roundtrip(self):
        """Verify IntentConfidence to_dict and from_dict roundtrip."""
        conf = IntentConfidence(
            objective=0.95,
            entities=0.85,
            dimensions=0.75,
            constraints=0.65,
            assumptions=0.55,
            overall_override=0.77,
        )
        data = conf.to_dict()
        restored = IntentConfidence.from_dict(data)
        self.assertEqual(restored.objective, 0.95)
        self.assertEqual(restored.entities, 0.85)
        self.assertEqual(restored.dimensions, 0.75)
        self.assertEqual(restored.constraints, 0.65)
        self.assertEqual(restored.assumptions, 0.55)
        self.assertEqual(restored.overall, 0.77)

    # -------------------------------------------------------------------------
    # Auxiliary Scope & Requirement Models
    # -------------------------------------------------------------------------

    def test_ambiguity_model(self):
        """Verify Ambiguity creation and serialization."""
        amb = Ambiguity(
            ambiguity_id="amb-001",
            description="Unclear whether benchmark includes memory consumption",
            impact="HIGH",
            affected_fields=["research_dimensions"],
            suggested_interpretation="Include memory and latency",
            blocking=True,
        )
        data = amb.to_dict()
        self.assertEqual(data["ambiguity_id"], "amb-001")
        self.assertTrue(data["blocking"])

        restored = Ambiguity.from_dict(data)
        self.assertEqual(restored.ambiguity_id, "amb-001")
        self.assertEqual(restored.impact, "HIGH")
        self.assertEqual(restored.affected_fields, ["research_dimensions"])
        self.assertTrue(restored.blocking)

    def test_clarification_question_model(self):
        """Verify ClarificationQuestion creation and serialization."""
        cq = ClarificationQuestion(
            question_id="cq-001",
            question_text="Should memory footprint be evaluated alongside throughput?",
            target_ambiguity_id="amb-001",
            options=["Yes, evaluate memory and throughput", "No, throughput only"],
            default_assumption="Evaluate throughput only",
        )
        data = cq.to_dict()
        self.assertEqual(data["question_id"], "cq-001")
        self.assertEqual(len(data["options"]), 2)

        restored = ClarificationQuestion.from_dict(data)
        self.assertEqual(restored.question_id, "cq-001")
        self.assertEqual(restored.target_ambiguity_id, "amb-001")
        self.assertEqual(restored.default_assumption, "Evaluate throughput only")

    def test_temporal_scope_model(self):
        """Verify TemporalScope creation and serialization."""
        ts = TemporalScope(
            start_date="2024-01-01T00:00:00Z",
            end_date="2024-12-31T23:59:59Z",
            reference_point="2024",
            recency_days=365,
            description="Full calendar year 2024",
        )
        data = ts.to_dict()
        self.assertEqual(data["recency_days"], 365)

        restored = TemporalScope.from_dict(data)
        self.assertEqual(restored.start_date, "2024-01-01T00:00:00Z")
        self.assertEqual(restored.recency_days, 365)
        self.assertEqual(restored.description, "Full calendar year 2024")

    def test_version_scope_model(self):
        """Verify VersionScope creation and serialization."""
        vs = VersionScope(
            target_version="3.12",
            min_version="3.10",
            max_version="3.13",
            version_specifier=">=3.10,<3.13",
            ecosystem="python",
            description="Python 3.10 through 3.12 compatibility",
        )
        data = vs.to_dict()
        self.assertEqual(data["ecosystem"], "python")

        restored = VersionScope.from_dict(data)
        self.assertEqual(restored.target_version, "3.12")
        self.assertEqual(restored.version_specifier, ">=3.10,<3.13")
        self.assertEqual(restored.ecosystem, "python")

    def test_evidence_requirement_model(self):
        """Verify EvidenceRequirement reuses SourceType and serializes correctly."""
        req = EvidenceRequirement(
            requirement_id="ev-req-01",
            description="Official documentation confirmation required",
            source_types=[SourceType.OFFICIAL_DOCUMENTATION, SourceType.REPOSITORY],
            min_independent_sources=2,
            mandatory=True,
            verification_criteria="Must cite official API docs or GitHub README",
        )
        data = req.to_dict()
        self.assertIn("OFFICIAL_DOCUMENTATION", data["source_types"])
        self.assertIn("REPOSITORY", data["source_types"])
        self.assertEqual(data["min_independent_sources"], 2)

        restored = EvidenceRequirement.from_dict(data)
        self.assertEqual(restored.requirement_id, "ev-req-01")
        self.assertIn(SourceType.OFFICIAL_DOCUMENTATION, restored.source_types)
        self.assertIn(SourceType.REPOSITORY, restored.source_types)
        self.assertTrue(restored.mandatory)

    # -------------------------------------------------------------------------
    # ResearchIntent Core Model Tests
    # -------------------------------------------------------------------------

    def test_research_intent_minimal_instantiation(self):
        """Verify minimal valid ResearchIntent instantiation with defaults."""
        intent = ResearchIntent(objective="Audit project authentication architecture")
        self.assertTrue(intent.intent_id.startswith("intent-"))
        self.assertEqual(intent.objective, "Audit project authentication architecture")
        self.assertEqual(intent.intent_types, [IntentType.DESCRIPTIVE])
        self.assertEqual(intent.freshness_requirement, FreshnessRequirement.STATIC)
        self.assertEqual(intent.desired_output, DesiredOutput.FACTUAL_ANSWER)
        self.assertFalse(intent.clarification_required)
        self.assertEqual(intent.confidence.overall, 1.0)
        self.assertEqual(intent.validate(), [])

    def test_research_intent_full_instantiation(self):
        """Verify full ResearchIntent instantiation with all fields populated."""
        intent = ResearchIntent(
            intent_id="intent-test-123",
            objective="Compare SQLite and PostgreSQL for embedded event storage",
            intent_types=[IntentType.COMPARATIVE, IntentType.TECHNICAL],
            subjects=["Database Storage", "Event Persistence"],
            entities=["SQLite", "PostgreSQL"],
            comparison_targets=["SQLite", "PostgreSQL"],
            research_dimensions=["write throughput", "memory footprint", "concurrency"],
            explicit_constraints=["zero external server process", "local filesystem only"],
            inferred_constraints=["Python sqlite3 standard library support"],
            freshness_requirement=FreshnessRequirement.RECENT,
            temporal_scope=TemporalScope(recency_days=180, description="Past 6 months"),
            geographic_scope=None,
            version_scope=VersionScope(ecosystem="python", target_version="3.12"),
            desired_output=DesiredOutput.COMPARISON,
            evidence_requirements=[
                EvidenceRequirement(
                    requirement_id="ev-1",
                    description="Official SQLite documentation on WAL mode",
                    source_types=[SourceType.OFFICIAL_DOCUMENTATION],
                )
            ],
            assumptions=["Single-writer multi-reader concurrency profile"],
            ambiguities=[],
            clarification_required=False,
            clarification_questions=[],
            confidence=IntentConfidence(objective=0.95, entities=0.95, dimensions=0.9),
            source_request_id="req-root-001",
            understanding_trace=["Step 1: Extracted entities SQLite, PostgreSQL", "Step 2: Tagged as COMPARATIVE"],
        )

        self.assertEqual(intent.intent_id, "intent-test-123")
        self.assertTrue(intent.has_intent_type(IntentType.COMPARATIVE))
        self.assertTrue(intent.has_intent_type("TECHNICAL"))
        self.assertFalse(intent.has_intent_type(IntentType.HISTORICAL))
        self.assertEqual(intent.source_request_id, "req-root-001")
        self.assertEqual(len(intent.understanding_trace), 2)
        self.assertEqual(intent.validate(), [])

    def test_research_intent_validation_empty_objective(self):
        """Verify validation catches empty objective."""
        intent = ResearchIntent(objective="   ")
        errors = intent.validate()
        self.assertTrue(any("objective" in e for e in errors))

    def test_research_intent_validation_empty_intent_types(self):
        """Verify validation catches missing intent_types."""
        intent = ResearchIntent(objective="Valid objective", intent_types=[])
        errors = intent.validate()
        self.assertTrue(any("intent_types" in e for e in errors))

    def test_research_intent_validation_comparative_targets(self):
        """Verify comparative intent requires comparison targets or multiple entities."""
        intent = ResearchIntent(
            objective="Compare performance",
            intent_types=[IntentType.COMPARATIVE],
            entities=["OnlyOneEntity"],
            comparison_targets=[],
            subjects=[],
        )
        errors = intent.validate()
        self.assertTrue(any("COMPARATIVE" in e for e in errors))

    def test_research_intent_validation_clarification_consistency(self):
        """Verify validation flags clarification_required=True with no ambiguities or questions."""
        intent = ResearchIntent(
            objective="Valid objective",
            clarification_required=True,
            ambiguities=[],
            clarification_questions=[],
        )
        errors = intent.validate()
        self.assertTrue(any("clarification_required" in e for e in errors))

    def test_research_intent_add_ambiguity_helpers(self):
        """Verify add_ambiguity and add_clarification_question update state correctly."""
        intent = ResearchIntent(objective="Investigate async frameworks")
        self.assertFalse(intent.clarification_required)
        self.assertFalse(intent.has_blocking_ambiguity())

        # Non-blocking ambiguity does not mandate clarification
        amb1 = intent.add_ambiguity(
            description="Framework versions not specified",
            impact="LOW",
            blocking=False,
        )
        self.assertFalse(intent.clarification_required)
        self.assertFalse(intent.has_blocking_ambiguity())
        self.assertEqual(len(intent.ambiguities), 1)

        # Blocking ambiguity mandates clarification
        amb2 = intent.add_ambiguity(
            description="Target deployment platform unknown (cloud vs bare metal)",
            impact="HIGH",
            blocking=True,
        )
        self.assertTrue(intent.clarification_required)
        self.assertTrue(intent.has_blocking_ambiguity())
        self.assertEqual(len(intent.ambiguities), 2)

        # Add clarification question
        cq = intent.add_clarification_question(
            question_text="What is the target deployment environment?",
            target_ambiguity_id=amb2.ambiguity_id,
            options=["AWS Lambda", "Kubernetes / Docker", "Bare Metal"],
            default_assumption="Kubernetes / Docker",
        )
        self.assertEqual(len(intent.clarification_questions), 1)
        self.assertEqual(cq.target_ambiguity_id, amb2.ambiguity_id)

    def test_research_intent_bidirectional_serialization_roundtrip(self):
        """Verify exact bidirectional serialization and deserialization."""
        intent = ResearchIntent(
            intent_id="intent-roundtrip-01",
            objective="Evaluate Python 3.12 subinterpreters for isolated task workers",
            intent_types=[IntentType.EVALUATIVE, IntentType.TECHNICAL],
            subjects=["Python CPython Internals", "Subinterpreters"],
            entities=["Python 3.12", "PEP 684", "Per-Interpreter GIL"],
            comparison_targets=["Subinterpreters", "Multiprocessing"],
            research_dimensions=["memory overhead", "IPC latency", "C-extension compatibility"],
            explicit_constraints=["CPython 3.12+", "standard library only"],
            inferred_constraints=["POSIX or Windows OS"],
            freshness_requirement=FreshnessRequirement.POINT_IN_TIME,
            temporal_scope=TemporalScope(reference_point="Python 3.12 release"),
            geographic_scope=None,
            version_scope=VersionScope(ecosystem="python", target_version="3.12"),
            desired_output=DesiredOutput.TECHNICAL_EXPLANATION,
            evidence_requirements=[
                EvidenceRequirement(
                    requirement_id="req-pep",
                    description="PEP 684 specification details",
                    source_types=[SourceType.PRIMARY_SOURCE, SourceType.OFFICIAL_DOCUMENTATION],
                    min_independent_sources=1,
                    mandatory=True,
                )
            ],
            assumptions=["Target libraries are pure Python or multi-phase init"],
            ambiguities=[
                Ambiguity(
                    ambiguity_id="amb-c-ext",
                    description="Are proprietary C-extensions involved?",
                    impact="MEDIUM",
                    blocking=False,
                )
            ],
            clarification_required=False,
            clarification_questions=[],
            confidence=IntentConfidence(
                objective=0.98,
                entities=0.92,
                dimensions=0.88,
                constraints=0.95,
                assumptions=0.85,
            ),
            source_request_id="req-sub-001",
            understanding_trace=["Parsed objective", "Extracted PEP 684", "Identified EVALUATIVE intent"],
            correlation_id="corr-intent-001",
            metadata={"priority": "high", "origin": "architecture_review"},
        )

        data = intent.to_dict()
        self.assertIsInstance(data, dict)
        self.assertEqual(data["intent_id"], "intent-roundtrip-01")
        self.assertEqual(data["desired_output"], "TECHNICAL_EXPLANATION")
        self.assertEqual(data["freshness_requirement"], "POINT_IN_TIME")

        restored = ResearchIntent.from_dict(data)
        self.assertEqual(restored.intent_id, intent.intent_id)
        self.assertEqual(restored.objective, intent.objective)
        self.assertEqual(restored.intent_types, intent.intent_types)
        self.assertEqual(restored.subjects, intent.subjects)
        self.assertEqual(restored.entities, intent.entities)
        self.assertEqual(restored.comparison_targets, intent.comparison_targets)
        self.assertEqual(restored.research_dimensions, intent.research_dimensions)
        self.assertEqual(restored.explicit_constraints, intent.explicit_constraints)
        self.assertEqual(restored.inferred_constraints, intent.inferred_constraints)
        self.assertEqual(restored.freshness_requirement, intent.freshness_requirement)
        self.assertIsNotNone(restored.temporal_scope)
        self.assertEqual(restored.temporal_scope.reference_point, "Python 3.12 release")
        self.assertIsNotNone(restored.version_scope)
        self.assertEqual(restored.version_scope.target_version, "3.12")
        self.assertEqual(restored.desired_output, intent.desired_output)
        self.assertEqual(len(restored.evidence_requirements), 1)
        self.assertEqual(restored.evidence_requirements[0].requirement_id, "req-pep")
        self.assertEqual(len(restored.ambiguities), 1)
        self.assertEqual(restored.confidence.objective, 0.98)
        self.assertEqual(restored.source_request_id, "req-sub-001")
        self.assertEqual(restored.correlation_id, "corr-intent-001")
        self.assertEqual(restored.metadata["priority"], "high")

        # Second roundtrip to verify identical dictionary export
        data_second = restored.to_dict()
        self.assertEqual(data, data_second)

    def test_research_intent_resilience_to_malformed_dict(self):
        """Verify from_dict gracefully handles unknown enum strings without crashing."""
        malformed = {
            "objective": "Test malformed inputs",
            "intent_types": ["NON_EXISTENT_INTENT", "DIAGNOSTIC"],
            "freshness_requirement": "INVALID_FRESHNESS",
            "desired_output": "INVALID_OUTPUT",
            "temporal_scope": None,
            "version_scope": None,
            "evidence_requirements": [
                {
                    "requirement_id": "r1",
                    "description": "desc",
                    "source_types": ["UNKNOWN_SOURCE_TYPE"],
                }
            ],
            "confidence": {"objective": 0.8},
        }

        intent = ResearchIntent.from_dict(malformed)
        self.assertEqual(intent.intent_types, [IntentType.DIAGNOSTIC])
        self.assertEqual(intent.freshness_requirement, FreshnessRequirement.STATIC)
        self.assertEqual(intent.desired_output, DesiredOutput.FACTUAL_ANSWER)
        self.assertEqual(intent.evidence_requirements[0].source_types, [SourceType.OTHER])
        self.assertEqual(intent.confidence.objective, 0.8)
        self.assertEqual(intent.confidence.entities, 1.0)  # default

    def test_research_intent_default_list_isolation(self):
        """Verify default list factories do not leak mutations between instances."""
        intent1 = ResearchIntent(objective="Intent 1")
        intent2 = ResearchIntent(objective="Intent 2")

        intent1.subjects.append("Subject 1")
        intent1.intent_types.append(IntentType.DIAGNOSTIC)
        intent1.entities.append("Entity 1")

        self.assertEqual(intent2.subjects, [])
        self.assertEqual(intent2.intent_types, [IntentType.DESCRIPTIVE])
        self.assertEqual(intent2.entities, [])


if __name__ == "__main__":
    unittest.main()
