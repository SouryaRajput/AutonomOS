from enum import Enum


class ModelCapability(str, Enum):
    """Normalized model capabilities requested by workers or exposed by models."""
    TEXT_GENERATION = "TEXT_GENERATION"
    CODE_GENERATION = "CODE_GENERATION"
    VISION = "VISION"
    AUDIO_INPUT = "AUDIO_INPUT"
    AUDIO_OUTPUT = "AUDIO_OUTPUT"
    TOOL_CALLING = "TOOL_CALLING"
    STRUCTURED_OUTPUT = "STRUCTURED_OUTPUT"
    LONG_CONTEXT = "LONG_CONTEXT"
    REASONING = "REASONING"
    STREAMING = "STREAMING"


class ProviderHealthStatus(str, Enum):
    """Health and availability status of an inference provider."""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class RoutingProfile(str, Enum):
    """Configurable routing preferences for model and provider selection."""
    CHEAPEST = "CHEAPEST"
    FASTEST = "FASTEST"
    BEST_AVAILABLE = "BEST_AVAILABLE"
    FREE_FIRST = "FREE_FIRST"
    QUALITY_FIRST = "QUALITY_FIRST"
    CODE = "CODE"
    VISION = "VISION"
    LONG_CONTEXT = "LONG_CONTEXT"


class InferenceRequestStatus(str, Enum):
    """Lifecycle state of an inference gateway request."""
    CREATED = "CREATED"
    ROUTING = "ROUTING"
    ROUTED = "ROUTED"
    STARTED = "STARTED"
    STREAMING = "STREAMING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    RATE_LIMITED = "RATE_LIMITED"
    NO_PROVIDER_AVAILABLE = "NO_PROVIDER_AVAILABLE"


class CostType(str, Enum):
    """Precision tier for inference cost tracking."""
    ACTUAL = "ACTUAL"
    ESTIMATED = "ESTIMATED"
    UNKNOWN = "UNKNOWN"


class InferenceErrorCode(str, Enum):
    """Normalized provider-agnostic error codes."""
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    INVALID_REQUEST = "INVALID_REQUEST"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    CONTENT_POLICY = "CONTENT_POLICY"
    SERVER_ERROR = "SERVER_ERROR"
    NO_PROVIDER_AVAILABLE = "NO_PROVIDER_AVAILABLE"
    COST_LIMIT_EXCEEDED = "COST_LIMIT_EXCEEDED"
    UNKNOWN = "UNKNOWN"
