from __future__ import annotations

from typing import Any, Optional

from core.errors import AutonomOSError


class ProgrammerError(AutonomOSError):
    """Base exception for all Programmer subsystem errors."""

    def __init__(
        self,
        message: str,
        code: str = "PROGRAMMER_ERROR",
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message=message, code=code, details=details)


class InvalidProgrammerIdError(ProgrammerError):
    """Raised when an identifier does not conform to the Programmer subsystem prefix or format."""

    def __init__(self, identifier_type: str, identifier_value: str, expected_prefix: str):
        super().__init__(
            message=f"Invalid {identifier_type} identifier '{identifier_value}'. Expected prefix '{expected_prefix}'.",
            code="INVALID_PROGRAMMER_ID",
            details={
                "identifier_type": identifier_type,
                "identifier_value": identifier_value,
                "expected_prefix": expected_prefix,
            },
        )
        self.identifier_type = identifier_type
        self.identifier_value = identifier_value
        self.expected_prefix = expected_prefix


class ProgrammerLineageError(ProgrammerError):
    """Raised when an entity's parent lineage or correlation reference is missing, broken, or tampered with."""

    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(
            message=message,
            code="PROGRAMMER_LINEAGE_ERROR",
            details=details,
        )


class InvalidProgrammerTransitionError(ProgrammerError):
    """Raised when an illegal lifecycle transition is attempted on a Programmer execution or work order."""

    def __init__(
        self,
        entity_id: str,
        current_status: str,
        target_status: str,
        reason: str = "",
    ):
        msg = f"Cannot transition '{entity_id}' from '{current_status}' to '{target_status}'"
        if reason:
            msg += f": {reason}"
        super().__init__(
            message=msg,
            code="INVALID_PROGRAMMER_TRANSITION",
            details={
                "entity_id": entity_id,
                "current_status": current_status,
                "target_status": target_status,
                "reason": reason,
            },
        )
        self.entity_id = entity_id
        self.current_status = current_status
        self.target_status = target_status


class ProgrammerBlockerError(ProgrammerError):
    """Raised when a material blocker prevents execution from proceeding, requiring escalation to Manager."""

    def __init__(self, work_order_id: str, blocker: str, details: Optional[dict[str, Any]] = None):
        d = {"work_order_id": work_order_id, "blocker": blocker}
        if details:
            d.update(details)
        super().__init__(
            message=f"Execution blocked for work order '{work_order_id}': {blocker}",
            code="PROGRAMMER_MATERIAL_BLOCKER",
            details=d,
        )
        self.work_order_id = work_order_id
        self.blocker = blocker


class ProgrammerValidationError(ProgrammerError):
    """Raised when a Programmer contract fails schema, boundary, or sanity validation."""

    def __init__(
        self,
        message: str,
        field_name: Optional[str] = None,
        code: str = "PROGRAMMER_VALIDATION_ERROR",
        details: Optional[dict[str, Any]] = None,
    ):
        d = dict(details or {})
        if field_name:
            d["field_name"] = field_name
        super().__init__(message=message, code=code, details=d)
        self.field_name = field_name


class InvalidPathScopeError(ProgrammerValidationError):
    """Raised when path scopes are malformed, contradictory, or violate confinement policies."""

    def __init__(self, message: str, path: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        d = dict(details or {})
        if path:
            d["path"] = path
        super().__init__(
            message=message,
            field_name="paths",
            code="INVALID_PATH_SCOPE",
            details=d,
        )
        self.path = path


class InvalidCommandScopeError(ProgrammerValidationError):
    """Raised when allowed commands are malformed or invalid."""

    def __init__(self, message: str, command: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        d = dict(details or {})
        if command:
            d["command"] = command
        super().__init__(
            message=message,
            field_name="allowed_commands",
            code="INVALID_COMMAND_SCOPE",
            details=d,
        )
        self.command = command


class InvalidBudgetError(ProgrammerValidationError):
    """Raised when execution budgets are non-positive or malformed."""

    def __init__(self, message: str, budget_type: Optional[str] = None, value: Any = None, details: Optional[dict[str, Any]] = None):
        d = dict(details or {})
        if budget_type:
            d["budget_type"] = budget_type
        if value is not None:
            d["value"] = value
        super().__init__(
            message=message,
            field_name="budgets",
            code="INVALID_BUDGET",
            details=d,
        )
        self.budget_type = budget_type
        self.value = value


class InvalidWorkspaceError(ProgrammerValidationError):
    """Raised when a workspace fails boundary, ownership, or isolation validation."""

    def __init__(self, message: str, workspace_id: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        d = dict(details or {})
        if workspace_id:
            d["workspace_id"] = workspace_id
        super().__init__(
            message=message,
            field_name="workspace",
            code="INVALID_WORKSPACE",
            details=d,
        )
        self.workspace_id = workspace_id


class WorkspaceProvisioningError(ProgrammerError):
    """Raised when workspace provisioning fails deterministically."""

    def __init__(
        self,
        message: str,
        error_code: str = "UNKNOWN",
        workspace_id: Optional[str] = None,
        project_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        d = dict(details or {})
        code_str = error_code.value if hasattr(error_code, "value") else str(error_code)
        d["error_code"] = code_str
        if workspace_id:
            d["workspace_id"] = workspace_id
        if project_id:
            d["project_id"] = project_id
        super().__init__(
            message=message,
            code=f"WORKSPACE_PROVISIONING_{code_str}",
            details=d,
        )
        self.error_code = error_code
        self.workspace_id = workspace_id
        self.project_id = project_id


class GitWorktreeError(ProgrammerError):
    """Raised when Git worktree provisioning, lifecycle transition, or cleanup fails."""

    def __init__(
        self,
        message: str,
        error_code: str = "UNKNOWN",
        worktree_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        repository_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        d = dict(details or {})
        code_str = error_code.value if hasattr(error_code, "value") else str(error_code)
        d["error_code"] = code_str
        if worktree_id:
            d["worktree_id"] = worktree_id
        if execution_id:
            d["execution_id"] = execution_id
        if repository_id:
            d["repository_id"] = repository_id
        super().__init__(
            message=message,
            code=f"GIT_WORKTREE_{code_str}",
            details=d,
        )
        self.error_code = error_code
        self.worktree_id = worktree_id
        self.execution_id = execution_id
        self.repository_id = repository_id


class MissingResearchEvidenceError(ProgrammerValidationError):
    """Raised when an explicit research evidence reference requested by Manager cannot be found in the research result."""

    def __init__(self, evidence_id: str, message: Optional[str] = None):
        msg = message or f"Research evidence '{evidence_id}' was not found in research result."
        super().__init__(
            message=msg,
            field_name="research_evidence",
            code="MISSING_RESEARCH_EVIDENCE",
            details={"evidence_id": evidence_id},
        )
        self.evidence_id = evidence_id


class ConflictingResearchError(ProgrammerValidationError):
    """Raised when contradictory research findings are detected and lack explicit Manager resolution."""

    def __init__(self, message: str, contradiction_id: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        d = dict(details or {})
        if contradiction_id:
            d["contradiction_id"] = contradiction_id
        super().__init__(
            message=message,
            field_name="research_contradictions",
            code="CONFLICTING_RESEARCH_FINDINGS",
            details=d,
        )
        self.contradiction_id = contradiction_id


class MissingImplementationEvidenceError(ProgrammerValidationError):
    """Raised when an API endpoint or capability claimed in a designer handoff lacks supporting code or evidence."""

    def __init__(self, message: str, endpoint_ref: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        d = dict(details or {})
        if endpoint_ref:
            d["endpoint_ref"] = endpoint_ref
        super().__init__(
            message=message,
            field_name="endpoints",
            code="MISSING_IMPLEMENTATION_EVIDENCE",
            details=d,
        )
        self.endpoint_ref = endpoint_ref


class UnauthorizedUXPrescriptionError(ProgrammerValidationError):
    """Raised when Programmer attempts to mandate visual/UX styling decisions without Manager authorization."""

    def __init__(self, message: str, prescription: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        d = dict(details or {})
        if prescription:
            d["prescription"] = prescription
        super().__init__(
            message=message,
            field_name="ux_prescription",
            code="UNAUTHORIZED_UX_PRESCRIPTION",
            details=d,
        )
        self.prescription = prescription


class MissingVerificationEvidenceError(ProgrammerValidationError):
    """Raised when a verification result or changed API references an evidence ID missing from the handoff."""

    def __init__(self, evidence_id: str, message: Optional[str] = None):
        msg = message or f"Verification evidence '{evidence_id}' referenced in handoff is missing from evidence records."
        super().__init__(
            message=msg,
            field_name="verification_evidence",
            code="MISSING_VERIFICATION_EVIDENCE",
            details={"evidence_id": evidence_id},
        )
        self.evidence_id = evidence_id


class UnauthorizedQAClaimError(ProgrammerValidationError):
    """Raised when Programmer attempts to declare independent QA unnecessary or marks Tester tasks complete."""

    def __init__(self, message: str, claim: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        d = dict(details or {})
        if claim:
            d["claim"] = claim
        super().__init__(
            message=message,
            field_name="qa_authority",
            code="UNAUTHORIZED_QA_CLAIM",
            details=d,
        )
        self.claim = claim


class UnrelatedFeedbackError(ProgrammerValidationError):
    """Raised when feedback targets an unrelated project or mismatched work order."""

    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(
            message=message,
            field_name="target_project",
            code="UNRELATED_FEEDBACK_ERROR",
            details=details,
        )


class StaleFeedbackError(ProgrammerValidationError):
    """Raised when feedback references an obsolete or superseded work order revision."""

    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(
            message=message,
            field_name="related_work_order",
            code="STALE_FEEDBACK_ERROR",
            details=details,
        )


class DuplicateFeedbackError(ProgrammerValidationError):
    """Raised when duplicate feedback submissions are detected."""

    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(
            message=message,
            field_name="feedback_id",
            code="DUPLICATE_FEEDBACK_ERROR",
            details=details,
        )


class ProductArtifactError(ProgrammerError):
    """Base exception for all ProductArtifact-related errors."""

    def __init__(self, message: str, code: str = "PRODUCT_ARTIFACT_ERROR", details: Optional[dict[str, Any]] = None):
        super().__init__(message=message, code=code, details=details)


class InvalidArtifactReferenceError(ProgrammerValidationError):
    """Raised when artifact reference is missing, empty, or malformed."""

    def __init__(self, message: str, artifact_reference: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        d = dict(details or {})
        if artifact_reference is not None:
            d["artifact_reference"] = artifact_reference
        super().__init__(
            message=message,
            field_name="artifact_reference",
            code="INVALID_ARTIFACT_REFERENCE",
            details=d,
        )
        self.artifact_reference = artifact_reference


class InvalidArtifactVersionError(ProgrammerValidationError):
    """Raised when version string is missing, invalid, or malformed."""

    def __init__(self, message: str, version: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        d = dict(details or {})
        if version is not None:
            d["version"] = version
        super().__init__(
            message=message,
            field_name="version",
            code="INVALID_ARTIFACT_VERSION",
            details=d,
        )
        self.version = version


class NonexistentRevisionError(ProgrammerLineageError):
    """Raised when an artifact references a git revision that does not exist or does not match execution context / repository lineage."""

    def __init__(self, message: str, commit_hash: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        d = dict(details or {})
        if commit_hash is not None:
            d["commit_hash"] = commit_hash
        super().__init__(
            message=message,
            details=d,
        )
        self.commit_hash = commit_hash


class UnauthorizedDeployabilityClaimError(ProgrammerValidationError):
    """Raised when an artifact claims deployability without meeting eligibility criteria or authoritative verification evidence."""

    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(
            message=message,
            field_name="is_deployable",
            code="UNAUTHORIZED_DEPLOYABILITY_CLAIM",
            details=details,
        )


class ArtifactLineageError(ProgrammerLineageError):
    """Raised when an artifact's project, execution, or work order ownership does not match associated entities."""

    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(
            message=message,
            details=details,
        )


class BuildPackagingError(ProgrammerError):
    """Base exception for all build and packaging errors."""

    def __init__(self, message: str, code: str = "BUILD_PACKAGING_ERROR", details: Optional[dict[str, Any]] = None):
        super().__init__(message=message, code=code, details=details)


class MissingBuildCommandError(ProgrammerValidationError):
    """Raised when no build command is specified in the request or configured in the work order."""

    def __init__(self, message: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        msg = message or "No build command specified or configured in the work order."
        super().__init__(
            message=msg,
            field_name="build_command",
            code="MISSING_BUILD_COMMAND",
            details=details,
        )


class UnauthorizedBuildCommandError(ProgrammerValidationError):
    """Raised when the requested build command is not authorized by the work order command policy."""

    def __init__(self, message: str, build_command: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        d = dict(details or {})
        if build_command is not None:
            d["build_command"] = build_command
        super().__init__(
            message=message,
            field_name="build_command",
            code="UNAUTHORIZED_BUILD_COMMAND",
            details=d,
        )
        self.build_command = build_command


class SourceRevisionMismatchError(NonexistentRevisionError):
    """Raised when the source revision specified for building does not match the actual execution context revision."""

    def __init__(self, message: str, commit_hash: Optional[str] = None, expected_hash: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        d = dict(details or {})
        if expected_hash is not None:
            d["expected_hash"] = expected_hash
        super().__init__(
            message=message,
            commit_hash=commit_hash,
            details=d,
        )
        self.expected_hash = expected_hash


class MissingArtifactError(ProgrammerValidationError):
    """Raised when a build command exits 0 but the declared output artifact file is missing on disk."""

    def __init__(self, message: str, artifact_reference: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        d = dict(details or {})
        if artifact_reference is not None:
            d["artifact_reference"] = artifact_reference
        super().__init__(
            message=message,
            field_name="artifact_reference",
            code="MISSING_ARTIFACT",
            details=d,
        )
        self.artifact_reference = artifact_reference


class DependencyAuthorizationError(ProgrammerValidationError):
    """Raised when dependency installation is required or attempted without explicit Manager authorization."""

    def __init__(self, message: str, dependency_command: Optional[str] = None, escalation_id: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        d = dict(details or {})
        if dependency_command is not None:
            d["dependency_command"] = dependency_command
        if escalation_id is not None:
            d["escalation_id"] = escalation_id
        super().__init__(
            message=message,
            field_name="dependencies",
            code="UNAUTHORIZED_DEPENDENCY_INSTALLATION",
            details=d,
        )
        self.dependency_command = dependency_command
        self.escalation_id = escalation_id


class ConfigurationError(ProgrammerError):
    """Base exception for all configuration-related errors."""

    def __init__(self, message: str, code: str = "CONFIGURATION_ERROR", details: Optional[dict[str, Any]] = None):
        super().__init__(message=message, code=code, details=details)


class ConfigurationValidationError(ProgrammerValidationError, ConfigurationError):
    """Raised when a configuration fails validation against a schema or safety rules."""

    def __init__(
        self,
        message: str,
        field_name: Optional[str] = None,
        code: str = "CONFIGURATION_VALIDATION_ERROR",
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message=message, field_name=field_name, code=code, details=details)


class MissingRequiredConfigurationError(ConfigurationValidationError):
    """Raised when a required configuration field is missing."""

    def __init__(self, message: str, field_name: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        super().__init__(
            message=message,
            field_name=field_name,
            code="MISSING_REQUIRED_CONFIGURATION",
            details=details,
        )


class InvalidConfigurationTypeError(ConfigurationValidationError):
    """Raised when a configuration field value does not match the expected type."""

    def __init__(
        self,
        message: str,
        field_name: Optional[str] = None,
        expected_type: Optional[str] = None,
        actual_type: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        d = dict(details or {})
        if expected_type is not None:
            d["expected_type"] = expected_type
        if actual_type is not None:
            d["actual_type"] = actual_type
        super().__init__(
            message=message,
            field_name=field_name,
            code="INVALID_CONFIGURATION_TYPE",
            details=d,
        )
        self.expected_type = expected_type
        self.actual_type = actual_type


class InvalidConfigurationValueError(ConfigurationValidationError):
    """Raised when a configuration value violates value constraints (range, enum allowed values, pattern)."""

    def __init__(
        self,
        message: str,
        field_name: Optional[str] = None,
        value: Any = None,
        constraint: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        d = dict(details or {})
        if constraint is not None:
            d["constraint"] = constraint
        super().__init__(
            message=message,
            field_name=field_name,
            code="INVALID_CONFIGURATION_VALUE",
            details=d,
        )
        self.value = value
        self.constraint = constraint


class SchemaVersionMismatchError(ConfigurationValidationError):
    """Raised when a configuration references a schema version incompatible with the target schema."""

    def __init__(
        self,
        message: str,
        expected_version: Optional[str] = None,
        actual_version: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        d = dict(details or {})
        if expected_version is not None:
            d["expected_version"] = expected_version
        if actual_version is not None:
            d["actual_version"] = actual_version
        super().__init__(
            message=message,
            field_name="schema_version",
            code="SCHEMA_VERSION_MISMATCH",
            details=d,
        )
        self.expected_version = expected_version
        self.actual_version = actual_version


class MaliciousConfigurationError(ConfigurationValidationError):
    """Raised when configuration values contain dangerous patterns (shell injection, code execution, path traversal)."""

    def __init__(
        self,
        message: str,
        field_name: Optional[str] = None,
        detected_pattern: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        d = dict(details or {})
        if detected_pattern is not None:
            d["detected_pattern"] = detected_pattern
        super().__init__(
            message=message,
            field_name=field_name,
            code="MALICIOUS_CONFIGURATION",
            details=d,
        )
        self.detected_pattern = detected_pattern


class PlaintextSensitiveValueError(ConfigurationValidationError):
    """Raised when a sensitive configuration field contains raw plaintext instead of a secret reference."""

    def __init__(
        self,
        message: str,
        field_name: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            field_name=field_name,
            code="PLAINTEXT_SENSITIVE_VALUE",
            details=details,
        )


class FieldDependencyViolationError(ConfigurationValidationError):
    """Raised when inter-field dependency rules are violated."""

    def __init__(
        self,
        message: str,
        source_field: Optional[str] = None,
        target_field: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        d = dict(details or {})
        if source_field is not None:
            d["source_field"] = source_field
        if target_field is not None:
            d["target_field"] = target_field
        super().__init__(
            message=message,
            field_name=source_field,
            code="FIELD_DEPENDENCY_VIOLATION",
            details=d,
        )
        self.source_field = source_field
        self.target_field = target_field


class ImmutableConfigurationModificationError(ConfigurationValidationError):
    """Raised when attempting to modify an immutable configuration field."""

    def __init__(
        self,
        message: str,
        field_name: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            field_name=field_name,
            code="IMMUTABLE_CONFIGURATION_MODIFICATION",
            details=details,
        )


class EnvironmentRequirementError(ProgrammerError):
    """Base exception for all environment requirement errors."""

    def __init__(self, message: str, code: str = "ENVIRONMENT_REQUIREMENT_ERROR", details: Optional[dict[str, Any]] = None):
        super().__init__(message=message, code=code, details=details)


class SecretLeakageError(ProgrammerValidationError, ConfigurationError):
    """Raised when raw production secrets or credentials are found in prompts, Git, results, evidence, or logs."""

    def __init__(
        self,
        message: str,
        secret_type: Optional[str] = None,
        location: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        d = dict(details or {})
        if secret_type is not None:
            d["secret_type"] = secret_type
        if location is not None:
            d["location"] = location
        super().__init__(
            message=message,
            field_name="secrets",
            code="SECRET_LEAKAGE_DETECTED",
            details=d,
        )
        self.secret_type = secret_type
        self.location = location


class SecretReferenceUnavailableError(ConfigurationValidationError):
    """Raised when a required secret reference placeholder cannot be resolved or is absent from available secret references."""

    def __init__(
        self,
        message: str,
        secret_reference: Optional[str] = None,
        requirement_name: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        d = dict(details or {})
        if secret_reference is not None:
            d["secret_reference"] = secret_reference
        if requirement_name is not None:
            d["requirement_name"] = requirement_name
        super().__init__(
            message=message,
            field_name=requirement_name,
            code="SECRET_REFERENCE_UNAVAILABLE",
            details=d,
        )
        self.secret_reference = secret_reference
        self.requirement_name = requirement_name


class MissingEnvironmentRequirementError(ProgrammerValidationError):
    """Raised when a required environment variable or parameter is missing from the target environment."""

    def __init__(
        self,
        message: str,
        requirement_name: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            field_name=requirement_name,
            code="MISSING_ENVIRONMENT_REQUIREMENT",
            details=details,
        )
        self.requirement_name = requirement_name


class DeploymentHandoffError(ProgrammerValidationError):
    """Base exception for product deployment handoff contract violations."""

    def __init__(
        self,
        message: str,
        handoff_id: Optional[str] = None,
        field_name: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        d = dict(details or {})
        if handoff_id is not None:
            d["handoff_id"] = handoff_id
        super().__init__(
            message=message,
            field_name=field_name or "deployment_handoff",
            code="DEPLOYMENT_HANDOFF_ERROR",
            details=d,
        )
        self.handoff_id = handoff_id


class PrematureDeploymentClaimError(DeploymentHandoffError):
    """
    Raised when Programmer attempts to claim lifecycle_state == DEPLOYED or asserts
    that deployment has already executed. Programmer cannot autonomously deploy.
    """

    def __init__(
        self,
        message: str = "Programmer cannot claim artifact is DEPLOYED; deployment authority rests exclusively with future deployment/operations layer under Manager authority.",
        handoff_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            handoff_id=handoff_id,
            field_name="lifecycle_state",
            details=details,
        )


class MissingPrerequisiteError(DeploymentHandoffError):
    """Raised when mandatory deployment prerequisites are missing or unfulfilled."""

    def __init__(
        self,
        message: str,
        missing_prerequisites: Optional[Sequence[str]] = None,
        handoff_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        d = dict(details or {})
        if missing_prerequisites is not None:
            d["missing_prerequisites"] = list(missing_prerequisites)
        super().__init__(
            message=message,
            handoff_id=handoff_id,
            field_name="deployment_prerequisites",
            details=d,
        )
        self.missing_prerequisites = list(missing_prerequisites or [])








