from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional, Union
import uuid

from core.research.structured.auth import CredentialReference
from core.research.structured.models import (
    HttpMethod,
    PaginationConfig,
    StructuredDataLimits,
    StructuredDataRequest,
)
from core.research.structured.policy import StructuredDataSecurityPolicy

if TYPE_CHECKING:
    from core.research.contracts.crawler_task import CrawlerTask


class StructuredDataRequestBuilder:
    """
    Fluent builder for StructuredDataRequest instances that enforces security policies.
    Guarantees that untrusted research input cannot execute unvalidated or dangerous requests.
    """

    def __init__(self, policy: Optional[StructuredDataSecurityPolicy] = None):
        self.policy: StructuredDataSecurityPolicy = policy or StructuredDataSecurityPolicy()
        self._request_id: Optional[str] = None
        self._endpoint_url: Optional[str] = None
        self._method: HttpMethod = HttpMethod.GET
        self._query_params: dict[str, Any] = {}
        self._headers: dict[str, str] = {}
        self._body: Optional[Union[str, bytes, dict[str, Any]]] = None
        self._body_content_type: Optional[str] = None
        self._requested_fields: list[str] = []
        self._filters: dict[str, Any] = {}
        self._pagination: Optional[PaginationConfig] = None
        self._limits: Optional[StructuredDataLimits] = None
        self._credential_ref: Optional[CredentialReference] = None
        self._metadata: dict[str, Any] = {}

    def with_credential_ref(self, credential_ref: Optional[CredentialReference]) -> StructuredDataRequestBuilder:
        self._credential_ref = credential_ref
        return self

    def with_request_id(self, request_id: str) -> StructuredDataRequestBuilder:
        self._request_id = request_id
        return self

    def with_endpoint(self, endpoint_url: str) -> StructuredDataRequestBuilder:
        self._endpoint_url = endpoint_url
        return self

    def with_method(self, method: str | HttpMethod) -> StructuredDataRequestBuilder:
        if isinstance(method, HttpMethod):
            self._method = method
        elif isinstance(method, str):
            self._method = HttpMethod.from_string(method)
        else:
            self._method = method
        return self

    def with_query_param(self, key: str, value: Any) -> StructuredDataRequestBuilder:
        self._query_params[key] = value
        return self

    def with_query_params(self, params: dict[str, Any]) -> StructuredDataRequestBuilder:
        self._query_params.update(params)
        return self

    def with_header(self, key: str, value: str) -> StructuredDataRequestBuilder:
        self._headers[key] = value
        return self

    def with_headers(self, headers: dict[str, str]) -> StructuredDataRequestBuilder:
        self._headers.update(headers)
        return self

    def with_body(
        self,
        body: Any,
        content_type: Optional[str] = None,
    ) -> StructuredDataRequestBuilder:
        self._body = body
        if content_type:
            self._body_content_type = content_type
        return self

    def with_requested_fields(self, fields: list[str]) -> StructuredDataRequestBuilder:
        self._requested_fields = list(fields)
        return self

    def with_filters(self, filters: dict[str, Any]) -> StructuredDataRequestBuilder:
        self._filters = dict(filters)
        return self

    def with_pagination(self, pagination: PaginationConfig) -> StructuredDataRequestBuilder:
        self._pagination = pagination
        return self

    def with_limits(self, limits: StructuredDataLimits) -> StructuredDataRequestBuilder:
        self._limits = limits
        return self

    def with_metadata(self, metadata: dict[str, Any]) -> StructuredDataRequestBuilder:
        self._metadata.update(metadata)
        return self

    def build(self) -> StructuredDataRequest:
        """
        Validate all accumulated request parameters against the active security policy
        and produce an immutable, fully validated StructuredDataRequest.
        """
        if not self._endpoint_url:
            raise self.policy.validate_endpoint("")

        # 1. Validate endpoint URL and SSRF bounds
        validated_endpoint = self.policy.validate_endpoint(self._endpoint_url)

        # 2. Validate HTTP method
        validated_method = self.policy.validate_method(self._method)

        # 3. Validate query parameters
        validated_query_params = self.policy.validate_query_params(self._query_params)

        # 4. Validate headers
        validated_headers = self.policy.validate_headers(self._headers)

        # 5. Validate body & content type
        effective_content_type = self._body_content_type or validated_headers.get("content-type")
        validated_body = self.policy.validate_body(
            self._body,
            content_type=effective_content_type,
        )

        # 6. Validate resource limits
        effective_limits = self._limits or StructuredDataLimits(
            timeout_seconds=self.policy.default_timeout_seconds,
            max_bytes=min(10_000_000, self.policy.max_decompressed_bytes),
        )
        validated_limits = self.policy.validate_limits(effective_limits)

        # 7. Generate or validate request_id
        req_id = self._request_id.strip() if (self._request_id and self._request_id.strip()) else f"sreq_{uuid.uuid4().hex[:12]}"

        return StructuredDataRequest(
            request_id=req_id,
            endpoint_url=validated_endpoint,
            method=validated_method,
            query_params=validated_query_params,
            headers=validated_headers,
            body=validated_body,
            requested_fields=list(self._requested_fields),
            filters=dict(self._filters),
            pagination=self._pagination,
            limits=validated_limits,
            credential_ref=self._credential_ref,
            metadata=dict(self._metadata),
        )

    # -------------------------------------------------------------------------
    # Factories
    # -------------------------------------------------------------------------

    @classmethod
    def from_task(
        cls,
        task: CrawlerTask,
        policy: Optional[StructuredDataSecurityPolicy] = None,
    ) -> StructuredDataRequestBuilder:
        """
        Create a builder pre-populated from a CrawlerTask.
        Treats all task parameters as untrusted inputs.
        """
        builder = cls(policy=policy)
        params = task.parameters or {}

        # Resolve endpoint from parameters or fallback to query_or_target
        endpoint = (
            params.get("endpoint_url")
            or params.get("target_url")
            or params.get("url")
            or task.query_or_target
        )
        if endpoint:
            endpoint_str = str(endpoint).strip()
            # If endpoint is not already a clean URL directly, attempt to extract a URL embedded in text
            if not (endpoint_str.startswith("http://") or endpoint_str.startswith("https://") or endpoint_str.startswith("mock://")):
                import re
                match = re.search(r'(https?://[^\s]+|mock://[^\s]+)', endpoint_str)
                if match:
                    endpoint_str = match.group(1).rstrip('.,;)"\'?')
            builder.with_endpoint(endpoint_str)

        builder.with_request_id(task.task_id)

        if "method" in params:
            builder.with_method(params["method"])
        if "query_params" in params and isinstance(params["query_params"], dict):
            builder.with_query_params(params["query_params"])
        if "headers" in params and isinstance(params["headers"], dict):
            builder.with_headers(params["headers"])
        if "body" in params:
            builder.with_body(params["body"], content_type=params.get("content_type"))
        if "requested_fields" in params and isinstance(params["requested_fields"], list):
            builder.with_requested_fields(params["requested_fields"])
        if "filters" in params and isinstance(params["filters"], dict):
            builder.with_filters(params["filters"])
        if "pagination" in params:
            p_val = params["pagination"]
            if isinstance(p_val, PaginationConfig):
                builder.with_pagination(p_val)
            elif isinstance(p_val, dict):
                builder.with_pagination(PaginationConfig.from_dict(p_val))
        if "credential_ref" in params:
            c_val = params["credential_ref"]
            if isinstance(c_val, CredentialReference):
                builder.with_credential_ref(c_val)
            elif isinstance(c_val, dict):
                builder.with_credential_ref(CredentialReference.from_dict(c_val))

        # Limits resolution
        limits_data = params.get("limits")
        if isinstance(limits_data, StructuredDataLimits):
            builder.with_limits(limits_data)
        elif isinstance(limits_data, dict):
            builder.with_limits(StructuredDataLimits.from_dict(limits_data))
        elif task.timeout_seconds:
            max_t = policy.max_timeout_seconds if policy else 60.0
            builder.with_limits(StructuredDataLimits(timeout_seconds=min(float(task.timeout_seconds), max_t)))

        meta = {
            "task_id": task.task_id,
            "plan_id": task.plan_id,
            "question_id": task.question_id,
            "correlation_id": task.correlation_id,
            **task.metadata,
        }
        builder.with_metadata(meta)
        return builder

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        policy: Optional[StructuredDataSecurityPolicy] = None,
    ) -> StructuredDataRequestBuilder:
        """Create a builder pre-populated from a dictionary."""
        builder = cls(policy=policy)
        if "request_id" in data:
            builder.with_request_id(data["request_id"])
        if "endpoint_url" in data:
            builder.with_endpoint(data["endpoint_url"])
        if "method" in data:
            builder.with_method(data["method"])
        if "query_params" in data and isinstance(data["query_params"], dict):
            builder.with_query_params(data["query_params"])
        if "headers" in data and isinstance(data["headers"], dict):
            builder.with_headers(data["headers"])
        if "body" in data:
            builder.with_body(data["body"], content_type=data.get("content_type"))
        if "requested_fields" in data and isinstance(data["requested_fields"], list):
            builder.with_requested_fields(data["requested_fields"])
        if "filters" in data and isinstance(data["filters"], dict):
            builder.with_filters(data["filters"])
        if "pagination" in data:
            p_val = data["pagination"]
            if isinstance(p_val, PaginationConfig):
                builder.with_pagination(p_val)
            elif isinstance(p_val, dict):
                builder.with_pagination(PaginationConfig.from_dict(p_val))
        if "credential_ref" in data:
            c_val = data["credential_ref"]
            if isinstance(c_val, CredentialReference):
                builder.with_credential_ref(c_val)
            elif isinstance(c_val, dict):
                builder.with_credential_ref(CredentialReference.from_dict(c_val))
        if "limits" in data:
            l_val = data["limits"]
            if isinstance(l_val, StructuredDataLimits):
                builder.with_limits(l_val)
            elif isinstance(l_val, dict):
                builder.with_limits(StructuredDataLimits.from_dict(l_val))
        if "metadata" in data and isinstance(data["metadata"], dict):
            builder.with_metadata(data["metadata"])
        return builder
