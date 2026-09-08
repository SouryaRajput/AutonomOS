from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
from typing import Any, Optional, Sequence

from core.programmer.contracts.delivery import DeliveryPackage
from core.programmer.contracts.git_model import (
    GitExecutionContext,
    GitRepository,
    GitRevision,
)
from core.programmer.contracts.identifiers import (
    new_product_artifact_id,
    validate_delivery_id,
    validate_execution_id,
    validate_product_artifact_id,
    validate_work_order_id,
)
from core.programmer.contracts.verification import (
    VerificationEvidence,
    VerificationSummary,
)
from core.programmer.errors import (
    ArtifactLineageError,
    InvalidArtifactReferenceError,
    InvalidArtifactVersionError,
    NonexistentRevisionError,
    ProgrammerValidationError,
    UnauthorizedDeployabilityClaimError,
)
from core.programmer.types import (
    DeployabilityStatus,
    ProductArtifactType,
    VerificationStatus,
    VerificationSummaryStatus,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# Regex pattern for version validation (accepts semver, calver, PEP 440 style versions)
_VERSION_PATTERN = re.compile(
    r"^v?[0-9]+(\.[0-9a-zA-Z_\-]+)*([+\-][0-9a-zA-Z_\-\.]+)?$"
)


@dataclass
class DeployabilityAssessment:
    """
    Structured outcome of assessing an artifact's readiness and eligibility for deployment.
    Never assumes deployability exists simply because an artifact has been created or packaged.
    """
    status: DeployabilityStatus
    is_deployable: bool
    reasons: list[str] = field(default_factory=list)
    missing_prerequisites: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "is_deployable": self.is_deployable,
            "reasons": list(self.reasons),
            "missing_prerequisites": list(self.missing_prerequisites),
        }


@dataclass
class ProductArtifact:
    """
    Domain model representing a software product artifact produced by a Programmer execution.

    Architectural Lineage:
        ManagerTask -> ProgrammerWorkOrder -> ProgrammerExecution -> GitRevision -> ProductArtifact

    Core Invariants:
    1. Strict Revision Linkage: An artifact must reference the actual Git revision from which it was produced.
       An artifact can NEVER claim a revision that does not exist or does not match execution context.
    2. Non-Deployability by Default: Existing does not imply deployability. An artifact defaults to
       `is_deployable = False`. Only packaged artifacts with authoritative verification evidence can qualify.
    3. Categorization Disambiguation: Clearly distinguishes source code, build output, deployable package,
       container, configuration, and documentation. Source code, config, and docs are never deployable.
    4. Immutable Lineage & Provenance: Binds project, work order, execution, and delivery identities.
    5. Pure Local Domain: Never performs deployment, upload, external calls, or secret management.
    """
    artifact_id: str = field(default_factory=new_product_artifact_id)
    project_id: str = ""
    work_order_id: str = ""
    execution_id: str = ""
    source_revision: Optional[GitRevision] = None
    artifact_type: ProductArtifactType = ProductArtifactType.SOURCE
    artifact_reference: str = ""
    version: str = "0.1.0"
    build_metadata: dict[str, Any] = field(default_factory=dict)
    runtime_requirements: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    configuration_schema: dict[str, Any] = field(default_factory=dict)
    environment_requirements: dict[str, Any] = field(default_factory=dict)
    verification_summary: Optional[Any] = None
    delivery_reference: Optional[str] = None
    evidence: list[VerificationEvidence] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    is_deployable: bool = False
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        # Coerce artifact_type
        if isinstance(self.artifact_type, str):
            try:
                self.artifact_type = ProductArtifactType(self.artifact_type.upper())
            except (ValueError, TypeError):
                self.artifact_type = ProductArtifactType.SOURCE

        # Rehydrate source_revision if dict
        if isinstance(self.source_revision, dict):
            self.source_revision = GitRevision.from_dict(self.source_revision)

        # Rehydrate verification_summary if dict
        if isinstance(self.verification_summary, dict):
            try:
                self.verification_summary = VerificationSummary.from_dict(self.verification_summary)
            except Exception:
                pass

        # Rehydrate evidence if dicts
        normalized_ev: list[VerificationEvidence] = []
        for e in self.evidence:
            if isinstance(e, dict):
                normalized_ev.append(VerificationEvidence.from_dict(e))
            else:
                normalized_ev.append(e)
        self.evidence = normalized_ev

        # Rehydrate dependencies if not list
        if not isinstance(self.dependencies, list):
            self.dependencies = list(self.dependencies)

        self.validate()

    # -------------------------------------------------------------------------
    # Categorization Predicates
    # -------------------------------------------------------------------------

    def is_source(self) -> bool:
        """Check if this artifact represents raw source code."""
        return self.artifact_type == ProductArtifactType.SOURCE

    def is_build_output(self) -> bool:
        """Check if this artifact represents compiled/intermediate build outputs."""
        return self.artifact_type == ProductArtifactType.BUILD

    def is_package(self) -> bool:
        """Check if this artifact represents an installable/distributable software package."""
        return self.artifact_type == ProductArtifactType.PACKAGE

    def is_container(self) -> bool:
        """Check if this artifact represents an image or container bundle."""
        return self.artifact_type == ProductArtifactType.CONTAINER

    def is_documentation(self) -> bool:
        """Check if this artifact represents software documentation or manuals."""
        return self.artifact_type == ProductArtifactType.DOCUMENTATION

    def is_configuration(self) -> bool:
        """Check if this artifact represents configuration specifications or schemas."""
        return self.artifact_type == ProductArtifactType.CONFIGURATION

    # -------------------------------------------------------------------------
    # Deployability Assessment
    # -------------------------------------------------------------------------

    def assess_deployability(self) -> DeployabilityAssessment:
        """
        Objectively evaluate whether this artifact is eligible and qualified for deployment.
        Does not imply deployability merely because the artifact exists.
        """
        reasons: list[str] = []
        missing: list[str] = []

        # Non-package categories cannot be deployed directly
        if self.is_source():
            reasons.append("Source code is not an installable package or container.")
            return DeployabilityAssessment(
                status=DeployabilityStatus.NOT_DEPLOYABLE,
                is_deployable=False,
                reasons=reasons,
                missing_prerequisites=["Build and packaging step required"],
            )

        if self.is_documentation():
            reasons.append("Documentation artifacts cannot be deployed as software workloads.")
            return DeployabilityAssessment(
                status=DeployabilityStatus.NOT_DEPLOYABLE,
                is_deployable=False,
                reasons=reasons,
                missing_prerequisites=["Not an executable workload"],
            )

        if self.is_configuration():
            reasons.append("Configuration schemas alone are not deployable workloads.")
            return DeployabilityAssessment(
                status=DeployabilityStatus.NOT_DEPLOYABLE,
                is_deployable=False,
                reasons=reasons,
                missing_prerequisites=["Requires application runtime package"],
            )

        if self.is_build_output():
            reasons.append("Intermediate build outputs must be bundled into a package or container before deployment.")
            return DeployabilityAssessment(
                status=DeployabilityStatus.NOT_DEPLOYABLE,
                is_deployable=False,
                reasons=reasons,
                missing_prerequisites=["Packaging step required"],
            )

        # Artifact is PACKAGE or CONTAINER: verify prerequisites
        if not self.is_package() and not self.is_container():
            reasons.append(f"Artifact type '{self.artifact_type.value}' is not deployable.")
            return DeployabilityAssessment(
                status=DeployabilityStatus.NOT_DEPLOYABLE,
                is_deployable=False,
                reasons=reasons,
            )

        # Check authoritative verification evidence
        auth_evidence = [e for e in self.evidence if e.is_authoritative()]
        if not auth_evidence:
            reasons.append("Lacks authoritative verification evidence.")
            missing.append("Authoritative test/verification evidence")

        # Check verification summary if present
        if self.verification_summary is not None:
            if hasattr(self.verification_summary, "is_verified") and not self.verification_summary.is_verified:
                reasons.append("Verification summary indicates unverified or failed status.")
                missing.append("Passing verification summary")
            elif hasattr(self.verification_summary, "is_failed") and self.verification_summary.is_failed:
                reasons.append("Verification summary reports verification failures.")
                missing.append("Resolution of verification failures")

        # Check runtime requirements
        if not self.runtime_requirements and not self.is_container():
            reasons.append("Runtime requirements are unspecified.")
            missing.append("Runtime requirements specification")

        # Check explicit qualification
        if not self.is_deployable:
            reasons.append("Artifact has not been explicitly qualified for deployment.")
            missing.append("Explicit deployment qualification")

        if missing or not self.is_deployable:
            return DeployabilityAssessment(
                status=DeployabilityStatus.INELIGIBLE,
                is_deployable=False,
                reasons=reasons,
                missing_prerequisites=missing,
            )

        reasons.append("Artifact is packaged, verified with authoritative evidence, and qualified for deployment.")
        return DeployabilityAssessment(
            status=DeployabilityStatus.QUALIFIED,
            is_deployable=True,
            reasons=reasons,
            missing_prerequisites=[],
        )

    # -------------------------------------------------------------------------
    # Validation & Invariants
    # -------------------------------------------------------------------------

    def validate(
        self,
        project_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        work_order_id: Optional[str] = None,
        execution_context: Optional[GitExecutionContext] = None,
        delivery_package: Optional[DeliveryPackage] = None,
        repository: Optional[GitRepository] = None,
        known_revisions: Optional[Sequence[Any]] = None,
    ) -> None:
        """
        Validate internal invariants, identifier syntax, revision linkage,
        and cross-entity ownership.
        """
        # 1. Identifier syntax validation
        validate_product_artifact_id(self.artifact_id)

        # 2. Project ownership
        if not self.project_id or not isinstance(self.project_id, str) or not self.project_id.strip():
            raise ProgrammerValidationError(
                "ProductArtifact requires a non-empty project_id.",
                field_name="project_id",
            )

        # 3. Execution ownership
        if not self.execution_id or not isinstance(self.execution_id, str) or not self.execution_id.strip():
            raise ProgrammerValidationError(
                "ProductArtifact requires a non-empty execution_id.",
                field_name="execution_id",
            )
        validate_execution_id(self.execution_id)

        # 4. Work order ownership
        if not self.work_order_id or not isinstance(self.work_order_id, str) or not self.work_order_id.strip():
            raise ProgrammerValidationError(
                "ProductArtifact requires a non-empty work_order_id.",
                field_name="work_order_id",
            )
        validate_work_order_id(self.work_order_id)

        # 5. Artifact reference validation
        if not self.artifact_reference or not isinstance(self.artifact_reference, str) or not self.artifact_reference.strip():
            raise InvalidArtifactReferenceError(
                "ProductArtifact requires a non-empty artifact_reference.",
                artifact_reference=str(self.artifact_reference),
            )

        # 6. Version format validation
        if not self.version or not isinstance(self.version, str) or not self.version.strip():
            raise InvalidArtifactVersionError(
                "ProductArtifact requires a non-empty version string.",
                version=str(self.version),
            )
        v_clean = self.version.strip()
        if not _VERSION_PATTERN.match(v_clean):
            raise InvalidArtifactVersionError(
                f"Invalid version format '{self.version}'. Expected semantic or calver format (e.g., '1.0.0', 'v2.1.0-beta.1').",
                version=self.version,
            )

        # 7. Source revision linkage
        if self.source_revision is None:
            raise NonexistentRevisionError(
                "ProductArtifact must reference an authoritative source_revision.",
            )
        if not isinstance(self.source_revision, GitRevision):
            raise ProgrammerValidationError(
                "source_revision must be a GitRevision instance.",
                field_name="source_revision",
            )
        self.source_revision.validate()

        # 8. Delivery reference syntax (if present)
        if self.delivery_reference is not None and self.delivery_reference.strip():
            validate_delivery_id(self.delivery_reference)

        # 9. Evidence lineage and integrity
        for ev in self.evidence:
            ev.validate()
            if ev.execution_id != self.execution_id:
                raise ArtifactLineageError(
                    f"VerificationEvidence execution_id '{ev.execution_id}' does not match ProductArtifact execution_id '{self.execution_id}'."
                )
            if ev.work_order_id != self.work_order_id:
                raise ArtifactLineageError(
                    f"VerificationEvidence work_order_id '{ev.work_order_id}' does not match ProductArtifact work_order_id '{self.work_order_id}'."
                )

        # 10. Verification summary lineage (if present)
        if self.verification_summary is not None:
            if hasattr(self.verification_summary, "execution_id") and self.verification_summary.execution_id:
                if self.verification_summary.execution_id != self.execution_id:
                    raise ArtifactLineageError(
                        f"VerificationSummary execution_id '{self.verification_summary.execution_id}' does not match ProductArtifact execution_id '{self.execution_id}'."
                    )
            if hasattr(self.verification_summary, "work_order_id") and self.verification_summary.work_order_id:
                if self.verification_summary.work_order_id != self.work_order_id:
                    raise ArtifactLineageError(
                        f"VerificationSummary work_order_id '{self.verification_summary.work_order_id}' does not match ProductArtifact work_order_id '{self.work_order_id}'."
                    )

        # 11. Deployability claim validation: Cannot claim is_deployable without qualification
        if self.is_deployable:
            if not self.is_package() and not self.is_container():
                raise UnauthorizedDeployabilityClaimError(
                    f"Artifact of type '{self.artifact_type.value}' cannot be marked deployable. Only PACKAGE and CONTAINER artifacts can be deployable."
                )
            auth_ev = [e for e in self.evidence if e.is_authoritative()]
            if not auth_ev:
                raise UnauthorizedDeployabilityClaimError(
                    "ProductArtifact cannot claim deployability without authoritative verification evidence."
                )
            if self.verification_summary is not None:
                if hasattr(self.verification_summary, "is_verified") and not self.verification_summary.is_verified:
                    raise UnauthorizedDeployabilityClaimError(
                        "ProductArtifact cannot claim deployability when verification summary is unverified or failed."
                    )

        # ---------------------------------------------------------------------
        # Cross-Entity Cross-Validation
        # ---------------------------------------------------------------------

        # Direct parameter checks
        if project_id is not None and self.project_id != project_id:
            raise ArtifactLineageError(
                f"Project mismatch: ProductArtifact project_id '{self.project_id}' does not match expected '{project_id}'."
            )
        if execution_id is not None and self.execution_id != execution_id:
            raise ArtifactLineageError(
                f"Execution mismatch: ProductArtifact execution_id '{self.execution_id}' does not match expected '{execution_id}'."
            )
        if work_order_id is not None and self.work_order_id != work_order_id:
            raise ArtifactLineageError(
                f"Work order mismatch: ProductArtifact work_order_id '{self.work_order_id}' does not match expected '{work_order_id}'."
            )

        # GitExecutionContext linkage
        if execution_context is not None:
            if execution_context.execution_id != self.execution_id:
                raise ArtifactLineageError(
                    f"GitExecutionContext execution_id '{execution_context.execution_id}' does not match artifact '{self.execution_id}'."
                )
            if execution_context.work_order_id != self.work_order_id:
                raise ArtifactLineageError(
                    f"GitExecutionContext work_order_id '{execution_context.work_order_id}' does not match artifact '{self.work_order_id}'."
                )
            if execution_context.project_id and execution_context.project_id != self.project_id:
                raise ArtifactLineageError(
                    f"GitExecutionContext project_id '{execution_context.project_id}' does not match artifact '{self.project_id}'."
                )

            # Revision linkage check: must match resulting_revision (or base_revision if no changes)
            valid_hashes: set[str] = set()
            if execution_context.resulting_revision:
                valid_hashes.add(execution_context.resulting_revision.commit_hash)
            if execution_context.base_revision:
                valid_hashes.add(execution_context.base_revision.commit_hash)

            if self.source_revision.commit_hash not in valid_hashes:
                raise NonexistentRevisionError(
                    f"Artifact claims source revision '{self.source_revision.commit_hash}' which does not exist in execution context lineage.",
                    commit_hash=self.source_revision.commit_hash,
                )

        # DeliveryPackage linkage
        if delivery_package is not None:
            if delivery_package.execution_id != self.execution_id:
                raise ArtifactLineageError(
                    f"DeliveryPackage execution_id '{delivery_package.execution_id}' does not match artifact '{self.execution_id}'."
                )
            if delivery_package.work_order_id != self.work_order_id:
                raise ArtifactLineageError(
                    f"DeliveryPackage work_order_id '{delivery_package.work_order_id}' does not match artifact '{self.work_order_id}'."
                )
            pkg_proj = delivery_package.metadata.get("project_id")
            if pkg_proj and pkg_proj != self.project_id:
                raise ArtifactLineageError(
                    f"DeliveryPackage project_id '{pkg_proj}' does not match artifact '{self.project_id}'."
                )

            valid_delivery_hashes: set[str] = set()
            res_rev = delivery_package.result_revision()
            if res_rev:
                valid_delivery_hashes.add(res_rev)
            start_rev = delivery_package.start_revision()
            if start_rev:
                valid_delivery_hashes.add(start_rev)

            if valid_delivery_hashes and self.source_revision.commit_hash not in valid_delivery_hashes:
                raise NonexistentRevisionError(
                    f"Artifact claims source revision '{self.source_revision.commit_hash}' which does not exist in delivery package.",
                    commit_hash=self.source_revision.commit_hash,
                )

        # GitRepository check
        if repository is not None:
            if repository.project_id != self.project_id:
                raise ArtifactLineageError(
                    f"Repository project_id '{repository.project_id}' does not match artifact '{self.project_id}'."
                )

        # Known revisions collection check
        if known_revisions is not None:
            known_hashes: set[str] = set()
            for r in known_revisions:
                if isinstance(r, GitRevision):
                    known_hashes.add(r.commit_hash)
                elif isinstance(r, str):
                    known_hashes.add(r)
            if self.source_revision.commit_hash not in known_hashes:
                raise NonexistentRevisionError(
                    f"Artifact claims nonexistent revision '{self.source_revision.commit_hash}'. Not found in known repository revisions.",
                    commit_hash=self.source_revision.commit_hash,
                )

    # -------------------------------------------------------------------------
    # Serialization
    # -------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Convert ProductArtifact to deterministic serializable dictionary."""
        return {
            "artifact_id": self.artifact_id,
            "project_id": self.project_id,
            "work_order_id": self.work_order_id,
            "execution_id": self.execution_id,
            "source_revision": self.source_revision.to_dict() if self.source_revision else None,
            "artifact_type": self.artifact_type.value,
            "artifact_reference": self.artifact_reference,
            "version": self.version,
            "build_metadata": dict(self.build_metadata),
            "runtime_requirements": dict(self.runtime_requirements),
            "dependencies": list(self.dependencies),
            "configuration_schema": dict(self.configuration_schema),
            "environment_requirements": dict(self.environment_requirements),
            "verification_summary": self.verification_summary.to_dict() if hasattr(self.verification_summary, "to_dict") else self.verification_summary,
            "delivery_reference": self.delivery_reference,
            "evidence": [e.to_dict() for e in self.evidence],
            "trace": dict(self.trace),
            "is_deployable": self.is_deployable,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        """Convert ProductArtifact to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProductArtifact:
        """Construct ProductArtifact from a dictionary."""
        rev = GitRevision.from_dict(data["source_revision"]) if data.get("source_revision") else None

        at_raw = data.get("artifact_type", ProductArtifactType.SOURCE.value)
        try:
            artifact_type = ProductArtifactType(str(at_raw).upper())
        except (ValueError, TypeError):
            artifact_type = ProductArtifactType.SOURCE

        ver_sum = data.get("verification_summary")
        if isinstance(ver_sum, dict):
            try:
                ver_sum = VerificationSummary.from_dict(ver_sum)
            except Exception:
                pass

        ev_list = [VerificationEvidence.from_dict(e) for e in data.get("evidence", [])]

        return cls(
            artifact_id=data.get("artifact_id", new_product_artifact_id()),
            project_id=data.get("project_id", ""),
            work_order_id=data.get("work_order_id", ""),
            execution_id=data.get("execution_id", ""),
            source_revision=rev,
            artifact_type=artifact_type,
            artifact_reference=data.get("artifact_reference", ""),
            version=data.get("version", "0.1.0"),
            build_metadata=dict(data.get("build_metadata", {})),
            runtime_requirements=dict(data.get("runtime_requirements", {})),
            dependencies=list(data.get("dependencies", [])),
            configuration_schema=dict(data.get("configuration_schema", {})),
            environment_requirements=dict(data.get("environment_requirements", {})),
            verification_summary=ver_sum,
            delivery_reference=data.get("delivery_reference"),
            evidence=ev_list,
            trace=dict(data.get("trace", {})),
            is_deployable=bool(data.get("is_deployable", False)),
            created_at=data.get("created_at", utc_now()),
        )

    @classmethod
    def from_json(cls, json_str: str) -> ProductArtifact:
        """Construct ProductArtifact from a JSON string."""
        return cls.from_dict(json.loads(json_str))


class ProductArtifactBuilder:
    """
    Fluent builder for constructing ProductArtifact instances with strict invariant validation.
    """

    def __init__(
        self,
        project_id: str,
        work_order_id: str,
        execution_id: str,
        source_revision: GitRevision,
    ):
        self.project_id = project_id
        self.work_order_id = work_order_id
        self.execution_id = execution_id
        self.source_revision = source_revision
        self.artifact_id = new_product_artifact_id()
        self.artifact_type = ProductArtifactType.SOURCE
        self.artifact_reference = ""
        self.version = "0.1.0"
        self.build_metadata: dict[str, Any] = {}
        self.runtime_requirements: dict[str, Any] = {}
        self.dependencies: list[str] = []
        self.configuration_schema: dict[str, Any] = {}
        self.environment_requirements: dict[str, Any] = {}
        self.verification_summary: Optional[Any] = None
        self.delivery_reference: Optional[str] = None
        self.evidence: list[VerificationEvidence] = []
        self.trace: dict[str, Any] = {
            "builder": "ProductArtifactBuilder",
            "created_at": utc_now(),
        }
        self.is_deployable: bool = False

    def with_id(self, artifact_id: str) -> ProductArtifactBuilder:
        self.artifact_id = artifact_id
        return self

    def with_type(self, artifact_type: ProductArtifactType) -> ProductArtifactBuilder:
        self.artifact_type = artifact_type
        return self

    def with_reference(self, artifact_reference: str) -> ProductArtifactBuilder:
        self.artifact_reference = artifact_reference
        return self

    def with_version(self, version: str) -> ProductArtifactBuilder:
        self.version = version
        return self

    def with_build_metadata(self, metadata: dict[str, Any]) -> ProductArtifactBuilder:
        self.build_metadata.update(metadata)
        return self

    def with_runtime_requirements(self, requirements: dict[str, Any]) -> ProductArtifactBuilder:
        self.runtime_requirements.update(requirements)
        return self

    def with_dependencies(self, dependencies: Sequence[str]) -> ProductArtifactBuilder:
        self.dependencies = list(dependencies)
        return self

    def with_configuration_schema(self, schema: dict[str, Any]) -> ProductArtifactBuilder:
        self.configuration_schema.update(schema)
        return self

    def with_environment_requirements(self, requirements: dict[str, Any]) -> ProductArtifactBuilder:
        self.environment_requirements.update(requirements)
        return self

    def with_verification_summary(self, summary: Any) -> ProductArtifactBuilder:
        self.verification_summary = summary
        return self

    def with_delivery_reference(self, delivery_id: str) -> ProductArtifactBuilder:
        self.delivery_reference = delivery_id
        return self

    def with_evidence(self, evidence: Sequence[VerificationEvidence]) -> ProductArtifactBuilder:
        self.evidence = list(evidence)
        return self

    def add_evidence(self, ev: VerificationEvidence) -> ProductArtifactBuilder:
        self.evidence.append(ev)
        return self

    def with_trace(self, trace: dict[str, Any]) -> ProductArtifactBuilder:
        self.trace.update(trace)
        return self

    def qualify_for_deployment(self) -> ProductArtifactBuilder:
        """
        Explicitly qualify this artifact for deployment.
        Will be validated against authoritative verification evidence and artifact type during build.
        """
        self.is_deployable = True
        return self

    def build(self) -> ProductArtifact:
        """Build and validate the ProductArtifact instance."""
        return ProductArtifact(
            artifact_id=self.artifact_id,
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            source_revision=self.source_revision,
            artifact_type=self.artifact_type,
            artifact_reference=self.artifact_reference,
            version=self.version,
            build_metadata=dict(self.build_metadata),
            runtime_requirements=dict(self.runtime_requirements),
            dependencies=list(self.dependencies),
            configuration_schema=dict(self.configuration_schema),
            environment_requirements=dict(self.environment_requirements),
            verification_summary=self.verification_summary,
            delivery_reference=self.delivery_reference,
            evidence=list(self.evidence),
            trace=dict(self.trace),
            is_deployable=self.is_deployable,
        )
