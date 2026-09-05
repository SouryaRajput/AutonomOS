from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any, Optional
import uuid

from core.research.contracts.request import ResearchRequest, ResearchScope
from core.research.types import ResearchMode, SourceType


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class NormalizedResearchRequest(ResearchRequest):
    """
    Strongly-typed domain model representing a canonically normalized ResearchRequest.
    Subclasses ResearchRequest for 100% backward compatibility with all downstream
    planning, supervisor, and worker execution components.
    """
    is_normalized: bool = True
    normalization_actions: list[str] = field(default_factory=list)
    raw_request_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize normalized request including normalization provenance."""
        base = super().to_dict()
        base["is_normalized"] = self.is_normalized
        base["normalization_actions"] = list(self.normalization_actions)
        base["raw_request_snapshot"] = dict(self.raw_request_snapshot)
        return base

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NormalizedResearchRequest:
        """Deserialize into NormalizedResearchRequest."""
        req = ResearchRequest.from_dict(data)
        return cls(
            request_id=req.request_id,
            project_id=req.project_id,
            task_id=req.task_id,
            correlation_id=req.correlation_id,
            objective=req.objective,
            mode=req.mode,
            questions=list(req.questions),
            scope=req.scope,
            constraints=list(req.constraints),
            required_output_format=req.required_output_format,
            priority=req.priority,
            context_references=list(req.context_references),
            metadata=dict(req.metadata),
            created_at=req.created_at,
            is_normalized=bool(data.get("is_normalized", True)),
            normalization_actions=list(data.get("normalization_actions", [])),
            raw_request_snapshot=dict(data.get("raw_request_snapshot", {})),
        )


class ResearchRequestNormalizer:
    """
    Deterministic preprocessing engine for ResearchRequest instances.
    Transforms raw requests into a canonical, cleaned, deduplicated, and
    predictably structured representation without inferring intent or calling LLMs.
    Guaranteed to be idempotent: normalize(normalize(req)) == normalize(req).
    """

    def normalize(self, request: ResearchRequest) -> NormalizedResearchRequest:
        """
        Normalize a ResearchRequest into a canonical NormalizedResearchRequest.
        Idempotent: Re-normalizing an already normalized request yields an equal request.
        """
        if request is None:
            raise TypeError("Cannot normalize None; expected a ResearchRequest instance.")
        if not isinstance(request, ResearchRequest):
            raise TypeError(f"Expected ResearchRequest instance, got {type(request).__name__}")

        # Idempotence shortcut: if already normalized, return identical copy
        if isinstance(request, NormalizedResearchRequest) and request.is_normalized:
            return NormalizedResearchRequest(
                request_id=request.request_id,
                project_id=request.project_id,
                task_id=request.task_id,
                correlation_id=request.correlation_id,
                objective=request.objective,
                mode=request.mode,
                questions=list(request.questions),
                scope=ResearchScope.from_dict(request.scope.to_dict()),
                constraints=list(request.constraints),
                required_output_format=request.required_output_format,
                priority=request.priority,
                context_references=[dict(r) for r in request.context_references],
                metadata=dict(request.metadata),
                created_at=request.created_at,
                is_normalized=True,
                normalization_actions=list(request.normalization_actions),
                raw_request_snapshot=dict(request.raw_request_snapshot),
            )

        # Snapshot raw input for lineage & provenance
        try:
            raw_snapshot = request.to_dict()
        except Exception:
            raw_snapshot = {
                "request_id": getattr(request, "request_id", ""),
                "project_id": getattr(request, "project_id", ""),
                "task_id": getattr(request, "task_id", ""),
                "objective": getattr(request, "objective", ""),
                "questions": list(getattr(request, "questions", []) or []),
                "constraints": list(getattr(request, "constraints", []) or []),
                "metadata": dict(getattr(request, "metadata", {}) or {}),
            }
            if getattr(request, "scope", None) is not None:
                try:
                    raw_snapshot["scope"] = request.scope.to_dict()
                except Exception:
                    pass
        actions: list[str] = []

        # 1. Identifier and Basic String Normalization
        request_id = request.request_id.strip() if request.request_id else f"req-{uuid.uuid4().hex[:8]}"
        project_id = request.project_id.strip() if request.project_id else ""
        task_id = request.task_id.strip() if request.task_id else ""
        correlation_id = request.correlation_id.strip() if request.correlation_id else str(uuid.uuid4())

        # 2. Objective Normalization
        objective = self.normalize_objective(request.objective)
        if objective != request.objective:
            actions.append("normalized_objective_whitespace")

        # 3. Questions Normalization (whitespace, empty filtering, order-preserving dedup)
        questions, q_actions = self.normalize_questions(request.questions)
        actions.extend(q_actions)

        # 4. Constraints Normalization (whitespace, bullet removal, dedup)
        constraints, c_actions = self.normalize_constraints(request.constraints)
        actions.extend(c_actions)

        # 5. Scope Normalization (domain cleaning, lexicographical sorting, numeric bounds)
        scope, s_actions = self.normalize_scope(request.scope)
        actions.extend(s_actions)

        # 6. Context References Normalization
        context_refs, ref_actions = self.normalize_context_references(request.context_references)
        actions.extend(ref_actions)

        # 7. Priority Clamping
        priority = self.normalize_priority(request.priority)
        if priority != request.priority:
            actions.append(f"clamped_priority_to_{priority}")

        # 8. Output Format Normalization
        output_format = self.normalize_output_format(request.required_output_format)

        # 9. Metadata & Temporal / Version / Geographic Normalization
        metadata, m_actions = self.normalize_metadata(request.metadata, scope)
        actions.extend(m_actions)

        # Embed normalization provenance directly into metadata
        metadata["_normalized"] = True
        metadata["_original_request_id"] = request.request_id
        metadata["_normalization_timestamp"] = utc_now()
        metadata["_normalization_actions"] = list(actions)

        return NormalizedResearchRequest(
            request_id=request_id,
            project_id=project_id,
            task_id=task_id,
            correlation_id=correlation_id,
            objective=objective,
            mode=request.mode if isinstance(request.mode, ResearchMode) else ResearchMode.STANDARD,
            questions=questions,
            scope=scope,
            constraints=constraints,
            required_output_format=output_format,
            priority=priority,
            context_references=context_refs,
            metadata=metadata,
            created_at=request.created_at or utc_now(),
            is_normalized=True,
            normalization_actions=actions,
            raw_request_snapshot=raw_snapshot,
        )

    # -------------------------------------------------------------------------
    # Granular Normalization Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def normalize_whitespace(text: str) -> str:
        """Strip leading/trailing whitespace and collapse internal whitespace sequences."""
        if not text:
            return ""
        return re.sub(r"\s+", " ", text).strip()

    def normalize_objective(self, objective: str) -> str:
        """Clean objective and validate that it is not empty."""
        if not objective or not isinstance(objective, str):
            raise ValueError("ResearchRequest 'objective' cannot be empty or non-string.")
        cleaned = self.normalize_whitespace(objective)
        if not cleaned:
            raise ValueError("ResearchRequest 'objective' cannot be empty or whitespace-only.")
        return cleaned

    def normalize_questions(self, questions: list[str]) -> tuple[list[str], list[str]]:
        """Clean questions, remove empty entries, and deduplicate case-insensitively."""
        actions: list[str] = []
        cleaned_list: list[str] = []
        seen_lower: set[str] = set()

        for q in questions:
            if not isinstance(q, str):
                continue
            cleaned = self.normalize_whitespace(q)
            if not cleaned:
                actions.append("removed_empty_question")
                continue
            key = cleaned.lower()
            if key in seen_lower:
                actions.append(f"removed_duplicate_question:{cleaned[:30]}")
                continue
            seen_lower.add(key)
            cleaned_list.append(cleaned)

        return cleaned_list, actions

    def normalize_constraints(self, constraints: list[str]) -> tuple[list[str], list[str]]:
        """
        Clean constraint strings:
        - Strip common bullet characters (- , * , • , 1. )
        - Collapse internal whitespace
        - Remove empty constraints
        - Case-insensitively deduplicate preserving initial ordering
        """
        actions: list[str] = []
        cleaned_list: list[str] = []
        seen_lower: set[str] = set()

        for c in constraints:
            if not isinstance(c, str):
                continue
            raw = c.strip()
            # Remove leading bullet markers
            raw = re.sub(r"^[-*•]\s+", "", raw)
            raw = re.sub(r"^\d+\.\s+", "", raw)
            cleaned = self.normalize_whitespace(raw)
            if not cleaned:
                actions.append("removed_empty_constraint")
                continue
            key = cleaned.lower()
            if key in seen_lower:
                actions.append(f"removed_duplicate_constraint:{cleaned[:30]}")
                continue
            seen_lower.add(key)
            cleaned_list.append(cleaned)

        return cleaned_list, actions

    @staticmethod
    def normalize_domain(domain: str) -> str:
        """
        Canonicalize domain:
        - Lowercase
        - Strip leading http:// or https://
        - Strip path segments and trailing slashes
        - Strip standard ports (:80, :443)
        """
        if not domain or not isinstance(domain, str):
            return ""
        d = domain.strip().lower()
        d = re.sub(r"^https?://", "", d)
        d = d.split("/")[0]
        d = re.sub(r":(80|443)$", "", d)
        d = d.strip(".")
        return d

    def normalize_domains_list(self, domains: list[str]) -> list[str]:
        """Normalize, deduplicate, and sort a list of domain names lexicographically."""
        cleaned_set: set[str] = set()
        for dom in domains:
            norm = self.normalize_domain(dom)
            if norm and "." in norm:  # Valid domain must have a dot (e.g. python.org, github.com)
                cleaned_set.add(norm)
            elif norm == "localhost":
                cleaned_set.add(norm)
        return sorted(cleaned_set)

    def normalize_scope(self, scope: Optional[ResearchScope]) -> tuple[ResearchScope, list[str]]:
        """Normalize research scope parameters, domains, source types, and bounds."""
        actions: list[str] = []
        if scope is None:
            return ResearchScope(), ["created_default_scope"]

        # Normalize domains
        allowed = self.normalize_domains_list(scope.allowed_domains)
        excluded = self.normalize_domains_list(scope.excluded_domains)

        # Excluded domains take precedence over allowed domains
        initial_allowed_len = len(allowed)
        allowed = [d for d in allowed if d not in set(excluded)]
        if len(allowed) < initial_allowed_len:
            actions.append("resolved_domain_conflict_in_favor_of_exclusion")

        # Preferred source types deduplication
        source_types: list[SourceType] = []
        seen_sources: set[str] = set()
        for st in scope.preferred_source_types:
            if isinstance(st, SourceType):
                st_enum = st
            else:
                try:
                    st_enum = SourceType(str(st))
                except (ValueError, TypeError):
                    continue
            if st_enum.value not in seen_sources:
                seen_sources.add(st_enum.value)
                source_types.append(st_enum)

        # Recency days normalization
        recency = scope.recency_days
        if recency is not None:
            if int(recency) <= 0:
                recency = None
                actions.append("normalized_non_positive_recency_days_to_none")
            else:
                recency = int(recency)

        # Numeric bounds clamping to positive, sane integers
        max_crawlers = max(1, min(50, int(scope.max_crawlers if scope.max_crawlers is not None else 5)))
        max_searches = max(1, min(100, int(scope.max_searches if scope.max_searches is not None else 6)))
        max_fetches = max(1, min(200, int(scope.max_fetches if scope.max_fetches is not None else 10)))
        max_sources = max(1, min(300, int(scope.max_sources if scope.max_sources is not None else 15)))
        max_inference_calls = max(0, min(100, int(scope.max_inference_calls if scope.max_inference_calls is not None else 5)))
        cost_limit = max(0.0, float(scope.cost_limit if scope.cost_limit is not None else 1.0))
        timeout_seconds = max(10, min(86400, int(scope.timeout_seconds if scope.timeout_seconds is not None else 300)))
        min_evidence = max(1, min(50, int(scope.min_evidence_per_question if scope.min_evidence_per_question is not None else 1)))

        norm_scope = ResearchScope(
            allowed_domains=allowed,
            excluded_domains=excluded,
            preferred_source_types=source_types,
            recency_days=recency,
            max_crawlers=max_crawlers,
            max_searches=max_searches,
            max_fetches=max_fetches,
            max_sources=max_sources,
            max_inference_calls=max_inference_calls,
            cost_limit=cost_limit,
            timeout_seconds=timeout_seconds,
            min_evidence_per_question=min_evidence,
        )

        return norm_scope, actions

    def normalize_context_references(
        self, refs: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Normalize context references by stripping whitespace and deduplicating by key/path."""
        actions: list[str] = []
        cleaned_refs: list[dict[str, Any]] = []
        seen_keys: set[str] = set()

        for ref in refs:
            if not isinstance(ref, dict) or not ref:
                continue
            cleaned_ref: dict[str, Any] = {}
            for k, v in ref.items():
                if isinstance(v, str):
                    cleaned_ref[k] = self.normalize_whitespace(v)
                else:
                    cleaned_ref[k] = v
            # Determine deduplication key
            dedup_key = (
                cleaned_ref.get("id")
                or cleaned_ref.get("uri")
                or cleaned_ref.get("path")
                or cleaned_ref.get("name")
                or str(cleaned_ref)
            )
            if dedup_key in seen_keys:
                actions.append(f"removed_duplicate_context_ref:{dedup_key}")
                continue
            seen_keys.add(dedup_key)
            cleaned_refs.append(cleaned_ref)

        return cleaned_refs, actions

    @staticmethod
    def normalize_priority(priority: Any) -> int:
        """Clamp priority to standard integer range [1, 100]."""
        try:
            val = int(priority)
            return max(1, min(100, val))
        except (ValueError, TypeError):
            return 50

    @staticmethod
    def normalize_output_format(fmt: Any) -> str:
        """Canonicalize required_output_format string."""
        if not fmt or not isinstance(fmt, str):
            return "markdown_report"
        cleaned = re.sub(r"\s+", "_", fmt.strip().lower())
        return cleaned or "markdown_report"

    def normalize_metadata(
        self, metadata: dict[str, Any], scope: ResearchScope
    ) -> tuple[dict[str, Any], list[str]]:
        """
        Normalize metadata dictionary:
        - Clean string values
        - Standardize temporal fields (ISO date formatting, start <= end)
        - Standardize version fields (strip 'v' prefix, specifier spacing)
        - Standardize geographic fields (uppercase 2-letter codes)
        """
        actions: list[str] = []
        norm_meta: dict[str, Any] = {}

        if not isinstance(metadata, dict):
            return norm_meta, actions

        for k, v in metadata.items():
            # Skip internal provenance fields that will be overwritten
            if k.startswith("_normalized"):
                continue
            if isinstance(v, str):
                norm_meta[k] = self.normalize_whitespace(v)
            elif isinstance(v, list):
                norm_meta[k] = [self.normalize_whitespace(x) if isinstance(x, str) else x for x in v]
            elif isinstance(v, dict):
                norm_meta[k] = dict(v)
            else:
                norm_meta[k] = v

        # 1. Temporal normalization
        self._normalize_temporal_keys(norm_meta, actions)

        # 2. Version normalization
        self._normalize_version_keys(norm_meta, actions)

        # 3. Geographic normalization
        self._normalize_geographic_keys(norm_meta, actions)

        return norm_meta, actions

    def _normalize_temporal_keys(self, meta: dict[str, Any], actions: list[str]) -> None:
        """Format and validate temporal keys in metadata."""
        date_keys = ["start_date", "end_date", "since", "until", "as_of_date", "as_of"]
        for dk in date_keys:
            if dk in meta and isinstance(meta[dk], str) and meta[dk]:
                parsed = self._try_parse_iso_date(meta[dk])
                if parsed:
                    meta[dk] = parsed

        # Date range consistency check (start_date <= end_date)
        start_key = "start_date" if "start_date" in meta else ("since" if "since" in meta else None)
        end_key = "end_date" if "end_date" in meta else ("until" if "until" in meta else None)

        if start_key and end_key:
            start_val = meta[start_key]
            end_val = meta[end_key]
            if isinstance(start_val, str) and isinstance(end_val, str):
                if start_val > end_val:
                    # Inverted range: swap deterministically
                    meta[start_key], meta[end_key] = end_val, start_val
                    actions.append(f"swapped_inverted_temporal_range:{start_key}_and_{end_key}")

    @staticmethod
    def _try_parse_iso_date(date_str: str) -> Optional[str]:
        """Try parsing date string and reformatting to canonical ISO 8601 UTC string."""
        s = date_str.strip()
        # YYYY-MM-DD
        if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            return f"{s}T00:00:00Z"
        # YYYY-MM-DDTHH:MM:SS...
        try:
            # Replace Z with +00:00 for fromisoformat compatibility
            clean_s = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_s)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            return None

    @staticmethod
    def _normalize_version_keys(meta: dict[str, Any], actions: list[str]) -> None:
        """Clean version specifiers and ecosystems in metadata."""
        version_keys = ["target_version", "min_version", "max_version", "version"]
        for vk in version_keys:
            if vk in meta and isinstance(meta[vk], str):
                raw = meta[vk].strip()
                # Strip leading 'v' or 'V' before digits (e.g. v3.12 -> 3.12)
                cleaned = re.sub(r"^[vV](\d+)", r"\1", raw)
                if cleaned != raw:
                    meta[vk] = cleaned
                    actions.append(f"stripped_version_prefix:{vk}")

        if "version_specifier" in meta and isinstance(meta["version_specifier"], str):
            # Normalize whitespace around comparison operators
            spec = meta["version_specifier"].strip()
            norm_spec = re.sub(r"\s*([<>=!~]+)\s*", r"\1", spec)
            norm_spec = re.sub(r"\s*,\s*", ",", norm_spec)
            meta["version_specifier"] = norm_spec

        if "ecosystem" in meta and isinstance(meta["ecosystem"], str):
            meta["ecosystem"] = meta["ecosystem"].strip().lower()

    @staticmethod
    def _normalize_geographic_keys(meta: dict[str, Any], actions: list[str]) -> None:
        """Uppercase 2-letter country codes in geographic scope."""
        geo_keys = ["geographic_scope", "country_code", "region"]
        for gk in geo_keys:
            if gk in meta and isinstance(meta[gk], str):
                val = meta[gk].strip()
                if len(val) == 2 and val.isalpha():
                    meta[gk] = val.upper()
