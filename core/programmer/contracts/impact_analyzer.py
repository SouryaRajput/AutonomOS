from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Optional, Sequence, Union

from core.programmer.contracts.codebase_understanding import (
    CodebaseUnderstanding,
    UnderstandingInsight,
)
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.filesystem_resolver import (
    FilesystemBoundaryResolver,
    FilesystemDecision,
    is_subpath_or_equal,
)
from core.programmer.contracts.git_model import GitExecutionContext
from core.programmer.contracts.identifiers import (
    new_impact_analysis_id,
    new_verification_evidence_id,
)
from core.programmer.contracts.impact_analysis import (
    ImpactAnalysis,
    ImpactItem,
)
from core.programmer.contracts.verification import VerificationEvidence
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.programmer.types import (
    FilesystemOperation,
    ImpactLevel,
    UnderstandingConfidence,
    VerificationEvidenceSourceType,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# Stop words filtered from objective tokenization
OBJECTIVE_STOPWORDS = {
    "a", "an", "the", "and", "or", "in", "on", "at", "to", "for", "with",
    "by", "from", "as", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "implement", "create", "add",
    "update", "fix", "modify", "change", "refactor", "support", "ensure",
    "verify", "test", "should", "must", "can", "will", "all", "each", "every",
}

IGNORE_DIRECTORIES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
}


class ImpactAnalyzer:
    """
    Evidence-driven implementation impact analysis engine for Programmer V1 (Phase 7.2).

    Determines directly, indirectly, and potentially affected files, modules, interfaces,
    tests, and configurations given a validated ProgrammerWorkOrder and CodebaseUnderstanding.

    Core Invariants:
    1. Bounded strictly by Programmer ExecutionContext and authorized filesystem boundaries.
    2. Empirical evidence over guessing: analyzes observable imports, exports, and routes.
    3. Epistemic calibration: DIRECT, INDIRECT, POTENTIAL, and UNKNOWN are strictly distinct.
    4. Never automatically expands WorkOrder scope. Restricted files remain UNKNOWN.
    5. Read-only: zero modifications or writes performed on the repository.
    """

    def __init__(
        self,
        fs_resolver: Optional[FilesystemBoundaryResolver] = None,
        max_files_scanned: int = 150,
    ) -> None:
        self.fs_resolver = fs_resolver or FilesystemBoundaryResolver()
        self.max_files_scanned = max_files_scanned

    def analyze(
        self,
        workspace: ProgrammerWorkspace,
        work_order: ProgrammerWorkOrder,
        understanding: CodebaseUnderstanding,
        execution_id: Optional[str] = None,
        trace: Optional[dict[str, Any]] = None,
    ) -> ImpactAnalysis:
        """
        Execute focused, empirical impact analysis across authorized workspace files.
        """
        tr = dict(trace or {})
        exec_id = execution_id or understanding.execution_id or "pexec-impact-default"
        wo_id = work_order.work_order_id
        proj_id = work_order.project_id
        und_id = understanding.understanding_id
        repo_root = os.path.abspath(workspace.root_path)

        directly_affected_files: list[str] = []
        indirectly_affected_files: list[str] = []
        affected_modules: list[str] = []
        affected_interfaces: list[str] = []
        affected_tests: list[str] = []
        affected_configuration: list[str] = []
        dependency_impacts: list[str] = []
        potential_side_effects: list[str] = []
        unknowns: list[str] = []
        impact_items: list[ImpactItem] = []
        evidence_list: list[VerificationEvidence] = []

        # Helper to check if reading a relative path is allowed
        def is_read_allowed(rel_path: str) -> tuple[bool, str]:
            norm_rel, err = self.fs_resolver.normalize_path(workspace, rel_path)
            if err or norm_rel is None:
                return False, err or "Normalization failed"
            decision: FilesystemDecision = self.fs_resolver.resolve(
                workspace=workspace,
                requested_path=norm_rel,
                operation=FilesystemOperation.READ,
            )
            return decision.allowed, decision.reason

        # Helper to record evidence
        def add_evidence(desc: str, data: dict[str, Any]) -> str:
            ev_id = new_verification_evidence_id()
            ev = VerificationEvidence(
                evidence_id=ev_id,
                execution_id=exec_id,
                work_order_id=wo_id,
                source_type=VerificationEvidenceSourceType.IMPACT_ANALYSIS,
                description=desc,
                data=data,
                is_agent_claim=False,
            )
            evidence_list.append(ev)
            return ev_id

        # Tokenize objective to extract keywords
        raw_words = re.findall(r"[A-Za-z0-9_-]+", work_order.objective.lower())
        objective_keywords = [w for w in raw_words if len(w) >= 3 and w not in OBJECTIVE_STOPWORDS]

        # ---------------------------------------------------------------------
        # Step 1: Discover & Index Authorized Workspace Files
        # ---------------------------------------------------------------------
        all_files: list[str] = []
        file_contents: dict[str, str] = {}
        files_scanned = 0

        # Scan roots: allowed_paths or repo_root
        candidate_scan_dirs = [ap for ap in work_order.allowed_paths if ap not in (".", "*", "")]
        if not candidate_scan_dirs:
            root_ok, _ = is_read_allowed(".")
            if root_ok:
                candidate_scan_dirs = ["."]

        for c_dir in candidate_scan_dirs:
            dir_full = os.path.join(repo_root, c_dir) if c_dir != "." else repo_root
            if not os.path.exists(dir_full):
                continue
            if os.path.isfile(dir_full):
                rel_f = os.path.relpath(dir_full, repo_root)
                allowed, _ = is_read_allowed(rel_f)
                if allowed and rel_f not in all_files:
                    all_files.append(rel_f)
                continue

            for root, dirs, files in os.walk(dir_full):
                dirs[:] = [d for d in dirs if d not in IGNORE_DIRECTORIES]
                rel_root = os.path.relpath(root, repo_root)
                if rel_root != ".":
                    allowed, _ = is_read_allowed(rel_root)
                    if not allowed:
                        dirs[:] = []
                        continue
                for f in sorted(files):
                    if files_scanned >= self.max_files_scanned:
                        break
                    rel_f = os.path.relpath(os.path.join(root, f), repo_root)
                    allowed, _ = is_read_allowed(rel_f)
                    if allowed and rel_f not in all_files:
                        all_files.append(rel_f)
                        files_scanned += 1

        # Cache content of inspected source files
        for rel_f in all_files:
            full_path = os.path.join(repo_root, rel_f)
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f_obj:
                    file_contents[rel_f] = f_obj.read()
            except Exception:
                pass

        # ---------------------------------------------------------------------
        # Step 2: Identify Direct Targets (DIRECT)
        # ---------------------------------------------------------------------
        # Prioritize relevant_modules from CodebaseUnderstanding
        for mod in understanding.relevant_modules:
            is_ro = any(is_subpath_or_equal(mod, r) for r in work_order.read_only_paths)
            if is_ro:
                if mod in all_files and mod not in indirectly_affected_files:
                    indirectly_affected_files.append(mod)
                    ev_id = add_evidence(f"Identified indirect read-only target '{mod}'", {"file": mod})
                    impact_items.append(ImpactItem(
                        target=mod,
                        target_type="file",
                        level=ImpactLevel.INDIRECT,
                        rationale="Read-only module matching WorkOrder objective and CodebaseUnderstanding relevant module",
                        source_reference="CodebaseUnderstanding.relevant_modules",
                        evidence_id=ev_id,
                    ))
            elif mod in all_files and mod not in directly_affected_files:
                directly_affected_files.append(mod)
                ev_id = add_evidence(f"Identified direct implementation target '{mod}'", {"file": mod})
                impact_items.append(ImpactItem(
                    target=mod,
                    target_type="file",
                    level=ImpactLevel.DIRECT,
                    rationale="Direct implementation target matching WorkOrder objective and CodebaseUnderstanding relevant module",
                    source_reference="CodebaseUnderstanding.relevant_modules",
                    evidence_id=ev_id,
                ))

        # Check files matching objective keywords directly
        for rel_f in all_files:
            lower_f = rel_f.lower()
            bname = os.path.basename(lower_f)
            # Exclude tests from being DIRECT implementation targets unless objective is exclusively test writing
            is_test = bname.startswith("test_") or bname.endswith("_test.py") or ".test." in bname or ".spec." in bname
            if not is_test:
                for kw in objective_keywords:
                    if kw in bname:
                        is_ro = any(is_subpath_or_equal(rel_f, r) for r in work_order.read_only_paths)
                        if is_ro:
                            if rel_f not in indirectly_affected_files:
                                indirectly_affected_files.append(rel_f)
                                ev_id = add_evidence(f"Indirect read-only dependency '{rel_f}' matched keyword '{kw}'", {"file": rel_f, "keyword": kw})
                                impact_items.append(ImpactItem(
                                    target=rel_f,
                                    target_type="file",
                                    level=ImpactLevel.INDIRECT,
                                    rationale=f"Read-only dependency filename matches objective keyword '{kw}'",
                                    source_reference=kw,
                                    evidence_id=ev_id,
                                ))
                        elif rel_f not in directly_affected_files:
                            directly_affected_files.append(rel_f)
                            ev_id = add_evidence(f"Direct target file '{rel_f}' matched keyword '{kw}'", {"file": rel_f, "keyword": kw})
                            impact_items.append(ImpactItem(
                                target=rel_f,
                                target_type="file",
                                level=ImpactLevel.DIRECT,
                                rationale=f"Direct implementation target filename matches objective keyword '{kw}'",
                                source_reference=kw,
                                evidence_id=ev_id,
                            ))
                        break

        # Discover affected interfaces inside directly affected files
        for dir_f in directly_affected_files:
            content = file_contents.get(dir_f, "")
            # Python def / class
            py_defs = re.findall(r"(?:def|class)\s+([A-Za-z0-9_]+)", content)
            # JS/TS function / class / interface / type
            ts_defs = re.findall(r"(?:function|class|interface|type)\s+([A-Za-z0-9_]+)", content)
            # Routes (@app.get("/path"), router.post("/path"))
            routes = re.findall(r"(?:@app|router)\.(?:get|post|put|delete|patch)\(\s*['\"]([^'\"]+)['\"]", content)
            # Express routes (app.get("/path", ...), router.post("/path", ...))
            express_routes = re.findall(r"(?:app|router)\.(?:get|post|put|delete|patch)\(\s*['\"]([^'\"]+)['\"]", content)

            all_defs = list(set(py_defs + ts_defs))
            for sym in all_defs:
                for kw in objective_keywords:
                    if kw in sym.lower():
                        if sym not in affected_interfaces:
                            affected_interfaces.append(sym)
                            impact_items.append(ImpactItem(
                                target=sym,
                                target_type="interface",
                                level=ImpactLevel.DIRECT,
                                rationale=f"Interface symbol '{sym}' matches objective keyword '{kw}' in '{dir_f}'",
                                source_reference=dir_f,
                            ))
                        break

            for r in set(routes + express_routes):
                if r not in affected_interfaces:
                    affected_interfaces.append(r)
                    impact_items.append(ImpactItem(
                        target=r,
                        target_type="interface",
                        level=ImpactLevel.DIRECT,
                        rationale=f"Route endpoint '{r}' declared in direct target '{dir_f}'",
                        source_reference=dir_f,
                    ))

        # Add modules of directly affected files to affected_modules
        for d_file in directly_affected_files:
            mod_name = os.path.splitext(d_file)[0].replace(os.sep, ".")
            if mod_name not in affected_modules:
                affected_modules.append(mod_name)

        # ---------------------------------------------------------------------
        # Step 3: Parse Imports & Identify Indirect Dependents (INDIRECT)
        # ---------------------------------------------------------------------
        # Map module identifiers from directly affected files
        direct_stems: set[str] = {Path(df).stem for df in directly_affected_files}
        direct_rel_paths: set[str] = set(directly_affected_files)

        for rel_f, content in file_contents.items():
            if rel_f in directly_affected_files:
                continue

            bname = os.path.basename(rel_f)
            is_test_file = (
                bname.startswith("test_")
                or bname.endswith("_test.py")
                or ".test." in bname
                or ".spec." in bname
                or rel_f.startswith("tests" + os.sep)
                or rel_f.startswith("test" + os.sep)
            )

            # Check if rel_f imports any directly affected target
            imports_direct = False
            matching_direct_target = ""

            # Python import patterns
            for df in directly_affected_files:
                stem = Path(df).stem
                # e.g. import stem, from stem import ..., from ...stem import ...
                py_import_pattern = rf"(?:from\s+[\w\.]*{re.escape(stem)}|import\s+[\w\.]*{re.escape(stem)})\b"
                if re.search(py_import_pattern, content):
                    imports_direct = True
                    matching_direct_target = df
                    break

                # JS/TS import patterns
                # e.g. from './stem', from '../dir/stem', require('./stem')
                ts_import_pattern = rf"(?:from\s+['\"][^'\"]*{re.escape(stem)}['\"]|require\(\s*['\"][^'\"]*{re.escape(stem)}['\"]\))"
                if re.search(ts_import_pattern, content):
                    imports_direct = True
                    matching_direct_target = df
                    break

            # Monorepo cross-package imports (e.g. packages/api importing @repo/core or packages/core)
            if not imports_direct:
                for df in directly_affected_files:
                    if df.startswith("packages" + os.sep):
                        pkg_name = df.split(os.sep)[1]  # e.g. "core"
                        if re.search(rf"['\"][^'\"]*{re.escape(pkg_name)}[^'\"]*['\"]", content):
                            imports_direct = True
                            matching_direct_target = df
                            break

            if imports_direct:
                if is_test_file:
                    if rel_f not in affected_tests:
                        affected_tests.append(rel_f)
                        ev_id = add_evidence(f"Test '{rel_f}' imports direct target '{matching_direct_target}'", {"test": rel_f, "target": matching_direct_target})
                        impact_items.append(ImpactItem(
                            target=rel_f,
                            target_type="test",
                            level=ImpactLevel.INDIRECT,
                            rationale=f"Test imports directly affected module '{matching_direct_target}'",
                            source_reference=matching_direct_target,
                            evidence_id=ev_id,
                        ))
                else:
                    if rel_f not in indirectly_affected_files:
                        indirectly_affected_files.append(rel_f)
                        ev_id = add_evidence(f"Indirect dependent '{rel_f}' imports direct target '{matching_direct_target}'", {"file": rel_f, "target": matching_direct_target})
                        impact_items.append(ImpactItem(
                            target=rel_f,
                            target_type="file",
                            level=ImpactLevel.INDIRECT,
                            rationale=f"Directly imports affected implementation module '{matching_direct_target}'",
                            source_reference=matching_direct_target,
                            evidence_id=ev_id,
                        ))
                    mod_name = os.path.splitext(rel_f)[0].replace(os.sep, ".")
                    if mod_name not in affected_modules:
                        affected_modules.append(mod_name)

        # Also identify test files matching directly affected filename convention (e.g. test_payment.py for payment.py)
        for df in directly_affected_files:
            stem = Path(df).stem
            for rel_f in all_files:
                bname = os.path.basename(rel_f)
                if bname in (f"test_{stem}.py", f"{stem}_test.py", f"{stem}.test.ts", f"{stem}.spec.ts", f"{stem}.test.js"):
                    if rel_f not in affected_tests:
                        affected_tests.append(rel_f)
                        impact_items.append(ImpactItem(
                            target=rel_f,
                            target_type="test",
                            level=ImpactLevel.INDIRECT,
                            rationale=f"Test file naming convention corresponds directly to implementation target '{df}'",
                            source_reference=df,
                        ))

        # ---------------------------------------------------------------------
        # Step 4: Frontend / Backend Relationship Tracing
        # ---------------------------------------------------------------------
        # If any route in affected_interfaces is referenced in client/frontend files
        for item in list(impact_items):
            if item.target_type == "interface" and item.target.startswith("/"):
                route_str = item.target
                for rel_f, content in file_contents.items():
                    if rel_f not in directly_affected_files and rel_f not in indirectly_affected_files:
                        if route_str in content:
                            indirectly_affected_files.append(rel_f)
                            ev_id = add_evidence(f"Frontend consumer '{rel_f}' references API route '{route_str}'", {"file": rel_f, "route": route_str})
                            impact_items.append(ImpactItem(
                                target=rel_f,
                                target_type="file",
                                level=ImpactLevel.INDIRECT,
                                rationale=f"Client/frontend file consumes backend route '{route_str}'",
                                source_reference=route_str,
                                evidence_id=ev_id,
                            ))

        # ---------------------------------------------------------------------
        # Step 5: Scope Boundaries & Restricted File Inspection (UNKNOWN)
        # ---------------------------------------------------------------------
        # Inspect imports inside directly affected files for references to restricted/forbidden paths or missing files
        for df in directly_affected_files:
            content = file_contents.get(df, "")

            # Check Python imports
            py_imports = re.findall(r"(?:from|import)\s+([a-zA-Z0-9_\.]+)", content)
            # Check JS/TS imports
            ts_imports = re.findall(r"(?:from|require\()\s*['\"]([^'\"]+)['\"]", content)

            combined_imports = py_imports + ts_imports
            for imp in combined_imports:
                # Check if import corresponds to a forbidden path
                for fb in workspace.forbidden_paths:
                    if fb in imp or imp.startswith(fb):
                        unknown_desc = f"Direct target '{df}' imports forbidden path or module '{imp}'"
                        if unknown_desc not in unknowns:
                            unknowns.append(unknown_desc)
                            impact_items.append(ImpactItem(
                                target=imp,
                                target_type="file",
                                level=ImpactLevel.UNKNOWN,
                                rationale=f"Referenced path '{imp}' is in forbidden_paths and cannot be analyzed",
                                source_reference=df,
                            ))

                # Check relative path references out of scope
                if imp.startswith("."):
                    curr_dir = os.path.dirname(df)
                    target_candidate = os.path.normpath(os.path.join(curr_dir, imp))
                    # Check against workspace allowed paths
                    cand_allowed, reason = is_read_allowed(target_candidate)
                    if not cand_allowed:
                        unknown_desc = f"Relative reference '{imp}' in '{df}' resolves outside authorized readable scope: {reason}"
                        if unknown_desc not in unknowns:
                            unknowns.append(unknown_desc)
                            impact_items.append(ImpactItem(
                                target=target_candidate,
                                target_type="file",
                                level=ImpactLevel.UNKNOWN,
                                rationale=f"Target '{target_candidate}' is outside authorized readable scope",
                                source_reference=df,
                            ))

                # Check missing references (imported module not in stdlib, manifests, or repo)
                elif imp.startswith("unresolved_") or imp.startswith("missing_"):
                    unknown_desc = f"Unresolved or missing reference '{imp}' imported by '{df}'"
                    if unknown_desc not in unknowns:
                        unknowns.append(unknown_desc)
                        impact_items.append(ImpactItem(
                            target=imp,
                            target_type="dependency",
                            level=ImpactLevel.POTENTIAL,
                            rationale=f"Reference '{imp}' is unresolvable in local repository and may indicate missing dependency",
                            source_reference=df,
                        ))

        # ---------------------------------------------------------------------
        # Step 6: Configuration & Dependency Manifest Impacts
        # ---------------------------------------------------------------------
        for cfg in understanding.configuration_files:
            # If objective mentions config, env, settings, or database
            for kw in objective_keywords:
                if kw in cfg.lower() or ("config" in kw and "config" in cfg.lower()) or ("env" in kw and "env" in cfg.lower()):
                    if cfg not in affected_configuration:
                        affected_configuration.append(cfg)
                        impact_items.append(ImpactItem(
                            target=cfg,
                            target_type="config",
                            level=ImpactLevel.INDIRECT,
                            rationale=f"Configuration file '{cfg}' relates to objective keyword '{kw}'",
                            source_reference=kw,
                        ))
                    break

        for mf in understanding.dependency_manifests:
            for kw in objective_keywords:
                if kw in ("dependency", "package", "install", "upgrade", "manifest", "library"):
                    if mf not in dependency_impacts:
                        dependency_impacts.append(mf)
                        impact_items.append(ImpactItem(
                            target=mf,
                            target_type="dependency",
                            level=ImpactLevel.DIRECT,
                            rationale=f"Dependency manifest '{mf}' impacted by package modification objective",
                            source_reference=kw,
                        ))
                    break

        # ---------------------------------------------------------------------
        # Step 7: Potential Side Effects
        # ---------------------------------------------------------------------
        if len(indirectly_affected_files) > 2:
            potential_side_effects.append(
                f"Modifying '{', '.join(directly_affected_files[:2])}' affects {len(indirectly_affected_files)} downstream consumer file(s)."
            )
        if any("db" in df or "repo" in df or "model" in df for df in directly_affected_files):
            potential_side_effects.append(
                "Changes to data models or repository persistence layer may affect schema or database integrity."
            )
        if affected_interfaces:
            potential_side_effects.append(
                f"Changes to public interface signatures ({', '.join(affected_interfaces[:3])}) may require updating dependent callers."
            )

        # Determine overall confidence
        if unknowns:
            confidence = UnderstandingConfidence.INFERRED
        elif directly_affected_files and (indirectly_affected_files or affected_tests):
            confidence = UnderstandingConfidence.OBSERVED
        else:
            confidence = UnderstandingConfidence.INFERRED

        # Construct ImpactAnalysis model
        analysis_id = new_impact_analysis_id()
        analysis = ImpactAnalysis(
            analysis_id=analysis_id,
            execution_id=exec_id,
            work_order_id=wo_id,
            project_id=proj_id,
            understanding_id=und_id,
            directly_affected_files=directly_affected_files,
            indirectly_affected_files=indirectly_affected_files,
            affected_modules=affected_modules,
            affected_interfaces=affected_interfaces,
            affected_tests=affected_tests,
            affected_configuration=affected_configuration,
            dependency_impacts=dependency_impacts,
            potential_side_effects=potential_side_effects,
            unknowns=unknowns,
            impact_items=impact_items,
            evidence=evidence_list,
            confidence=confidence,
            trace=tr,
        )

        return analysis

    def analyze_context(
        self,
        context: Union[ProgrammerExecutionContext, GitExecutionContext],
        work_order: ProgrammerWorkOrder,
        understanding: CodebaseUnderstanding,
        trace: Optional[dict[str, Any]] = None,
    ) -> ImpactAnalysis:
        """
        Convenience entry point analyzing impact directly from an execution context.
        """
        ws = getattr(context, "workspace", None)
        if ws is None:
            from core.programmer.contracts.identifiers import new_workspace_id
            ws_path = getattr(context, "workspace_path", "")
            ws = ProgrammerWorkspace(
                workspace_id=new_workspace_id(),
                project_id=work_order.project_id,
                work_order_id=work_order.work_order_id,
                root_path=str(ws_path),
                allowed_paths=list(work_order.allowed_paths),
                writable_paths=list(work_order.writable_paths),
                forbidden_paths=list(work_order.forbidden_paths),
            )

        return self.analyze(
            workspace=ws,
            work_order=work_order,
            understanding=understanding,
            execution_id=context.execution_id,
            trace=trace,
        )
