"""
Structured Data Crawler Implementation (Phase 1 / Part 7 / Step 8).

Specialized crawler worker for discovering, querying, retrieving, and extracting
lineage-preserving structured evidence from machine-readable data sources (REST APIs,
JSON, JSONL, XML, CSV, GraphQL endpoints). Integrates with the standard AutonomOS
Worker and Crawler runtime.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Callable, Optional, Union
import urllib.parse
import uuid

from core.inference.secrets import EnvSecretStore, SecretStore
from core.models import Task, WorkerManifest, WorkerOutput, utc_now
from core.research.contracts.crawler_report import CrawlerReport, RawSourceReference
from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.evidence import EvidenceItem, EvidenceProvenance
from core.research.crawler.base import BaseCrawler
from core.research.errors import (
    CrawlerExecutionError,
    StructuredDataAuthenticationError,
    StructuredDataAuthorizationError,
    StructuredDataCancelledError,
    StructuredDataError,
    StructuredDataLimitError,
    StructuredDataMalformedResponseError,
    StructuredDataPolicyViolationError,
    StructuredDataProviderError,
    StructuredDataQuotaExceededError,
    StructuredDataRateLimitError,
    StructuredDataSecurityError,
    StructuredDataTimeoutError,
    StructuredDataValidationError,
)
from core.research.structured.auth import (
    CredentialRedactor,
    CredentialReference,
    CredentialResolver,
)
from core.research.structured.builder import StructuredDataRequestBuilder
from core.research.structured.fake_provider import FakeStructuredDataProvider
from core.research.structured.models import (
    HttpMethod,
    PaginationConfig,
    SourceLocation,
    StructuredContentType,
    StructuredDataLimits,
    StructuredDataRequest,
    StructuredDataResponse,
    StructuredRecord,
    canonical_json_dumps,
    compute_structured_hash,
)
from core.research.structured.paginator import (
    BoundedRetrievalLimits,
    PaginatedRetrievalResult,
    RetrievalStatus,
    StructuredDataPaginator,
)
from core.research.structured.policy import StructuredDataSecurityPolicy
from core.research.structured.provider import StructuredDataProvider
from core.research.structured.query import (
    LocalStructuredQueryEngine,
    RemoteQueryContract,
    RemoteQueryMapper,
    StructuredQuery,
)
from core.research.structured.rate_limiter import (
    RateLimitConfig,
    RateLimitedStructuredDataProvider,
)
from core.research.types import (
    CrawlerCapability,
    CrawlerHealthStatus,
    CrawlerReportStatus,
    CrawlerStatus,
    CrawlerTaskStatus,
    FactClassification,
    ResearchConfidence,
    SourceType,
)
from pkg.sdk.types import WorkerCapability
from pkg.sdk.worker import Worker, WorkerRuntimeContext

logger = logging.getLogger("AutonomOS.Worker.StructuredDataCrawler")


class StructuredDataCrawler(Worker, BaseCrawler):
    """
    Production Structured Data Crawler in AutonomOS.
    Executes safe, policy-bounded retrieval and extraction across machine-readable endpoints,
    producing cryptographically grounded CrawlerReports with complete provenance.
    """

    def __init__(
        self,
        crawler_id: Optional[str] = None,
        name: str = "StructuredDataCrawler",
        provider: Optional[StructuredDataProvider] = None,
        policy: Optional[StructuredDataSecurityPolicy] = None,
        secret_store: Optional[SecretStore] = None,
        rate_limit_config: Optional[RateLimitConfig] = None,
        retrieval_limits: Optional[BoundedRetrievalLimits] = None,
        capabilities: Optional[list[CrawlerCapability]] = None,
        version: str = "1.0.0",
    ):
        cid = crawler_id or f"crawler.structured.{uuid.uuid4().hex[:6]}"
        caps = capabilities or [
            CrawlerCapability.STRUCTURED_DATA_EXTRACTION,
            CrawlerCapability.API_QUERY,
        ]
        BaseCrawler.__init__(self, crawler_id=cid, capabilities=caps, name=name)

        self.policy = policy if policy is not None else StructuredDataSecurityPolicy(allow_mock=True)
        self.secret_store = secret_store or EnvSecretStore()
        self.provider = provider or FakeStructuredDataProvider(allow_localhost=self.policy.allow_localhost)
        self.rate_limit_config = rate_limit_config or RateLimitConfig()
        self.retrieval_limits = retrieval_limits or BoundedRetrievalLimits()
        self.redactor = CredentialRedactor(secret_store=self.secret_store)
        self._version = version

        self._manifest = WorkerManifest(
            id=self.crawler_id,
            name=self.name,
            role="Crawler",
            description="Specialist crawler executing bounded structured data extraction and API queries",
            version=self._version,
            capabilities=[
                WorkerCapability.RESEARCH.value,
                WorkerCapability.STRUCTURED_OUTPUT.value,
                "API_QUERY",
                "STRUCTURED_DATA_EXTRACTION",
                "SOURCE_COLLECTION",
                "EVIDENCE_EXTRACTION",
            ] + [c.value for c in self._capabilities],
            permissions=["api", "structured_data", "*"],
            created_at=utc_now(),
        )
        self.transition_to(CrawlerStatus.QUEUED, reason="StructuredDataCrawler initialized and queued")

    def get_manifest(self) -> WorkerManifest:
        return self._manifest

    # -------------------------------------------------------------------------
    # Execution Pipeline
    # -------------------------------------------------------------------------

    def execute_crawler_task(
        self,
        task: CrawlerTask,
        context: Optional[WorkerRuntimeContext] = None,
    ) -> CrawlerReport:
        """
        Execute an assigned structured data collection task within safety and policy bounds.
        Produces a CrawlerReport containing grounded RawSourceReferences and EvidenceItems.
        """
        start_time = time.monotonic()
        self.validate_task_compatibility(task)
        if self.status in (CrawlerStatus.COMPLETED, CrawlerStatus.FAILED, CrawlerStatus.CANCELLED):
            self.reset_status()
        self.transition_to(CrawlerStatus.RUNNING, reason=f"Executing task '{task.task_id}'")
        self.current_task = task

        def is_cancelled() -> bool:
            return (
                task.status == CrawlerTaskStatus.CANCELLED
                or (context is not None and getattr(context, "is_cancelled", lambda: False)())
            )

        # 1. Check for immediate cancellation
        if is_cancelled():
            self.tasks_failed += 1
            self.transition_to(CrawlerStatus.CANCELLED, reason="Task cancelled prior to execution")
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "structured_crawl_cancelled",
                        self.redactor.redact_dict({
                            "endpoint": self.redactor.redact_url(task.query_or_target),
                            "reason": "Task cancelled prior to execution",
                            "worker_state": CrawlerStatus.CANCELLED.value,
                        }),
                    )
                except Exception:
                    pass
            return self._build_cancelled_report(task, execution_time=time.monotonic() - start_time)

        if context and hasattr(context, "progress"):
            context.progress.report(5.0, f"Preparing structured data extraction for '{self.redactor.redact_url(task.query_or_target)}'")

        try:
            # 2. Build StructuredDataRequest via builder + policy
            try:
                builder = StructuredDataRequestBuilder.from_task(task, policy=self.policy)
                request = builder.build()
            except (StructuredDataSecurityError, StructuredDataPolicyViolationError) as sec_err:
                logger.warning(f"Security policy rejected request for task '{task.task_id}': {sec_err}")
                self.tasks_failed += 1
                self.transition_to(CrawlerStatus.FAILED, reason=f"Security policy rejection: {sec_err}")
                if context and hasattr(context, "events"):
                    try:
                        context.events.emit(
                            "structured_crawl_failed",
                            self.redactor.redact_dict({
                                "endpoint": self.redactor.redact_url(task.query_or_target),
                                "error": self.redactor.redact_text(str(sec_err)),
                                "worker_state": CrawlerStatus.FAILED.value,
                            }),
                        )
                    except Exception:
                        pass
                return self._build_security_rejected_report(task, str(sec_err), execution_time=time.monotonic() - start_time)
            except StructuredDataValidationError as val_err:
                logger.warning(f"Validation error building request for task '{task.task_id}': {val_err}")
                self.tasks_failed += 1
                self.transition_to(CrawlerStatus.FAILED, reason=f"Validation error: {val_err}")
                if context and hasattr(context, "events"):
                    try:
                        context.events.emit(
                            "structured_crawl_failed",
                            self.redactor.redact_dict({
                                "endpoint": self.redactor.redact_url(task.query_or_target),
                                "error": self.redactor.redact_text(str(val_err)),
                                "worker_state": CrawlerStatus.FAILED.value,
                            }),
                        )
                    except Exception:
                        pass
                return self._build_failed_report(task, str(val_err), execution_time=time.monotonic() - start_time)

            if context and hasattr(context, "progress"):
                context.progress.report(15.0, f"Initiating structured crawl on '{self.redactor.redact_url(request.endpoint_url)}' via {request.method.value}")
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "structured_crawl_started",
                        self.redactor.redact_dict({
                            "endpoint": self.redactor.redact_url(request.endpoint_url),
                            "method": request.method.value,
                            "provider": getattr(self.provider, "provider_id", "structured_provider"),
                            "task_id": task.task_id,
                            "worker_state": self.status.value,
                        }),
                    )
                except Exception:
                    pass

            # 3. Resolve StructuredQuery if provided in task parameters
            query_obj: Optional[StructuredQuery] = None
            params = task.parameters or {}
            if "query" in params and isinstance(params["query"], StructuredQuery):
                query_obj = params["query"]
            elif "query" in params and isinstance(params["query"], dict):
                query_obj = StructuredQuery.from_dict(params["query"])
            elif any(k in params for k in ("filters", "select_fields", "order_by", "limit", "offset")):
                query_obj = StructuredQuery(
                    select_fields=list(params.get("select_fields", [])),
                    limit=params.get("limit"),
                    offset=params.get("offset", 0),
                )

            # 4. Remote Query Contract mapping if configured
            local_query: Optional[StructuredQuery] = query_obj
            if query_obj is not None and "remote_contract" in params and isinstance(params["remote_contract"], RemoteQueryContract):
                contract: RemoteQueryContract = params["remote_contract"]
                request, remainder_query = RemoteQueryMapper.map_to_request(
                    query=query_obj,
                    request=request,
                    contract=contract,
                    policy=self.policy,
                )
                local_query = remainder_query

            # 5. Credential Resolution (in-memory only; never persist raw secrets)
            resolved_headers: dict[str, str] = dict(request.headers)
            resolved_params: dict[str, Any] = dict(request.query_params)
            cred_ref = request.credential_ref or (
                CredentialReference.from_dict(params["credential_ref"])
                if "credential_ref" in params and isinstance(params["credential_ref"], dict)
                else None
            )

            if cred_ref:
                resolver = CredentialResolver(secret_store=self.secret_store)
                resolved_creds = resolver.resolve(cred_ref)
                resolved_headers.update(resolved_creds.headers)
                resolved_params.update(resolved_creds.query_params)

            # 6. Setup rate-limited provider wrapper
            rate_limited_provider = RateLimitedStructuredDataProvider(
                inner_provider=self.provider,
                config=self.rate_limit_config,
            )

            # 7. Configure paginator with bounds
            paginator_limits = BoundedRetrievalLimits.from_limits_and_config(
                limits=request.limits or self.retrieval_limits,
                config=request.pagination,
            )

            paginator = StructuredDataPaginator(
                provider=rate_limited_provider,
                policy=self.policy,
            )

            # Build runtime execution request with resolved credentials
            exec_request = StructuredDataRequest(
                request_id=request.request_id,
                endpoint_url=request.endpoint_url,
                method=request.method,
                query_params=resolved_params,
                headers=resolved_headers,
                body=request.body,
                requested_fields=request.requested_fields,
                filters=request.filters,
                pagination=request.pagination,
                limits=request.limits,
                credential_ref=cred_ref,
                metadata=request.metadata,
            )

            # 8. Execute Bounded Paginated Retrieval
            retrieval_result: PaginatedRetrievalResult = paginator.paginate(
                initial_request=exec_request,
                config=exec_request.pagination,
                limits=exec_request.limits,
                is_cancelled=is_cancelled,
            )

            # Check if execution was cancelled during retrieval
            if retrieval_result.status == RetrievalStatus.CANCELLED or is_cancelled():
                self.transition_to(CrawlerStatus.CANCELLED, reason="Cancelled during retrieval")
                if context and hasattr(context, "events"):
                    try:
                        context.events.emit(
                            "structured_crawl_cancelled",
                            self.redactor.redact_dict({
                                "endpoint": self.redactor.redact_url(request.endpoint_url),
                                "reason": "Cancelled during retrieval",
                                "worker_state": CrawlerStatus.CANCELLED.value,
                            }),
                        )
                    except Exception:
                        pass
                return self._build_cancelled_report(task, execution_time=time.monotonic() - start_time)

            if context and hasattr(context, "progress"):
                context.progress.report(60.0, f"Retrieved {len(retrieval_result.records)} records across {len(retrieval_result.pages)} page(s)")
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "structured_data_progress",
                        self.redactor.redact_dict({
                            "endpoint": self.redactor.redact_url(request.endpoint_url),
                            "pages_retrieved": len(retrieval_result.pages),
                            "records_retrieved": len(retrieval_result.records),
                            "bytes_retrieved": retrieval_result.total_bytes_retrieved,
                            "worker_state": self.status.value,
                        }),
                    )
                except Exception:
                    pass

            # 9. Apply Local Filtering and Projections if query present
            records = list(retrieval_result.records)
            if local_query is not None and records:
                query_engine = LocalStructuredQueryEngine(default_limits=request.limits)
                query_result = query_engine.execute_query(
                    query=local_query,
                    records=records,
                    is_cancelled=is_cancelled,
                )
                records = query_result.records

            # 10. Generate Grounded Provenance, Raw Sources, and Evidence Items
            raw_sources, evidence_items = self._build_artifacts(
                task=task,
                request=request,
                retrieval_result=retrieval_result,
                records=records,
            )

            # 11. Determine Report Status
            if retrieval_result.status == RetrievalStatus.PROVIDER_FAILURE:
                report_status = CrawlerReportStatus.FAILED
                self.health = CrawlerHealthStatus.DEGRADED
            elif retrieval_result.status == RetrievalStatus.TIMEOUT:
                report_status = CrawlerReportStatus.TIMED_OUT
                self.health = CrawlerHealthStatus.DEGRADED
            elif retrieval_result.status in (RetrievalStatus.INTENTIONALLY_BOUNDED, RetrievalStatus.PARTIAL):
                report_status = CrawlerReportStatus.PARTIAL
            elif retrieval_result.status == RetrievalStatus.EMPTY or len(evidence_items) == 0:
                report_status = CrawlerReportStatus.EMPTY
            else:
                report_status = CrawlerReportStatus.SUCCESS

            elapsed = time.monotonic() - start_time
            summary = (
                f"Retrieved {len(records)} structured records ({retrieval_result.total_bytes_retrieved} bytes) "
                f"across {len(retrieval_result.pages)} page(s) from '{self.redactor.redact_url(request.endpoint_url)}'. "
                f"Status: {retrieval_result.status.value} (Exhaustive: {retrieval_result.is_exhaustive})."
            )

            err_text = str(retrieval_result.error) if retrieval_result.error else (retrieval_result.termination_reason if retrieval_result.status in (RetrievalStatus.PROVIDER_FAILURE, RetrievalStatus.PARTIAL) else None)

            report = CrawlerReport(
                report_id=f"crep-{uuid.uuid4().hex[:8]}",
                crawler_task_id=task.task_id,
                crawler_id=self.crawler_id,
                request_id=task.request_id,
                plan_id=task.plan_id,
                question_id=task.question_id,
                correlation_id=task.correlation_id,
                status=report_status,
                raw_sources=raw_sources,
                extracted_evidence=evidence_items,
                summary=self.redactor.redact_text(summary),
                error_message=self.redactor.redact_text(err_text) if err_text else None,
                execution_time_seconds=elapsed,
                metadata=self.redactor.redact_dict({
                    "endpoint_url": self.redactor.redact_url(request.endpoint_url),
                    "method": request.method.value,
                    "pages_fetched": len(retrieval_result.pages),
                    "total_records": retrieval_result.total_records_collected,
                    "matched_records": len(records),
                    "total_bytes": retrieval_result.total_bytes_retrieved,
                    "retrieval_status": retrieval_result.status.value,
                    "is_exhaustive": retrieval_result.is_exhaustive,
                    "provider": getattr(self.provider, "provider_id", "structured_provider"),
                }),
            )

            final_crawler_status = (
                CrawlerStatus.COMPLETED if report_status in (CrawlerReportStatus.SUCCESS, CrawlerReportStatus.PARTIAL, CrawlerReportStatus.EMPTY)
                else CrawlerStatus.FAILED
            )
            self.transition_to(final_crawler_status, reason=f"Task finished with {report_status.value}")
            if final_crawler_status == CrawlerStatus.COMPLETED:
                self.tasks_completed += 1
            else:
                self.tasks_failed += 1

            if context and hasattr(context, "progress"):
                context.progress.report(100.0, f"Structured data crawl finished with {report_status.value}: {len(records)} records extracted")
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "structured_crawl_completed",
                        self.redactor.redact_dict({
                            "endpoint": self.redactor.redact_url(request.endpoint_url),
                            "status": report_status.value,
                            "provider": getattr(self.provider, "provider_id", "structured_provider"),
                            "pages_retrieved": len(retrieval_result.pages),
                            "records_retrieved": retrieval_result.total_records_collected,
                            "matched_records": len(records),
                            "is_exhaustive": retrieval_result.is_exhaustive,
                            "worker_state": final_crawler_status.value,
                        }),
                    )
                except Exception:
                    pass

            return report

        except StructuredDataCancelledError:
            self.tasks_failed += 1
            self.transition_to(CrawlerStatus.CANCELLED, reason="Cancelled during processing")
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "structured_crawl_cancelled",
                        self.redactor.redact_dict({
                            "endpoint": self.redactor.redact_url(task.query_or_target),
                            "reason": "Cancelled during processing",
                            "worker_state": CrawlerStatus.CANCELLED.value,
                        }),
                    )
                except Exception:
                    pass
            return self._build_cancelled_report(task, execution_time=time.monotonic() - start_time)

        except StructuredDataTimeoutError as time_err:
            logger.warning(f"Timeout executing task '{task.task_id}': {time_err}")
            self.tasks_failed += 1
            self.health = CrawlerHealthStatus.DEGRADED
            self.transition_to(CrawlerStatus.FAILED, reason=f"Timeout: {time_err}")
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "structured_crawl_timeout",
                        self.redactor.redact_dict({
                            "endpoint": self.redactor.redact_url(task.query_or_target),
                            "error": str(time_err),
                            "worker_state": CrawlerStatus.FAILED.value,
                        }),
                    )
                except Exception:
                    pass
            return self._build_timed_out_report(task, str(time_err), execution_time=time.monotonic() - start_time)

        except (StructuredDataAuthenticationError, StructuredDataAuthorizationError) as auth_err:
            logger.warning(f"Auth error executing task '{task.task_id}': {auth_err}")
            self.tasks_failed += 1
            self.transition_to(CrawlerStatus.FAILED, reason=f"Auth error: {auth_err}")
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "structured_crawl_failed",
                        self.redactor.redact_dict({
                            "endpoint": self.redactor.redact_url(task.query_or_target),
                            "error": str(auth_err),
                            "worker_state": CrawlerStatus.FAILED.value,
                        }),
                    )
                except Exception:
                    pass
            return self._build_failed_report(task, str(auth_err), execution_time=time.monotonic() - start_time)

        except StructuredDataQuotaExceededError as quota_err:
            logger.warning(f"Quota exceeded executing task '{task.task_id}': {quota_err}")
            self.tasks_failed += 1
            self.transition_to(CrawlerStatus.FAILED, reason=f"Quota exceeded: {quota_err}")
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "structured_crawl_failed",
                        self.redactor.redact_dict({
                            "endpoint": self.redactor.redact_url(task.query_or_target),
                            "error": str(quota_err),
                            "worker_state": CrawlerStatus.FAILED.value,
                        }),
                    )
                except Exception:
                    pass
            return self._build_failed_report(task, str(quota_err), execution_time=time.monotonic() - start_time)

        except Exception as exc:
            logger.exception(f"Unexpected error executing task '{task.task_id}': {exc}")
            self.tasks_failed += 1
            self.health = CrawlerHealthStatus.DEGRADED
            self.transition_to(CrawlerStatus.FAILED, reason=f"Unhandled error: {exc}")
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "structured_crawl_failed",
                        self.redactor.redact_dict({
                            "endpoint": self.redactor.redact_url(task.query_or_target),
                            "error": f"Structured data retrieval error: {exc}",
                            "worker_state": CrawlerStatus.FAILED.value,
                        }),
                    )
                except Exception:
                    pass
            return self._build_failed_report(task, f"Structured data retrieval error: {exc}", execution_time=time.monotonic() - start_time)

    # -------------------------------------------------------------------------
    # Provenance & Artifact Generation
    # -------------------------------------------------------------------------

    def _build_artifacts(
        self,
        task: CrawlerTask,
        request: StructuredDataRequest,
        retrieval_result: PaginatedRetrievalResult,
        records: list[StructuredRecord],
    ) -> tuple[list[RawSourceReference], list[EvidenceItem]]:
        """
        Construct cryptographically hashed RawSourceReference and EvidenceItem artifacts
        with full audit provenance metadata.
        """
        raw_sources: list[RawSourceReference] = []
        evidence_items: list[EvidenceItem] = []

        safe_endpoint = self.redactor.redact_url(request.endpoint_url)
        safe_params = self.redactor.redact_dict(request.query_params)
        provider_name = getattr(self.provider, "provider_id", "structured_provider")

        # Map responses by response_id for quick header/metadata lookup
        response_map = {resp.response_id: resp for resp in retrieval_result.pages}

        # 1. Create RawSourceReferences for each response page
        for resp in retrieval_result.pages:
            page_revision = (
                resp.headers.get("etag")
                or resp.headers.get("last-modified")
                or resp.metadata.get("version")
                or resp.metadata.get("revision")
                or ""
            )
            raw_sources.append(
                RawSourceReference(
                    url_or_ref=safe_endpoint,
                    title=f"Structured Source [{provider_name}] - Status {resp.status_code}",
                    publisher=provider_name,
                    source_type=SourceType.PRIMARY_SOURCE,
                    checksum=resp.raw_payload_hash,
                    bytes_fetched=resp.response_bytes,
                    content_snippet=self.redactor.redact_text(
                        canonical_json_dumps(resp.payload)[:1000] if isinstance(resp.payload, (dict, list)) else str(resp.payload)[:1000]
                    ),
                    fetched_at=resp.retrieved_at,
                    metadata=self.redactor.redact_dict({
                        "provider": provider_name,
                        "endpoint": safe_endpoint,
                        "method": request.method.value,
                        "request_params": safe_params,
                        "retrieved_at": resp.retrieved_at,
                        "response_status": resp.status_code,
                        "content_type": resp.normalized_content_type.value,
                        "response_hash": resp.raw_payload_hash,
                        "pagination_context": {
                            "page_number": resp.pagination_info.current_page if resp.pagination_info else None,
                            "records_fetched": retrieval_result.total_records_collected,
                            "is_exhaustive": retrieval_result.is_exhaustive,
                            "pagination_type": resp.pagination_info.pagination_type.value if resp.pagination_info else "none",
                        },
                        "lineage": {
                            "request_id": task.request_id,
                            "crawler_task_id": task.task_id,
                            "crawler_id": self.crawler_id,
                            "question_id": task.question_id,
                            "plan_id": task.plan_id,
                            "correlation_id": task.correlation_id,
                        },
                        "revision": str(page_revision),
                    }),
                )
            )

        # 2. Create EvidenceItems for each extracted StructuredRecord
        for idx, rec in enumerate(records):
            parent_resp = response_map.get(rec.metadata.get("parent_response_id", ""))
            status_code = parent_resp.status_code if parent_resp else 200
            content_type = parent_resp.normalized_content_type.value if parent_resp else "application/json"
            retrieved_at = parent_resp.retrieved_at if parent_resp else utc_now()
            response_hash = parent_resp.raw_payload_hash if parent_resp else rec.content_hash

            rec_revision = (
                parent_resp.headers.get("etag")
                or parent_resp.headers.get("last-modified")
                or rec.metadata.get("version")
                or ""
            ) if parent_resp else ""

            # Convert value to inert text snippet safely
            if isinstance(rec.value, (dict, list)):
                snippet = canonical_json_dumps(rec.value)
            else:
                snippet = str(rec.value)
            safe_snippet = self.redactor.redact_text(snippet[:2000])

            # Deterministic concise extracted fact (first key-values or representation)
            fact_summary = self._summarize_record_fact(rec.value, rec.source_location.path)
            safe_fact = self.redactor.redact_text(fact_summary)

            source_ref = f"{safe_endpoint}#{rec.source_location.path}"

            provenance = EvidenceProvenance(
                request_id=task.request_id,
                crawler_task_id=task.task_id,
                crawler_id=self.crawler_id,
                question_id=task.question_id,
                source_ref=source_ref,
                correlation_id=task.correlation_id,
                captured_at=retrieved_at,
            )

            evidence_metadata = self.redactor.redact_dict({
                "provider": provider_name,
                "endpoint": safe_endpoint,
                "method": request.method.value,
                "request_params": safe_params,
                "retrieved_at": retrieved_at,
                "response_status": status_code,
                "content_type": content_type,
                "response_hash": response_hash,
                "record_path": rec.source_location.path,
                "pagination_context": {
                    "records_fetched": retrieval_result.total_records_collected,
                    "is_exhaustive": retrieval_result.is_exhaustive,
                },
                "lineage": {
                    "request_id": task.request_id,
                    "crawler_task_id": task.task_id,
                    "crawler_id": self.crawler_id,
                    "question_id": task.question_id,
                    "plan_id": task.plan_id,
                    "correlation_id": task.correlation_id,
                },
                "revision": str(rec_revision),
            })

            evidence_items.append(
                EvidenceItem(
                    evidence_id=f"ev-{task.task_id}-{idx + 1}",
                    provenance=provenance,
                    extracted_fact=safe_fact,
                    content_snippet=safe_snippet,
                    classification=FactClassification.FACT,
                    confidence=ResearchConfidence.SUPPORTED,
                    reliability_score=0.95,
                    source_type=SourceType.PRIMARY_SOURCE,
                    checksum=rec.content_hash,
                    metadata=evidence_metadata,
                    created_at=retrieved_at,
                )
            )

        return raw_sources, evidence_items

    @staticmethod
    def _summarize_record_fact(val: Any, path: str) -> str:
        """Generate a concise, safe factual summary of a record without semantic assumptions."""
        if isinstance(val, dict):
            # Take up to first 3 keys sorted deterministically
            items = [f"{k}={val[k]}" for k in sorted(val.keys())[:3]]
            return f"Structured record at '{path}': {', '.join(items)}"
        elif isinstance(val, list):
            return f"Structured list at '{path}' ({len(val)} items)"
        else:
            return f"Structured value at '{path}': {val}"

    # -------------------------------------------------------------------------
    # Failure and Cancellation Report Builders
    # -------------------------------------------------------------------------

    def _build_cancelled_report(self, task: CrawlerTask, execution_time: float) -> CrawlerReport:
        return CrawlerReport(
            report_id=f"crep-{uuid.uuid4().hex[:8]}",
            crawler_task_id=task.task_id,
            crawler_id=self.crawler_id,
            request_id=task.request_id,
            plan_id=task.plan_id,
            question_id=task.question_id,
            correlation_id=task.correlation_id,
            status=CrawlerReportStatus.FAILED,
            raw_sources=[],
            extracted_evidence=[],
            summary=f"Task '{task.task_id}' was cancelled by supervisor/context.",
            error_message="Task cancelled by supervisor or runtime context.",
            execution_time_seconds=execution_time,
            metadata={"status": "cancelled", "cancelled": True},
        )

    def _build_security_rejected_report(self, task: CrawlerTask, error_msg: str, execution_time: float) -> CrawlerReport:
        clean_msg = self.redactor.redact_text(error_msg)
        return CrawlerReport(
            report_id=f"crep-{uuid.uuid4().hex[:8]}",
            crawler_task_id=task.task_id,
            crawler_id=self.crawler_id,
            request_id=task.request_id,
            plan_id=task.plan_id,
            question_id=task.question_id,
            correlation_id=task.correlation_id,
            status=CrawlerReportStatus.FAILED,
            raw_sources=[],
            extracted_evidence=[],
            summary=f"Security policy rejected request: {clean_msg}",
            error_message=clean_msg,
            execution_time_seconds=execution_time,
            metadata={"security_rejection": True},
        )

    def _build_timed_out_report(self, task: CrawlerTask, error_msg: str, execution_time: float) -> CrawlerReport:
        clean_msg = self.redactor.redact_text(error_msg)
        return CrawlerReport(
            report_id=f"crep-{uuid.uuid4().hex[:8]}",
            crawler_task_id=task.task_id,
            crawler_id=self.crawler_id,
            request_id=task.request_id,
            plan_id=task.plan_id,
            question_id=task.question_id,
            correlation_id=task.correlation_id,
            status=CrawlerReportStatus.TIMED_OUT,
            raw_sources=[],
            extracted_evidence=[],
            summary=f"Structured data crawler task timed out: {clean_msg}",
            error_message=clean_msg,
            execution_time_seconds=execution_time,
            metadata={"timeout": True},
        )

    def _build_failed_report(self, task: CrawlerTask, error_msg: str, execution_time: float) -> CrawlerReport:
        clean_msg = self.redactor.redact_text(error_msg)
        return CrawlerReport(
            report_id=f"crep-{uuid.uuid4().hex[:8]}",
            crawler_task_id=task.task_id,
            crawler_id=self.crawler_id,
            request_id=task.request_id,
            plan_id=task.plan_id,
            question_id=task.question_id,
            correlation_id=task.correlation_id,
            status=CrawlerReportStatus.FAILED,
            raw_sources=[],
            extracted_evidence=[],
            summary=f"Structured data crawler task failed: {clean_msg}",
            error_message=clean_msg,
            execution_time_seconds=execution_time,
            metadata={"failure": True},
        )

    def execute_task(self, *args, **kwargs) -> WorkerOutput:
        """
        Worker SDK standard execution interface.
        Supports both execute_task(context, task) and execute_task(task, context=None).
        """
        context = None
        task = None
        if len(args) == 2:
            if isinstance(args[0], Task):
                task, context = args[0], args[1]
            else:
                context, task = args[0], args[1]
        elif len(args) == 1:
            if isinstance(args[0], Task):
                task = args[0]
            else:
                context = args[0]
        if "task" in kwargs:
            task = kwargs["task"]
        if "context" in kwargs:
            context = kwargs["context"]

        if task is None:
            raise ValueError("Task is required for execute_task")

        metadata = dict(task.metadata) if task.metadata else {}
        endpoint_candidate = (
            metadata.get("endpoint_url")
            or metadata.get("url")
            or metadata.get("target")
            or task.objective
            or task.title
        )

        crawler_task = CrawlerTask(
            task_id=task.id,
            request_id=task.parent_task_id or task.id,
            plan_id=f"plan-{task.id}",
            question_id="q-direct",
            query_or_target=endpoint_candidate,
            required_capability=CrawlerCapability.STRUCTURED_DATA_EXTRACTION,
            parameters=metadata,
        )

        report = self.execute_crawler_task(crawler_task, context=context)

        return WorkerOutput(
            success=report.status in (CrawlerReportStatus.SUCCESS, CrawlerReportStatus.EMPTY, CrawlerReportStatus.PARTIAL),
            summary=report.summary,
            report_markdown=(
                f"# Structured Data Extraction Report\n\n"
                f"- Crawler: `{self.crawler_id}`\n"
                f"- Task: `{task.title}`\n"
                f"- Status: `{report.status.value}`\n"
                f"- Records: `{len(report.extracted_evidence)}`\n"
                f"- Execution Time: `{report.execution_time_seconds:.2f}s`\n\n"
                f"{report.summary}"
            ),
            error_message=report.error_message,
            metadata={"crawler_report": report.to_dict()},
        )
