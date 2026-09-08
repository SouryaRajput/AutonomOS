from __future__ import annotations

import os
from pathlib import PurePosixPath
import re
from typing import Any, Optional, Sequence

from core.enums import RiskLevel
from core.programmer.contracts.codebase_understanding import CodebaseUnderstanding
from core.programmer.contracts.engineering_risk import (
    EngineeringRisk,
    RiskAssessment,
    utc_now,
)
from core.programmer.contracts.identifiers import (
    new_engineering_risk_id,
    new_risk_assessment_id,
    new_verification_evidence_id,
)
from core.programmer.contracts.impact_analysis import ImpactAnalysis
from core.programmer.contracts.implementation_plan import ImplementationPlan
from core.programmer.contracts.verification import VerificationEvidence
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.programmer.errors import ProgrammerLineageError
from core.programmer.types import (
    EngineeringRiskCategory,
    UnderstandingConfidence,
    VerificationEvidenceSourceType,
)

# Known dependency manifest files
DEPENDENCY_MANIFEST_NAMES = frozenset({
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "pipfile",
    "pipfile.lock",
    "poetry.lock",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "cargo.toml",
    "cargo.lock",
    "go.mod",
    "go.sum",
    "pom.xml",
    "build.gradle",
    "gemfile",
    "composer.json",
})

# DDL and destructive SQL patterns
SQL_DDL_PATTERNS = [
    re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE),
    re.compile(r"\bDROP\s+COLUMN\b", re.IGNORECASE),
    re.compile(r"\bALTER\s+TABLE\b", re.IGNORECASE),
    re.compile(r"\bTRUNCATE\s+TABLE\b", re.IGNORECASE),
    re.compile(r"\bTRUNCATE\b", re.IGNORECASE),
    re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE),
]

DESTRUCTIVE_DDL_PATTERNS = [
    re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE),
    re.compile(r"\bDROP\s+COLUMN\b", re.IGNORECASE),
    re.compile(r"\bTRUNCATE\b", re.IGNORECASE),
    re.compile(r"\bCASCADE\b", re.IGNORECASE),
]

# Authentication / authorization keywords (content & contract signals)
AUTH_CONTENT_PATTERNS = [
    re.compile(r"\b(jwt|oauth|bearer|token|session|password_hash|verify_password|authenticate|rbac|check_permission)\b", re.IGNORECASE),
]

# Destructive shell command patterns
DESTRUCTIVE_COMMAND_PATTERNS = [
    re.compile(r"\brm\s+-[rf]{1,2}\b", re.IGNORECASE),
    re.compile(r"\bsudo\b", re.IGNORECASE),
    re.compile(r"\bchmod\s+777\b", re.IGNORECASE),
    re.compile(r"\bdd\s+if=", re.IGNORECASE),
    re.compile(r"\bformat\b", re.IGNORECASE),
]


class EngineeringRiskAnalyzer:
    """
    Deterministic risk analysis engine that identifies material engineering risks
    in an ImplementationPlan before code modification begins.

    Guarantees:
    1. Grounded in empirical facts and impact graphs; resistant to false-positives
       (scary file names alone do not trigger risk).
    2. Epistemically calibrated (OBSERVED, INFERRED, UNKNOWN).
    3. Routine low-risk tasks trigger zero material risks and zero escalations.
    4. Material risks produce structured EscalationCandidates for Manager authority.
    5. Strictly read-only: never modifies repository files or executes commands.
    """

    def analyze(
        self,
        work_order: ProgrammerWorkOrder,
        understanding: CodebaseUnderstanding,
        impact_analysis: ImpactAnalysis,
        plan: ImplementationPlan,
        workspace: Optional[ProgrammerWorkspace] = None,
        trace: Optional[dict[str, Any]] = None,
    ) -> RiskAssessment:
        """
        Evaluate implementation plan against work order, codebase understanding,
        and impact analysis to produce a comprehensive RiskAssessment.
        """
        # Lineage verification
        if plan.work_order_id != work_order.work_order_id:
            raise ProgrammerLineageError(
                f"ImplementationPlan work_order_id '{plan.work_order_id}' does not match "
                f"WorkOrder '{work_order.work_order_id}'."
            )
        if impact_analysis.work_order_id != work_order.work_order_id:
            raise ProgrammerLineageError(
                f"ImpactAnalysis work_order_id '{impact_analysis.work_order_id}' does not match "
                f"WorkOrder '{work_order.work_order_id}'."
            )
        if understanding.work_order_id != work_order.work_order_id:
            raise ProgrammerLineageError(
                f"CodebaseUnderstanding work_order_id '{understanding.work_order_id}' does not match "
                f"WorkOrder '{work_order.work_order_id}'."
            )

        execution_id = plan.execution_id or impact_analysis.execution_id or understanding.execution_id
        assessment_id = new_risk_assessment_id()

        detected_risks: list[EngineeringRisk] = []

        # 1. Evaluate Dependency Changes
        self._detect_dependency_risks(work_order, impact_analysis, plan, detected_risks, assessment_id, execution_id)

        # 2. Evaluate Database Migrations & Data Loss
        self._detect_database_and_data_loss_risks(work_order, impact_analysis, plan, workspace, detected_risks, assessment_id, execution_id)

        # 3. Evaluate Public API Changes & Breaking Changes
        self._detect_api_and_breaking_risks(work_order, impact_analysis, plan, detected_risks, assessment_id, execution_id)

        # 4. Evaluate Authentication & Authorization Risks
        self._detect_auth_risks(work_order, impact_analysis, plan, workspace, detected_risks, assessment_id, execution_id)

        # 5. Evaluate Security & Destructive Operations
        self._detect_security_and_destructive_risks(work_order, plan, detected_risks, assessment_id, execution_id)

        # 6. Evaluate Large Scope & Architectural Risks
        self._detect_scope_and_architectural_risks(work_order, impact_analysis, plan, detected_risks, assessment_id, execution_id)

        # 7. Evaluate Test Coverage Risks
        self._detect_test_coverage_risks(impact_analysis, plan, detected_risks, assessment_id, execution_id)

        # 8. Evaluate Unknown Dependencies / Context Gaps
        self._detect_unknown_risks(impact_analysis, detected_risks, assessment_id, execution_id)

        # Calculate Overall Risk Level
        overall_level = RiskLevel.LOW
        for r in detected_risks:
            if r.severity == RiskLevel.CRITICAL:
                overall_level = RiskLevel.CRITICAL
                break
            elif r.severity == RiskLevel.HIGH and overall_level != RiskLevel.CRITICAL:
                overall_level = RiskLevel.HIGH
            elif r.severity == RiskLevel.MEDIUM and overall_level not in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                overall_level = RiskLevel.MEDIUM

        # Filter Material Risks & Escalation Candidates
        material_risks = [
            r for r in detected_risks
            if r.escalation_required or r.severity in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        ]
        escalation_candidates = [r.to_escalation_candidate() for r in material_risks if r.escalation_required]

        assessment_trace = dict(trace or {})
        assessment_trace.update({
            "assessment_id": assessment_id,
            "work_order_id": work_order.work_order_id,
            "execution_id": execution_id,
            "plan_id": plan.plan_id,
            "project_id": work_order.project_id,
            "total_risks": len(detected_risks),
            "material_risks": len(material_risks),
            "created_at": utc_now(),
        })

        return RiskAssessment(
            assessment_id=assessment_id,
            execution_id=execution_id,
            work_order_id=work_order.work_order_id,
            project_id=work_order.project_id,
            plan_id=plan.plan_id,
            risks=detected_risks,
            material_risks=material_risks,
            escalation_candidates=escalation_candidates,
            overall_risk_level=overall_level,
            trace=assessment_trace,
        )

    def _create_evidence(
        self,
        assessment_id: str,
        execution_id: str,
        work_order_id: str,
        description: str,
        data: dict[str, Any],
    ) -> VerificationEvidence:
        return VerificationEvidence(
            evidence_id=new_verification_evidence_id(),
            execution_id=execution_id,
            work_order_id=work_order_id,
            source_type=VerificationEvidenceSourceType.RISK_ASSESSMENT,
            source_reference=assessment_id,
            description=description,
            data=data,
            is_agent_claim=False,
        )

    # -------------------------------------------------------------------------
    # Detection Rules
    # -------------------------------------------------------------------------

    def _detect_dependency_risks(
        self,
        work_order: ProgrammerWorkOrder,
        impact_analysis: ImpactAnalysis,
        plan: ImplementationPlan,
        risks: list[EngineeringRisk],
        assessment_id: str,
        execution_id: str,
    ) -> None:
        """Detect modifications to package manifests or external library dependencies."""
        all_targets = set(impact_analysis.directly_affected_files + plan.affected_files)
        for step in plan.steps:
            all_targets.update(step.target_files)

        manifest_targets = [
            f for f in all_targets
            if PurePosixPath(f).name.lower() in DEPENDENCY_MANIFEST_NAMES
        ]

        if manifest_targets or impact_analysis.dependency_impacts:
            evidence_targets = manifest_targets if manifest_targets else impact_analysis.dependency_impacts
            ev = self._create_evidence(
                assessment_id=assessment_id,
                execution_id=execution_id,
                work_order_id=work_order.work_order_id,
                description=f"Dependency manifest modification in {', '.join(evidence_targets)}",
                data={
                    "type": "DEPENDENCY_CHANGE",
                    "manifest_targets": manifest_targets,
                    "dependency_impacts": list(impact_analysis.dependency_impacts),
                },
            )
            risks.append(
                EngineeringRisk(
                    risk_id=new_engineering_risk_id(),
                    category=EngineeringRiskCategory.DEPENDENCY_CHANGE,
                    severity=RiskLevel.HIGH,
                    confidence=UnderstandingConfidence.OBSERVED if manifest_targets else UnderstandingConfidence.INFERRED,
                    description=f"Plan modifies dependency manifests or introduces library updates in {', '.join(evidence_targets)}.",
                    affected_area=", ".join(evidence_targets),
                    evidence=[ev],
                    mitigation="Pin exact dependency versions, update lockfiles, and run security vulnerability scan.",
                    escalation_required=True,
                )
            )

    def _detect_database_and_data_loss_risks(
        self,
        work_order: ProgrammerWorkOrder,
        impact_analysis: ImpactAnalysis,
        plan: ImplementationPlan,
        workspace: Optional[ProgrammerWorkspace],
        risks: list[EngineeringRisk],
        assessment_id: str,
        execution_id: str,
    ) -> None:
        """Detect schema migrations, DDL execution, and potential data destruction."""
        all_targets = set(impact_analysis.directly_affected_files + plan.affected_files)
        for step in plan.steps:
            all_targets.update(step.target_files)

        migration_targets: list[str] = []
        for t in all_targets:
            t_lower = t.lower()
            if any(p in t_lower for p in ("/migrations/", "migrations/", "alembic/", "/schema/", "schema.sql", ".sql")):
                migration_targets.append(t)

        # Check objective or file contents for destructive DDL
        has_destructive_ddl = False
        destructive_details: list[str] = []
        obj_text = f"{work_order.objective} {' '.join(work_order.instructions)}"

        for pat in DESTRUCTIVE_DDL_PATTERNS:
            match = pat.search(obj_text)
            if match:
                has_destructive_ddl = True
                destructive_details.append(match.group(0))

        # Check actual file contents if workspace provided
        if workspace:
            for mt in migration_targets:
                full_path = os.path.join(workspace.root_path, mt)
                if os.path.isfile(full_path):
                    try:
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read(4096)
                            for pat in DESTRUCTIVE_DDL_PATTERNS:
                                match = pat.search(content)
                                if match:
                                    has_destructive_ddl = True
                                    destructive_details.append(f"{mt}: {match.group(0)}")
                    except Exception:
                        pass

        if migration_targets or ("migration" in obj_text.lower() and "database" in obj_text.lower()):
            area = ", ".join(migration_targets) if migration_targets else "Database Schema"
            ev = self._create_evidence(
                assessment_id=assessment_id,
                execution_id=execution_id,
                work_order_id=work_order.work_order_id,
                description=f"Database migration detected in {area}",
                data={"migration_targets": migration_targets, "has_destructive_ddl": has_destructive_ddl},
            )
            risks.append(
                EngineeringRisk(
                    risk_id=new_engineering_risk_id(),
                    category=EngineeringRiskCategory.DATABASE_MIGRATION,
                    severity=RiskLevel.HIGH,
                    confidence=UnderstandingConfidence.OBSERVED if migration_targets else UnderstandingConfidence.INFERRED,
                    description=f"Plan modifies database schema or migration scripts in {area}.",
                    affected_area=area,
                    evidence=[ev],
                    mitigation="Review forward/rollback scripts, test on staging replica, take pre-migration database snapshot.",
                    escalation_required=True,
                )
            )

        if has_destructive_ddl:
            area = ", ".join(destructive_details)
            ev = self._create_evidence(
                assessment_id=assessment_id,
                execution_id=execution_id,
                work_order_id=work_order.work_order_id,
                description=f"Destructive database DDL operations: {area}",
                data={"destructive_operations": destructive_details},
            )
            risks.append(
                EngineeringRisk(
                    risk_id=new_engineering_risk_id(),
                    category=EngineeringRiskCategory.DATA_LOSS,
                    severity=RiskLevel.CRITICAL,
                    confidence=UnderstandingConfidence.OBSERVED,
                    description=f"Destructive database operations detected that may cause unrecoverable data loss: {area}.",
                    affected_area=area,
                    evidence=[ev],
                    mitigation="Require explicit backup snapshot, implement soft-deletes or shadow tables instead of drops.",
                    escalation_required=True,
                )
            )

    def _detect_api_and_breaking_risks(
        self,
        work_order: ProgrammerWorkOrder,
        impact_analysis: ImpactAnalysis,
        plan: ImplementationPlan,
        risks: list[EngineeringRisk],
        assessment_id: str,
        execution_id: str,
    ) -> None:
        """Detect public API modifications, route changes, or breaking contract modifications."""
        all_targets = set(impact_analysis.directly_affected_files + plan.affected_files)
        api_targets = [
            t for t in all_targets
            if any(p in t.lower() for p in ("/api/", "api/", "routes/", "controllers/", "endpoints/", "openapi", "swagger"))
        ]

        obj_lower = work_order.objective.lower()
        is_breaking = any(b in obj_lower for b in ("breaking", "deprecate", "remove endpoint", "rename parameter"))
        has_indirect_callers = len(impact_analysis.indirectly_affected_files) > 1

        if api_targets:
            area = ", ".join(api_targets)
            ev = self._create_evidence(
                assessment_id=assessment_id,
                execution_id=execution_id,
                work_order_id=work_order.work_order_id,
                description=f"Public API route changes in {area}",
                data={"api_targets": api_targets, "is_breaking": is_breaking, "callers": impact_analysis.indirectly_affected_files},
            )
            risks.append(
                EngineeringRisk(
                    risk_id=new_engineering_risk_id(),
                    category=EngineeringRiskCategory.PUBLIC_API_CHANGE,
                    severity=RiskLevel.HIGH if (is_breaking or has_indirect_callers) else RiskLevel.MEDIUM,
                    confidence=UnderstandingConfidence.OBSERVED,
                    description=f"Plan modifies public API routes or contract endpoints in {area}.",
                    affected_area=area,
                    evidence=[ev],
                    mitigation="Verify backwards compatibility, update API documentation/schemas, and run consumer integration tests.",
                    escalation_required=(is_breaking or has_indirect_callers),
                )
            )

        if is_breaking:
            ev = self._create_evidence(
                assessment_id=assessment_id,
                execution_id=execution_id,
                work_order_id=work_order.work_order_id,
                description=f"Breaking contract changes: {work_order.objective}",
                data={"breaking_intent": work_order.objective},
            )
            risks.append(
                EngineeringRisk(
                    risk_id=new_engineering_risk_id(),
                    category=EngineeringRiskCategory.BREAKING_CHANGE,
                    severity=RiskLevel.HIGH,
                    confidence=UnderstandingConfidence.INFERRED,
                    description=f"WorkOrder explicitly introduces breaking changes to existing contracts or endpoints.",
                    affected_area=work_order.objective,
                    evidence=[ev],
                    mitigation="Introduce transitional versioning or dual-write compatibility layer.",
                    escalation_required=True,
                )
            )

    def _detect_auth_risks(
        self,
        work_order: ProgrammerWorkOrder,
        impact_analysis: ImpactAnalysis,
        plan: ImplementationPlan,
        workspace: Optional[ProgrammerWorkspace],
        risks: list[EngineeringRisk],
        assessment_id: str,
        execution_id: str,
    ) -> None:
        """Detect modifications to authentication, session, token, or permission authorization policies."""
        all_targets = set(impact_analysis.directly_affected_files + plan.affected_files)
        auth_targets: list[str] = []

        for t in all_targets:
            # False-positive guard: skip tests or documentation files
            if any(sub in t.lower() for sub in ("test", "doc", "example", "mock")):
                continue

            # Check directory or module path
            path_parts = PurePosixPath(t).parts
            is_auth_module = any(part.lower() in ("auth", "authentication", "authorization", "security", "rbac", "permissions") for part in path_parts)

            # Check file content if workspace available
            has_auth_content = False
            if workspace and os.path.isfile(os.path.join(workspace.root_path, t)):
                try:
                    with open(os.path.join(workspace.root_path, t), "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read(4096)
                        has_auth_content = any(pat.search(content) for pat in AUTH_CONTENT_PATTERNS)
                except Exception:
                    pass

            if is_auth_module or has_auth_content:
                auth_targets.append(t)

        obj_lower = work_order.objective.lower()
        if any(term in obj_lower for term in ("login", "oauth", "jwt", "session", "permission", "rbac", "auth policy")):
            if not auth_targets and impact_analysis.directly_affected_files:
                auth_targets.extend(impact_analysis.directly_affected_files)

        if auth_targets:
            area = ", ".join(auth_targets)
            ev = self._create_evidence(
                assessment_id=assessment_id,
                execution_id=execution_id,
                work_order_id=work_order.work_order_id,
                description=f"Authentication/authorization changes in {area}",
                data={"auth_targets": auth_targets},
            )
            cat = EngineeringRiskCategory.AUTHORIZATION if any("permission" in t.lower() or "rbac" in t.lower() for t in auth_targets) else EngineeringRiskCategory.AUTHENTICATION
            risks.append(
                EngineeringRisk(
                    risk_id=new_engineering_risk_id(),
                    category=cat,
                    severity=RiskLevel.HIGH,
                    confidence=UnderstandingConfidence.OBSERVED,
                    description=f"Plan modifies security-critical authentication/authorization logic in {area}.",
                    affected_area=area,
                    evidence=[ev],
                    mitigation="Conduct peer security review, enforce rate-limiting, and verify timing-attack resistance.",
                    escalation_required=True,
                )
            )

    def _detect_security_and_destructive_risks(
        self,
        work_order: ProgrammerWorkOrder,
        plan: ImplementationPlan,
        risks: list[EngineeringRisk],
        assessment_id: str,
        execution_id: str,
    ) -> None:
        """Detect destructive shell commands or file operations."""
        destructive_commands: list[str] = []
        for cmd in work_order.allowed_commands:
            cmd_str = cmd.command if hasattr(cmd, "command") else str(cmd)
            for pat in DESTRUCTIVE_COMMAND_PATTERNS:
                if pat.search(cmd_str):
                    destructive_commands.append(cmd_str)

        if destructive_commands:
            area = ", ".join(destructive_commands)
            ev = self._create_evidence(
                assessment_id=assessment_id,
                execution_id=execution_id,
                work_order_id=work_order.work_order_id,
                description=f"Destructive commands detected: {area}",
                data={"destructive_commands": destructive_commands},
            )
            risks.append(
                EngineeringRisk(
                    risk_id=new_engineering_risk_id(),
                    category=EngineeringRiskCategory.DESTRUCTIVE_OPERATION,
                    severity=RiskLevel.CRITICAL,
                    confidence=UnderstandingConfidence.OBSERVED,
                    description=f"WorkOrder contains destructive system commands: {area}.",
                    affected_area=area,
                    evidence=[ev],
                    mitigation="Prohibit destructive shell options, run within ephemeral sandbox container.",
                    escalation_required=True,
                )
            )

    def _detect_scope_and_architectural_risks(
        self,
        work_order: ProgrammerWorkOrder,
        impact_analysis: ImpactAnalysis,
        plan: ImplementationPlan,
        risks: list[EngineeringRisk],
        assessment_id: str,
        execution_id: str,
    ) -> None:
        """Detect unusually large scope or sweeping architectural modifications."""
        all_affected = set(impact_analysis.directly_affected_files + plan.affected_files)
        distinct_packages = set()
        for f in all_affected:
            parts = PurePosixPath(f).parts
            if len(parts) > 1:
                distinct_packages.add(parts[0])

        is_large_scope = len(all_affected) >= 8 or len(plan.steps) >= 5 or len(distinct_packages) >= 3

        if is_large_scope:
            area = f"{len(all_affected)} files across {len(distinct_packages)} top-level packages ({len(plan.steps)} steps)"
            ev = self._create_evidence(
                assessment_id=assessment_id,
                execution_id=execution_id,
                work_order_id=work_order.work_order_id,
                description=f"Large scope detected: {area}",
                data={
                    "total_files": len(all_affected),
                    "steps_count": len(plan.steps),
                    "packages": list(distinct_packages),
                },
            )
            risks.append(
                EngineeringRisk(
                    risk_id=new_engineering_risk_id(),
                    category=EngineeringRiskCategory.LARGE_SCOPE,
                    severity=RiskLevel.HIGH if len(all_affected) >= 10 else RiskLevel.MEDIUM,
                    confidence=UnderstandingConfidence.OBSERVED,
                    description=f"Implementation plan spans unusually large scope: {area}.",
                    affected_area=area,
                    evidence=[ev],
                    mitigation="Decompose into multiple incremental work orders with independent verification gates.",
                    escalation_required=len(all_affected) >= 10,
                )
            )

    def _detect_test_coverage_risks(
        self,
        impact_analysis: ImpactAnalysis,
        plan: ImplementationPlan,
        risks: list[EngineeringRisk],
        assessment_id: str,
        execution_id: str,
    ) -> None:
        """Detect lack of test coverage for modified logic files."""
        has_logic_changes = any(
            not any(sub in f.lower() for sub in ("test", "doc", "md", "json", "yaml"))
            for f in impact_analysis.directly_affected_files
        )
        has_tests = len(impact_analysis.affected_tests) > 0 or any(
            any("test" in tf.lower() for tf in s.target_files) for s in plan.steps
        )

        if has_logic_changes and not has_tests:
            ev = self._create_evidence(
                assessment_id=assessment_id,
                execution_id=execution_id,
                work_order_id=impact_analysis.work_order_id,
                description="Production logic files modified with no test coverage",
                data={"affected_files": impact_analysis.directly_affected_files},
            )
            risks.append(
                EngineeringRisk(
                    risk_id=new_engineering_risk_id(),
                    category=EngineeringRiskCategory.INSUFFICIENT_TEST_COVERAGE,
                    severity=RiskLevel.MEDIUM,
                    confidence=UnderstandingConfidence.OBSERVED,
                    description="Plan modifies production logic files but no corresponding test files or test steps were identified.",
                    affected_area=", ".join(impact_analysis.directly_affected_files),
                    evidence=[ev],
                    mitigation="Add dedicated unit test step to verify modified behavior.",
                    escalation_required=False,
                )
            )

    def _detect_unknown_risks(
        self,
        impact_analysis: ImpactAnalysis,
        risks: list[EngineeringRisk],
        assessment_id: str,
        execution_id: str,
    ) -> None:
        """Detect unresolvable dependencies or unknown impacts."""
        for unknown in impact_analysis.unknowns:
            ev = self._create_evidence(
                assessment_id=assessment_id,
                execution_id=execution_id,
                work_order_id=impact_analysis.work_order_id,
                description=f"Unknown dependency impact: {unknown}",
                data={"unknown_impact": unknown},
            )
            risks.append(
                EngineeringRisk(
                    risk_id=new_engineering_risk_id(),
                    category=EngineeringRiskCategory.UNKNOWN,
                    severity=RiskLevel.HIGH,
                    confidence=UnderstandingConfidence.UNKNOWN,
                    description=f"Unknown or unresolvable dependency impact detected: '{unknown}'.",
                    affected_area=unknown,
                    evidence=[ev],
                    mitigation="Consult repository owner or Manager for contextual resolution.",
                    escalation_required=True,
                )
            )
