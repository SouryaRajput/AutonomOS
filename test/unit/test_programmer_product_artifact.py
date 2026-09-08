from __future__ import annotations

import pytest

from core.programmer.contracts.delivery import DeliveryPackage
from core.programmer.contracts.git_change_set import ChangeSet
from core.programmer.contracts.git_model import (
    GitExecutionContext,
    GitRepository,
    GitRevision,
)
from core.programmer.contracts.identifiers import (
    new_delivery_id,
    new_execution_id,
    new_git_repository_id,
    new_git_revision_id,
    new_product_artifact_id,
    new_verification_evidence_id,
    new_work_order_id,
    validate_product_artifact_id,
)
from core.programmer.contracts.product_artifact import (
    DeployabilityAssessment,
    ProductArtifact,
    ProductArtifactBuilder,
)
from core.programmer.contracts.verification import (
    VerificationEvidence,
    VerificationSummary,
)
from core.programmer.errors import (
    ArtifactLineageError,
    InvalidArtifactReferenceError,
    InvalidArtifactVersionError,
    InvalidProgrammerIdError,
    NonexistentRevisionError,
    ProgrammerValidationError,
    UnauthorizedDeployabilityClaimError,
)
from core.programmer.types import (
    DeployabilityStatus,
    ManagerDisposition,
    ProductArtifactType,
    VerificationEvidenceSourceType,
    VerificationSummaryStatus,
)


def make_valid_revision(
    commit_hash: str = "a" * 40,
    branch: str = "main",
) -> GitRevision:
    """Helper to create a valid GitRevision."""
    return GitRevision(
        revision_id=new_git_revision_id(),
        commit_hash=commit_hash,
        branch=branch,
    )


def make_valid_evidence(
    execution_id: str,
    work_order_id: str,
    is_agent_claim: bool = False,
    description: str = "Integration test execution passed",
) -> VerificationEvidence:
    """Helper to create a valid VerificationEvidence."""
    return VerificationEvidence(
        evidence_id=new_verification_evidence_id(),
        execution_id=execution_id,
        work_order_id=work_order_id,
        source_type=VerificationEvidenceSourceType.AGENT_CLAIM if is_agent_claim else VerificationEvidenceSourceType.TEST_RUNNER,
        source_reference="pytest test/test_app.py",
        description=description,
        is_agent_claim=is_agent_claim,
        data={"passed": 10, "failed": 0},
    )


class TestProgrammerProductArtifact:
    """
    Unit test suite validating the ProductArtifact domain model, its strict revision
    linkage, non-deployability defaults, categorization, lineage, and validation invariants.
    """

    def test_01_product_artifact_instantiation_defaults_and_fields(self) -> None:
        """Verify complete field initialization, default values, and serialization fidelity."""
        wo_id = new_work_order_id()
        exec_id = new_execution_id()
        rev = make_valid_revision()

        artifact = ProductArtifact(
            project_id="proj-commerce-001",
            work_order_id=wo_id,
            execution_id=exec_id,
            source_revision=rev,
            artifact_type=ProductArtifactType.PACKAGE,
            artifact_reference="dist/commerce_pkg-1.2.0.tar.gz",
            version="1.2.0",
            build_metadata={"compiler": "wheel", "target": "py3-none-any"},
            runtime_requirements={"python": ">=3.12"},
            dependencies=["fastapi>=0.100.0", "pydantic>=2.0"],
            configuration_schema={"properties": {"PORT": {"type": "integer"}}},
            environment_requirements={"PORT": "8080"},
            delivery_reference=new_delivery_id(),
            trace={"builder": "test-runner"},
        )

        assert artifact.artifact_id.startswith("part-")
        validate_product_artifact_id(artifact.artifact_id)
        assert artifact.project_id == "proj-commerce-001"
        assert artifact.work_order_id == wo_id
        assert artifact.execution_id == exec_id
        assert artifact.source_revision.commit_hash == "a" * 40
        assert artifact.artifact_type == ProductArtifactType.PACKAGE
        assert artifact.version == "1.2.0"
        assert artifact.is_deployable is False  # Invariant: non-deployable by default
        assert len(artifact.dependencies) == 2

        # Test dictionary & JSON serialization roundtrips
        data = artifact.to_dict()
        assert data["artifact_id"] == artifact.artifact_id
        assert data["artifact_type"] == "PACKAGE"
        assert data["version"] == "1.2.0"
        assert data["source_revision"]["commit_hash"] == "a" * 40

        rehydrated = ProductArtifact.from_dict(data)
        assert rehydrated.artifact_id == artifact.artifact_id
        assert rehydrated.source_revision.commit_hash == artifact.source_revision.commit_hash
        assert rehydrated.is_package() is True

        json_str = artifact.to_json()
        assert artifact.artifact_id in json_str
        from_json = ProductArtifact.from_json(json_str)
        assert from_json.artifact_id == artifact.artifact_id

    def test_02_artifact_categorization_and_type_predicates(self) -> None:
        """Verify strict distinction among source, build, package, container, config, and doc."""
        wo_id = new_work_order_id()
        exec_id = new_execution_id()
        rev = make_valid_revision()

        types_and_predicates = [
            (ProductArtifactType.SOURCE, "src/index.ts", lambda a: a.is_source()),
            (ProductArtifactType.BUILD, "build/app.o", lambda a: a.is_build_output()),
            (ProductArtifactType.PACKAGE, "dist/app-1.0.whl", lambda a: a.is_package()),
            (ProductArtifactType.CONTAINER, "docker://org/app:v1", lambda a: a.is_container()),
            (ProductArtifactType.DOCUMENTATION, "docs/api.html", lambda a: a.is_documentation()),
            (ProductArtifactType.CONFIGURATION, "config/schema.json", lambda a: a.is_configuration()),
        ]

        for art_type, ref, predicate in types_and_predicates:
            art = ProductArtifact(
                project_id="proj-alpha",
                work_order_id=wo_id,
                execution_id=exec_id,
                source_revision=rev,
                artifact_type=art_type,
                artifact_reference=ref,
            )
            assert predicate(art) is True
            assert art.artifact_type == art_type

        # String coercion in post_init (case insensitive)
        art_coerced = ProductArtifact(
            project_id="proj-alpha",
            work_order_id=wo_id,
            execution_id=exec_id,
            source_revision=rev,
            artifact_type="container",  # lowercase string
            artifact_reference="docker://org/app:v1",
        )
        assert art_coerced.artifact_type == ProductArtifactType.CONTAINER
        assert art_coerced.is_container() is True

    def test_03_non_deployability_by_default(self) -> None:
        """Verify invariant: Do not imply that an artifact is deployable merely because it exists."""
        wo_id = new_work_order_id()
        exec_id = new_execution_id()
        rev = make_valid_revision()

        # Existing package artifact
        package_artifact = ProductArtifact(
            project_id="proj-alpha",
            work_order_id=wo_id,
            execution_id=exec_id,
            source_revision=rev,
            artifact_type=ProductArtifactType.PACKAGE,
            artifact_reference="dist/app-1.0.whl",
        )

        assert package_artifact.is_deployable is False

        # Assess deployability: must be INELIGIBLE because it has not been explicitly qualified or verified
        assessment: DeployabilityAssessment = package_artifact.assess_deployability()
        assert assessment.is_deployable is False
        assert assessment.status == DeployabilityStatus.INELIGIBLE
        assert "Authoritative test/verification evidence" in assessment.missing_prerequisites
        assert "Explicit deployment qualification" in assessment.missing_prerequisites

    def test_04_source_documentation_configuration_cannot_be_marked_deployable(self) -> None:
        """Verify that non-deployable artifact types cannot be marked deployable under any circumstances."""
        wo_id = new_work_order_id()
        exec_id = new_execution_id()
        rev = make_valid_revision()
        ev = make_valid_evidence(exec_id, wo_id, is_agent_claim=False)

        non_deployable_types = [
            ProductArtifactType.SOURCE,
            ProductArtifactType.BUILD,
            ProductArtifactType.DOCUMENTATION,
            ProductArtifactType.CONFIGURATION,
        ]

        for nd_type in non_deployable_types:
            # 1. Attempting to create with is_deployable = True must raise UnauthorizedDeployabilityClaimError
            with pytest.raises(UnauthorizedDeployabilityClaimError) as exc_info:
                ProductArtifact(
                    project_id="proj-alpha",
                    work_order_id=wo_id,
                    execution_id=exec_id,
                    source_revision=rev,
                    artifact_type=nd_type,
                    artifact_reference=f"test/{nd_type.value.lower()}",
                    evidence=[ev],
                    is_deployable=True,
                )
            assert "cannot be marked deployable" in str(exc_info.value)

            # 2. assess_deployability returns NOT_DEPLOYABLE
            art = ProductArtifact(
                project_id="proj-alpha",
                work_order_id=wo_id,
                execution_id=exec_id,
                source_revision=rev,
                artifact_type=nd_type,
                artifact_reference=f"test/{nd_type.value.lower()}",
                evidence=[ev],
                is_deployable=False,
            )
            assessment = art.assess_deployability()
            assert assessment.status == DeployabilityStatus.NOT_DEPLOYABLE
            assert assessment.is_deployable is False

    def test_05_deployability_qualification_requires_authoritative_evidence(self) -> None:
        """Verify that PACKAGE/CONTAINER artifacts require authoritative evidence to qualify for deployment."""
        wo_id = new_work_order_id()
        exec_id = new_execution_id()
        rev = make_valid_revision()

        # 1. Attempting to mark deployable without ANY evidence raises error
        with pytest.raises(UnauthorizedDeployabilityClaimError) as exc_info:
            ProductArtifact(
                project_id="proj-alpha",
                work_order_id=wo_id,
                execution_id=exec_id,
                source_revision=rev,
                artifact_type=ProductArtifactType.PACKAGE,
                artifact_reference="dist/app.whl",
                is_deployable=True,
            )
        assert "without authoritative verification evidence" in str(exc_info.value)

        # 2. Attempting to mark deployable with ONLY agent claims (non-authoritative) raises error
        claim_ev = make_valid_evidence(exec_id, wo_id, is_agent_claim=True, description="Agent claims tests pass")
        with pytest.raises(UnauthorizedDeployabilityClaimError) as exc_info:
            ProductArtifact(
                project_id="proj-alpha",
                work_order_id=wo_id,
                execution_id=exec_id,
                source_revision=rev,
                artifact_type=ProductArtifactType.PACKAGE,
                artifact_reference="dist/app.whl",
                evidence=[claim_ev],
                is_deployable=True,
            )
        assert "without authoritative verification evidence" in str(exc_info.value)

        # 3. Attempting to mark deployable when verification summary is failed raises error
        auth_ev = make_valid_evidence(exec_id, wo_id, is_agent_claim=False)
        failed_summary = VerificationSummary(
            execution_id=exec_id,
            work_order_id=wo_id,
            overall_status=VerificationSummaryStatus.FAILED,
        )
        with pytest.raises(UnauthorizedDeployabilityClaimError) as exc_info:
            ProductArtifact(
                project_id="proj-alpha",
                work_order_id=wo_id,
                execution_id=exec_id,
                source_revision=rev,
                artifact_type=ProductArtifactType.PACKAGE,
                artifact_reference="dist/app.whl",
                evidence=[auth_ev],
                verification_summary=failed_summary,
                is_deployable=True,
            )
        assert "unverified or failed" in str(exc_info.value)

        # 4. Valid qualification with authoritative evidence and passing summary
        passing_summary = VerificationSummary(
            execution_id=exec_id,
            work_order_id=wo_id,
            overall_status=VerificationSummaryStatus.VERIFIED,
        )
        qualified_art = ProductArtifact(
            project_id="proj-alpha",
            work_order_id=wo_id,
            execution_id=exec_id,
            source_revision=rev,
            artifact_type=ProductArtifactType.PACKAGE,
            artifact_reference="dist/app.whl",
            runtime_requirements={"python": ">=3.12"},
            evidence=[auth_ev],
            verification_summary=passing_summary,
            is_deployable=True,
        )
        assert qualified_art.is_deployable is True
        assessment = qualified_art.assess_deployability()
        assert assessment.status == DeployabilityStatus.QUALIFIED
        assert assessment.is_deployable is True
        assert len(assessment.missing_prerequisites) == 0

    def test_06_strict_source_revision_linkage_and_nonexistent_revision_rejection(self) -> None:
        """Verify requirement: Artifact must reference actual revision; cannot claim nonexistent revision."""
        wo_id = new_work_order_id()
        exec_id = new_execution_id()
        base_rev = make_valid_revision(commit_hash="1" * 40)
        res_rev = make_valid_revision(commit_hash="2" * 40)
        bogus_rev = make_valid_revision(commit_hash="9" * 40)

        # 1. Missing source revision entirely
        with pytest.raises(NonexistentRevisionError):
            ProductArtifact(
                project_id="proj-alpha",
                work_order_id=wo_id,
                execution_id=exec_id,
                source_revision=None,  # type: ignore
                artifact_reference="src/",
            )

        # 2. Artifact with bogus revision against GitExecutionContext
        git_context = GitExecutionContext(
            repository_id=new_git_repository_id(),
            execution_id=exec_id,
            work_order_id=wo_id,
            project_id="proj-alpha",
            base_revision=base_rev,
            resulting_revision=res_rev,
            workspace_path="/tmp/ws",
        )

        bogus_art = ProductArtifact(
            project_id="proj-alpha",
            work_order_id=wo_id,
            execution_id=exec_id,
            source_revision=bogus_rev,
            artifact_reference="dist/app.tar.gz",
        )

        with pytest.raises(NonexistentRevisionError) as exc_info:
            bogus_art.validate(execution_context=git_context)
        assert "does not exist in execution context lineage" in str(exc_info.value)
        assert exc_info.value.commit_hash == bogus_rev.commit_hash

        # 3. Artifact referencing actual resulting_revision passes
        valid_art = ProductArtifact(
            project_id="proj-alpha",
            work_order_id=wo_id,
            execution_id=exec_id,
            source_revision=res_rev,
            artifact_reference="dist/app.tar.gz",
        )
        valid_art.validate(execution_context=git_context)  # passes cleanly

        # 4. Cross-check against known revisions list
        with pytest.raises(NonexistentRevisionError):
            bogus_art.validate(known_revisions=[base_rev, res_rev])

        valid_art.validate(known_revisions=[base_rev, res_rev])  # passes cleanly

    def test_07_project_ownership_and_lineage_validation(self) -> None:
        """Verify project ownership validation and cross-project rejection."""
        wo_id = new_work_order_id()
        exec_id = new_execution_id()
        rev = make_valid_revision()

        # 1. Empty project ID rejected
        with pytest.raises(ProgrammerValidationError) as exc_info:
            ProductArtifact(
                project_id="",
                work_order_id=wo_id,
                execution_id=exec_id,
                source_revision=rev,
                artifact_reference="dist/app.whl",
            )
        assert "project_id" in str(exc_info.value)

        # 2. Project ID mismatch against validation target
        art = ProductArtifact(
            project_id="proj-team-alpha",
            work_order_id=wo_id,
            execution_id=exec_id,
            source_revision=rev,
            artifact_reference="dist/app.whl",
        )

        with pytest.raises(ArtifactLineageError) as exc_info:
            art.validate(project_id="proj-team-beta")
        assert "Project mismatch" in str(exc_info.value)

        # 3. Project mismatch against GitRepository
        repo = GitRepository(
            repository_id=new_git_repository_id(),
            project_id="proj-team-beta",
            root_path="/repos/beta",
        )
        with pytest.raises(ArtifactLineageError) as exc_info:
            art.validate(repository=repo)
        assert "Repository project_id" in str(exc_info.value)

    def test_08_execution_and_work_order_ownership_validation(self) -> None:
        """Verify execution ownership, work order ownership, and prefix validation."""
        rev = make_valid_revision()

        # 1. Invalid execution ID prefix
        with pytest.raises(InvalidProgrammerIdError):
            ProductArtifact(
                project_id="proj-01",
                work_order_id=new_work_order_id(),
                execution_id="invalid-exec-id",
                source_revision=rev,
                artifact_reference="dist/app.whl",
            )

        # 2. Invalid work order ID prefix
        with pytest.raises(InvalidProgrammerIdError):
            ProductArtifact(
                project_id="proj-01",
                work_order_id="bad-wo-123",
                execution_id=new_execution_id(),
                source_revision=rev,
                artifact_reference="dist/app.whl",
            )

        # 3. Lineage mismatch against GitExecutionContext
        exec_id1 = new_execution_id()
        exec_id2 = new_execution_id()
        wo_id = new_work_order_id()

        art = ProductArtifact(
            project_id="proj-01",
            work_order_id=wo_id,
            execution_id=exec_id1,
            source_revision=rev,
            artifact_reference="dist/app.whl",
        )

        git_context = GitExecutionContext(
            repository_id=new_git_repository_id(),
            execution_id=exec_id2,  # different execution
            work_order_id=wo_id,
            project_id="proj-01",
            base_revision=rev,
            workspace_path="/tmp/ws",
        )

        with pytest.raises(ArtifactLineageError) as exc_info:
            art.validate(execution_context=git_context)
        assert "execution_id" in str(exc_info.value)

    def test_09_artifact_reference_and_version_validation(self) -> None:
        """Verify validation of artifact references and semantic versioning."""
        wo_id = new_work_order_id()
        exec_id = new_execution_id()
        rev = make_valid_revision()

        # 1. Empty artifact reference rejected
        with pytest.raises(InvalidArtifactReferenceError):
            ProductArtifact(
                project_id="proj-01",
                work_order_id=wo_id,
                execution_id=exec_id,
                source_revision=rev,
                artifact_reference="",
            )

        with pytest.raises(InvalidArtifactReferenceError):
            ProductArtifact(
                project_id="proj-01",
                work_order_id=wo_id,
                execution_id=exec_id,
                source_revision=rev,
                artifact_reference="   ",
            )

        # 2. Empty or invalid version rejected
        with pytest.raises(InvalidArtifactVersionError):
            ProductArtifact(
                project_id="proj-01",
                work_order_id=wo_id,
                execution_id=exec_id,
                source_revision=rev,
                artifact_reference="dist/app.whl",
                version="",
            )

        with pytest.raises(InvalidArtifactVersionError):
            ProductArtifact(
                project_id="proj-01",
                work_order_id=wo_id,
                execution_id=exec_id,
                source_revision=rev,
                artifact_reference="dist/app.whl",
                version="not a valid version string!",
            )

        # 3. Valid versions accepted
        valid_versions = ["0.1.0", "v1.2.3", "2.0.0-beta.1", "2026.09.01", "1.0.0+build42"]
        for v in valid_versions:
            art = ProductArtifact(
                project_id="proj-01",
                work_order_id=wo_id,
                execution_id=exec_id,
                source_revision=rev,
                artifact_reference="dist/app.whl",
                version=v,
            )
            assert art.version == v

    def test_10_verification_evidence_lineage_and_summary_validation(self) -> None:
        """Verify that attached evidence and verification summaries must match artifact lineage."""
        wo_id = new_work_order_id()
        exec_id = new_execution_id()
        rev = make_valid_revision()

        # Evidence with mismatched execution ID
        foreign_ev = make_valid_evidence(
            execution_id=new_execution_id(),  # foreign
            work_order_id=wo_id,
        )

        with pytest.raises(ArtifactLineageError) as exc_info:
            ProductArtifact(
                project_id="proj-01",
                work_order_id=wo_id,
                execution_id=exec_id,
                source_revision=rev,
                artifact_reference="dist/app.whl",
                evidence=[foreign_ev],
            )
        assert "VerificationEvidence execution_id" in str(exc_info.value)

        # Summary with mismatched work order ID
        foreign_summary = VerificationSummary(
            execution_id=exec_id,
            work_order_id=new_work_order_id(),  # foreign
            overall_status=VerificationSummaryStatus.VERIFIED,
        )
        with pytest.raises(ArtifactLineageError) as exc_info:
            ProductArtifact(
                project_id="proj-01",
                work_order_id=wo_id,
                execution_id=exec_id,
                source_revision=rev,
                artifact_reference="dist/app.whl",
                verification_summary=foreign_summary,
            )
        assert "VerificationSummary work_order_id" in str(exc_info.value)

    def test_11_product_artifact_builder_fluent_api(self) -> None:
        """Verify construction using the fluent ProductArtifactBuilder."""
        wo_id = new_work_order_id()
        exec_id = new_execution_id()
        rev = make_valid_revision()
        ev = make_valid_evidence(exec_id, wo_id)
        summary = VerificationSummary(
            execution_id=exec_id,
            work_order_id=wo_id,
            overall_status=VerificationSummaryStatus.VERIFIED,
        )

        builder = (
            ProductArtifactBuilder(
                project_id="proj-builder-test",
                work_order_id=wo_id,
                execution_id=exec_id,
                source_revision=rev,
            )
            .with_type(ProductArtifactType.CONTAINER)
            .with_reference("registry.internal/service:2.4.0")
            .with_version("2.4.0")
            .with_build_metadata({"base_image": "distroless/python3"})
            .with_runtime_requirements({"memory": "512Mi", "cpu": "0.5"})
            .with_dependencies(["numpy>=1.26.0"])
            .with_configuration_schema({"type": "object"})
            .with_environment_requirements({"ENV": "production"})
            .with_delivery_reference(new_delivery_id())
            .with_verification_summary(summary)
            .add_evidence(ev)
            .qualify_for_deployment()
        )

        artifact = builder.build()

        assert artifact.project_id == "proj-builder-test"
        assert artifact.artifact_type == ProductArtifactType.CONTAINER
        assert artifact.version == "2.4.0"
        assert artifact.is_deployable is True
        assert len(artifact.evidence) == 1
        assert artifact.build_metadata["base_image"] == "distroless/python3"
        assert artifact.trace["builder"] == "ProductArtifactBuilder"

        assessment = artifact.assess_deployability()
        assert assessment.status == DeployabilityStatus.QUALIFIED
        assert assessment.is_deployable is True

    def test_12_cross_validation_with_delivery_package(self) -> None:
        """Verify cross-validation between ProductArtifact and DeliveryPackage."""
        wo_id = new_work_order_id()
        exec_id = new_execution_id()
        base_rev = make_valid_revision(commit_hash="3" * 40)
        res_rev = make_valid_revision(commit_hash="4" * 40)
        foreign_rev = make_valid_revision(commit_hash="5" * 40)

        ev = make_valid_evidence(exec_id, wo_id)
        delivery = DeliveryPackage(
            execution_id=exec_id,
            work_order_id=wo_id,
            base_revision=base_rev,
            resulting_revision=res_rev,
            evidence=[ev],
            metadata={"project_id": "proj-delivery-test"},
            recommended_disposition=ManagerDisposition.ACCEPT,
        )

        # Artifact referencing delivery resulting_revision passes
        art = ProductArtifact(
            project_id="proj-delivery-test",
            work_order_id=wo_id,
            execution_id=exec_id,
            source_revision=res_rev,
            artifact_reference="dist/app.whl",
            delivery_reference=delivery.delivery_id,
        )
        art.validate(delivery_package=delivery)

        # Artifact referencing foreign revision fails
        bad_art = ProductArtifact(
            project_id="proj-delivery-test",
            work_order_id=wo_id,
            execution_id=exec_id,
            source_revision=foreign_rev,
            artifact_reference="dist/app.whl",
        )
        with pytest.raises(NonexistentRevisionError) as exc_info:
            bad_art.validate(delivery_package=delivery)
        assert "does not exist in delivery package" in str(exc_info.value)
