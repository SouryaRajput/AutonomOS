from __future__ import annotations

import json
import unittest

from core.inference.gateway import InferenceGateway
from core.inference.model import InferenceMessage, InferenceRequest, InferenceResponse, ModelRequirement
from core.inference.provider import MockProvider
from core.inference.registry import ModelRegistry, ProviderRegistry
from core.inference.secrets import EnvSecretStore
from core.research.contracts.intent import ResearchIntent
from core.research.contracts.request import ResearchRequest, ResearchScope
from core.research.planning.extractor import (
    DeterministicRequestExtractor,
    ExtractedRequestElements,
)
from core.research.planning.intent_classifier import (
    DeterministicIntentClassifier,
    IntentClassificationResult,
    IntentSignal,
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
)


class TestLLMUnderstandingEngine(unittest.TestCase):
    """
    Unit test suite verifying the LLM Understanding Engine, proposal validation,
    guardrails, and deterministic synthesis into ResearchIntent.
    """

    def setUp(self):
        self.validator = ProposalValidator()

    def _create_mock_engine(self, custom_response: str) -> LLMUnderstandingEngine:
        """Create an LLMUnderstandingEngine backed by a deterministic MockProvider."""
        provider = MockProvider(
            provider_id="mock-analyst",
            custom_response=custom_response,
        )
        return LLMUnderstandingEngine(provider=provider, validator=self.validator)

    def test_successful_structured_understanding_proposal(self):
        """Verify valid structured JSON proposal passes validation and converts to trusted ResearchIntent."""
        mock_payload = {
            "objective": "Compare PostgreSQL with MySQL for distributed time-series storage.",
            "intent_types": ["COMPARATIVE", "TECHNICAL"],
            "subjects": ["distributed time-series storage"],
            "entities": ["PostgreSQL", "MySQL"],
            "comparison_targets": ["PostgreSQL", "MySQL"],
            "research_dimensions": ["performance", "scalability"],
            "explicit_constraints": ["Must support horizontal scaling"],
            "inferred_constraints": ["High availability required for distributed clusters"],
            "freshness_requirement": "RECENT",
            "temporal_scope": {
                "start_date": "2023-01-01",
                "end_date": "2024-12-31",
                "description": "2023 through 2024",
            },
            "geographic_scope": "GLOBAL",
            "version_scope": {
                "target_version": "16",
                "ecosystem": "PostgreSQL",
                "description": "PostgreSQL 16",
            },
            "desired_output": "COMPARISON",
            "evidence_requirements": [
                {
                    "description": "Official benchmark documentation",
                    "source_types": ["OFFICIAL_DOCUMENTATION"],
                    "min_independent_sources": 2,
                }
            ],
            "assumptions": ["Assuming commodity cloud hardware"],
            "ambiguities": [],
            "clarification_required": False,
            "clarification_questions": [],
            "confidence": {
                "objective": 0.95,
                "entities": 0.90,
                "dimensions": 0.85,
                "constraints": 0.90,
                "assumptions": 0.80,
            },
        }

        engine = self._create_mock_engine(json.dumps(mock_payload))
        req = ResearchRequest(
            request_id="req-101",
            project_id="proj-1",
            task_id="task-1",
            objective=mock_payload["objective"],
        )

        proposal, result = engine.understand(req)

        self.assertTrue(result.is_valid)
        self.assertEqual(len(result.errors), 0)
        self.assertIsNotNone(result.validated_intent)

        intent = result.validated_intent
        self.assertEqual(intent.objective, mock_payload["objective"])
        self.assertIn(IntentType.COMPARATIVE, intent.intent_types)
        self.assertIn(IntentType.TECHNICAL, intent.intent_types)
        self.assertIn("PostgreSQL", intent.entities)
        self.assertIn("MySQL", intent.entities)
        self.assertIn("Must support horizontal scaling", intent.explicit_constraints)
        self.assertIn("High availability required for distributed clusters", intent.inferred_constraints)
        self.assertEqual(intent.freshness_requirement, FreshnessRequirement.RECENT)
        self.assertEqual(intent.desired_output, DesiredOutput.COMPARISON)
        self.assertEqual(intent.confidence.objective, 0.95)

    def test_inferred_constraints_remain_strictly_inferred(self):
        """Verify that inferred constraints are never promoted to explicit constraints."""
        mock_payload = {
            "objective": "Build real-time notification service.",
            "intent_types": ["IMPLEMENTATION"],
            "explicit_constraints": ["Must use WebSockets"],
            "inferred_constraints": ["Low latency connection handling is necessary"],
        }
        engine = self._create_mock_engine(json.dumps(mock_payload))
        req = ResearchRequest(
            request_id="req-102",
            project_id="p1",
            task_id="t1",
            objective="Build real-time notification service.",
            constraints=["Must use WebSockets"],
        )

        proposal, result = engine.understand(req)
        self.assertTrue(result.is_valid)
        intent = result.validated_intent

        self.assertIn("Must use WebSockets", intent.explicit_constraints)
        self.assertNotIn("Low latency connection handling is necessary", intent.explicit_constraints)
        self.assertIn("Low latency connection handling is necessary", intent.inferred_constraints)

    def test_markdown_code_fence_stripping(self):
        """Verify JSON enclosed within markdown ```json ... ``` code fences is parsed successfully."""
        json_content = json.dumps({
            "objective": "Research ASGI servers.",
            "intent_types": ["EXPLORATORY"],
            "entities": ["Uvicorn", "Hypercorn"],
        })
        fenced_content = f"```json\n{json_content}\n```"

        engine = self._create_mock_engine(fenced_content)
        req = ResearchRequest(request_id="req-103", project_id="p1", task_id="t1", objective="Research ASGI servers.")

        proposal, result = engine.understand(req)
        self.assertTrue(result.is_valid)
        self.assertIn("Uvicorn", result.validated_intent.entities)

    def test_malformed_json_fails_validation_cleanly(self):
        """Verify non-JSON response produces validation error without crash."""
        engine = self._create_mock_engine("This is conversational text without JSON { broken: ")
        req = ResearchRequest(request_id="req-104", project_id="p1", task_id="t1", objective="Test objective")

        proposal, result = engine.understand(req)
        self.assertFalse(result.is_valid)
        self.assertTrue(len(result.errors) > 0)
        self.assertIsNone(result.validated_intent)

    def test_missing_required_fields_validation(self):
        """Verify proposal with missing objective or empty intent_types is rejected."""
        # Case A: empty objective
        payload_a = {"objective": "", "intent_types": ["DESCRIPTIVE"]}
        prop_a = ResearchIntentProposal.from_dict(payload_a)
        res_a = self.validator.validate(prop_a)
        self.assertFalse(res_a.is_valid)
        self.assertTrue(any("objective" in err.lower() for err in res_a.errors))

        # Case B: empty intent_types
        payload_b = {"objective": "Valid objective", "intent_types": []}
        prop_b = ResearchIntentProposal.from_dict(payload_b)
        res_b = self.validator.validate(prop_b)
        self.assertFalse(res_b.is_valid)
        self.assertTrue(any("intent" in err.lower() for err in res_b.errors))

    def test_invalid_enum_validation(self):
        """Verify invalid intent type, freshness requirement, and desired output strings fail validation."""
        payload = {
            "objective": "Valid objective",
            "intent_types": ["NOT_A_REAL_INTENT"],
            "freshness_requirement": "ULTRA_FRESH",
            "desired_output": "MAGIC_SLIDES",
        }
        prop = ResearchIntentProposal.from_dict(payload)
        res = self.validator.validate(prop)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("intent_type" in err for err in res.errors))
        self.assertTrue(any("freshness_requirement" in err for err in res.errors))
        self.assertTrue(any("desired_output" in err for err in res.errors))

    def test_confidence_bounds_validation(self):
        """Verify confidence scores outside [0.0, 1.0] are rejected."""
        payload = {
            "objective": "Valid objective",
            "intent_types": ["DESCRIPTIVE"],
            "confidence": {"objective": 1.5, "entities": -0.2},
        }
        prop = ResearchIntentProposal.from_dict(payload)
        res = self.validator.validate(prop)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("1.5" in err for err in res.errors))
        self.assertTrue(any("-0.2" in err for err in res.errors))

    def test_clarification_consistency_reconciliation(self):
        """Verify clarification_required is reconciled to True if questions or blocking ambiguities exist."""
        payload = {
            "objective": "Compare caching options.",
            "intent_types": ["COMPARATIVE"],
            "clarification_required": False,  # Contradicts presence of clarification questions
            "clarification_questions": [
                {
                    "question_text": "Which deployment environment will be used?",
                    "options": ["AWS", "On-premise"],
                }
            ],
            "ambiguities": [
                {
                    "description": "Missing throughput SLA",
                    "impact": "Cannot size cache",
                    "affected_fields": ["constraints"],
                    "blocking": True,
                }
            ],
        }
        prop = ResearchIntentProposal.from_dict(payload)
        res = self.validator.validate(prop)

        self.assertTrue(res.is_valid)
        # Should reconcile to True
        self.assertTrue(res.validated_intent.clarification_required)
        self.assertTrue(len(res.warnings) > 0)
        self.assertEqual(len(res.validated_intent.clarification_questions), 1)
        self.assertEqual(len(res.validated_intent.ambiguities), 1)

    def test_provider_failure_raises_understanding_error(self):
        """Verify provider execution errors are raised as UnderstandingError."""
        failing_provider = MockProvider(provider_id="failing-mock", should_fail=True)
        engine = LLMUnderstandingEngine(provider=failing_provider)
        req = ResearchRequest(request_id="req-105", project_id="p1", task_id="t1", objective="Test error handling")

        with self.assertRaises(UnderstandingError):
            engine.understand(req)

    def test_fusion_with_deterministic_signals_and_extracted_elements(self):
        """Verify deterministic intent signals and extracted elements enrich and ground the proposal."""
        mock_payload = {
            "objective": "Compare FastAPI and Flask.",
            "intent_types": ["COMPARATIVE"],
            "entities": ["FastAPI"],  # Model missed Flask in entities
        }
        engine = self._create_mock_engine(json.dumps(mock_payload))

        req = ResearchRequest(
            request_id="req-106",
            project_id="p1",
            task_id="t1",
            objective="Compare FastAPI and Flask under GDPR.",
        )

        # Extracted elements ground truth
        extracted = ExtractedRequestElements(
            source_request_id=req.request_id,
            entities=["FastAPI", "Flask"],
            comparison_targets=["FastAPI", "Flask"],
            geographic_scope="EU (GDPR)",
            explicit_constraints=["Must comply with GDPR"],
        )

        # Classification signal
        signals = IntentClassificationResult(
            signals=[
                IntentSignal(
                    intent_type=IntentType.COMPARATIVE,
                    confidence=0.90,
                    rule_name="compare_syntax",
                    source_field="objective",
                )
            ],
            primary_intent=IntentType.COMPARATIVE,
        )

        proposal, result = engine.understand(req, deterministic_signals=signals, extracted_elements=extracted)
        self.assertTrue(result.is_valid)
        intent = result.validated_intent

        # Ground truth preserved and merged
        self.assertIn("FastAPI", intent.entities)
        self.assertIn("Flask", intent.entities)
        self.assertIn("FastAPI", intent.comparison_targets)
        self.assertIn("Flask", intent.comparison_targets)
        self.assertEqual(intent.geographic_scope, "EU (GDPR)")
        self.assertIn("Must comply with GDPR", intent.explicit_constraints)

    def test_system_prompt_analyst_guardrails(self):
        """Assert system prompt explicitly instructs against answering, researching, tool use, or inventing facts."""
        prompt = LLMUnderstandingEngine.SYSTEM_PROMPT
        self.assertIn("ANALYST", prompt)
        self.assertIn("Do NOT invent facts", prompt)
        self.assertIn("Do NOT answer the research question", prompt)
        self.assertIn("Do NOT perform research", prompt)
        self.assertIn("Do NOT generate evidence", prompt)
        self.assertIn("Do NOT select crawlers", prompt)
        self.assertIn("Do NOT generate search queries", prompt)
        self.assertIn("Do NOT execute tools", prompt)
        self.assertIn("Do NOT modify state", prompt)
        self.assertIn("inferred_constraints", prompt)

    def test_serialization_roundtrip(self):
        """Verify bidirectional dict serialization for ResearchIntentProposal and ProposalValidationResult."""
        payload = {
            "proposal_id": "prop-test",
            "source_request_id": "req-99",
            "objective": "Test serialization",
            "intent_types": ["DESCRIPTIVE"],
            "subjects": ["serialization"],
            "entities": ["JSON"],
            "confidence": {"objective": 0.95},
        }
        prop = ResearchIntentProposal.from_dict(payload)
        prop_dict = prop.to_dict()
        restored = ResearchIntentProposal.from_dict(prop_dict)

        self.assertEqual(restored.proposal_id, prop.proposal_id)
        self.assertEqual(restored.objective, prop.objective)
        self.assertEqual(restored.intent_types, prop.intent_types)
        self.assertEqual(restored.confidence["objective"], 0.95)

        val_res = self.validator.validate(prop)
        val_dict = val_res.to_dict()
        restored_val = ProposalValidationResult.from_dict(val_dict)

        self.assertTrue(restored_val.is_valid)
        self.assertIsNotNone(restored_val.validated_intent)
        self.assertEqual(restored_val.validated_intent.objective, "Test serialization")


if __name__ == "__main__":
    unittest.main()
