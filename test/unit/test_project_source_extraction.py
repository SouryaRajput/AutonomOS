"""
Unit tests for Project Source and Contract Extraction (Phase 1 / Part 8 / Step 5).
"""
import pytest

from core.research.contracts.evidence import EvidenceProvenance
from core.research.errors import ProjectCancelledError
from core.research.project.extractor import (
    ExtractedProjectFile,
    ProjectContractClassifier,
    ProjectExtractionOptions,
    ProjectSourceExtractor,
)
from core.research.project.models import (
    LineRange,
    ProjectContext,
    ProjectContract,
    ProjectContractType,
    ProjectIdentity,
    ProjectSourceMaterial,
    ProjectSymbol,
)
from core.research.repo.structure import (
    CodeClass,
    CodeFunction,
    CodeParsingStatus,
    SymbolKind,
)


def _make_dummy_provenance() -> EvidenceProvenance:
    return EvidenceProvenance(
        request_id="req-test-1",
        crawler_task_id="task-test-1",
        crawler_id="crawler.project.test",
        question_id="q-test-1",
    )


def test_python_source_symbol_extraction():
    code = """
import os
from typing import Optional

GLOBAL_MAX_RETRIES: int = 5

class ServiceManager:
    \"\"\"Manages background service instances.\"\"\"
    def __init__(self, name: str):
        self.name = name

    async def start(self) -> bool:
        \"\"\"Start the managed service.\"\"\"
        return True

def standalone_helper(x: int, y: int) -> int:
    \"\"\"Calculates sum.\"\"\"
    return x + y
"""
    extracted = ProjectSourceExtractor.extract_file(
        file_path="src/manager.py",
        content=code,
        provenance=_make_dummy_provenance(),
    )

    assert extracted.is_supported is True
    assert extracted.parsing_status == CodeParsingStatus.PARSED
    assert extracted.file_path == "src/manager.py"
    assert extracted.language == "python"

    # Symbols check
    names = {s.name for s in extracted.symbols}
    assert "ServiceManager" in names
    assert "start" in names
    assert "standalone_helper" in names
    assert "GLOBAL_MAX_RETRIES" in names

    # Method check
    start_sym = next(s for s in extracted.symbols if s.name == "start")
    assert start_sym.symbol_type == SymbolKind.METHOD
    assert start_sym.parent_symbol == "ServiceManager"
    assert "Start the managed service." in (start_sym.docstring or "")

    # Top-level function check
    fn_sym = next(s for s in extracted.symbols if s.name == "standalone_helper")
    assert fn_sym.symbol_type == SymbolKind.FUNCTION
    assert fn_sym.parent_symbol is None

    # Constant check
    const_sym = next(s for s in extracted.symbols if s.name == "GLOBAL_MAX_RETRIES")
    assert const_sym.symbol_type == SymbolKind.CONSTANT


def test_contract_classification_protocol():
    code = """
from typing import Protocol, runtime_checkable

@runtime_checkable
class LifecycleWatcher(Protocol):
    \"\"\"Watches lifecycle transitions.\"\"\"
    def on_start(self) -> None: ...
    def on_stop(self) -> None: ...
"""
    extracted = ProjectSourceExtractor.extract_file(
        file_path="src/contracts/lifecycle.py",
        content=code,
    )
    assert len(extracted.contracts) == 1
    contract = extracted.contracts[0]
    assert contract.name == "LifecycleWatcher"
    assert contract.contract_type == ProjectContractType.PROTOCOL
    assert "on_start" in contract.members
    assert "on_stop" in contract.members
    assert "Watches lifecycle transitions." in (contract.docstring or "")


def test_contract_classification_interface():
    code = """
from abc import ABC, abstractmethod

class IDataRepository(ABC):
    \"\"\"Abstract interface for data repository.\"\"\"
    @abstractmethod
    def get_by_id(self, item_id: str) -> dict:
        pass
"""
    extracted = ProjectSourceExtractor.extract_file(
        file_path="src/interfaces/repo.py",
        content=code,
    )
    assert len(extracted.contracts) == 1
    contract = extracted.contracts[0]
    assert contract.name == "IDataRepository"
    assert contract.contract_type == ProjectContractType.INTERFACE
    assert "get_by_id" in contract.members


def test_contract_classification_typed_model():
    code = """
from dataclasses import dataclass
from typing import Optional

@dataclass
class UserAccountModel:
    \"\"\"Typed representation of a user account.\"\"\"
    user_id: str
    email: str
    is_active: bool = True
"""
    extracted = ProjectSourceExtractor.extract_file(
        file_path="src/models/user.py",
        content=code,
    )
    assert len(extracted.contracts) == 1
    contract = extracted.contracts[0]
    assert contract.name == "UserAccountModel"
    assert contract.contract_type == ProjectContractType.TYPED_MODEL


def test_contract_classification_lifecycle_enum():
    code = """
from enum import Enum

class WorkerState(str, Enum):
    \"\"\"Current operational state of a worker.\"\"\"
    IDLE = "idle"
    BUSY = "busy"
    STOPPED = "stopped"
"""
    extracted = ProjectSourceExtractor.extract_file(
        file_path="src/enums/states.py",
        content=code,
    )
    assert len(extracted.contracts) == 1
    contract = extracted.contracts[0]
    assert contract.name == "WorkerState"
    assert contract.contract_type == ProjectContractType.LIFECYCLE_ENUM


def test_contract_classification_config_schema():
    code = """
class DatabaseConfig:
    \"\"\"Configuration schema for database connections.\"\"\"
    host: str = "localhost"
    port: int = 5432
    max_connections: int = 20
"""
    extracted = ProjectSourceExtractor.extract_file(
        file_path="src/config/database.py",
        content=code,
    )
    assert len(extracted.contracts) == 1
    contract = extracted.contracts[0]
    assert contract.name == "DatabaseConfig"
    assert contract.contract_type == ProjectContractType.CONFIG_SCHEMA


def test_contract_classification_event_contract():
    code = """
class OrderSubmittedEvent:
    \"\"\"Event dispatched when a new order is submitted.\"\"\"
    order_id: str
    customer_id: str
    total_amount: float
"""
    extracted = ProjectSourceExtractor.extract_file(
        file_path="src/events/order.py",
        content=code,
    )
    assert len(extracted.contracts) == 1
    contract = extracted.contracts[0]
    assert contract.name == "OrderSubmittedEvent"
    assert contract.contract_type == ProjectContractType.EVENT_CONTRACT


def test_contract_classification_registry_definition():
    code = """
class ProviderRegistry:
    \"\"\"Central registry for provider instances.\"\"\"
    def __init__(self):
        self._providers = {}

    def register(self, name: str, provider: object) -> None:
        self._providers[name] = provider
"""
    extracted = ProjectSourceExtractor.extract_file(
        file_path="src/registry/providers.py",
        content=code,
    )
    assert len(extracted.contracts) == 1
    contract = extracted.contracts[0]
    assert contract.name == "ProviderRegistry"
    assert contract.contract_type == ProjectContractType.REGISTRY_DEFINITION


def test_contract_to_evidence_item():
    contract = ProjectContract(
        contract_id="contract-test-auth",
        name="IAuthenticator",
        contract_type=ProjectContractType.INTERFACE,
        file_path="src/auth.py",
        line_range=LineRange(start_line=10, end_line=30),
        members=["authenticate", "refresh_token"],
        docstring="Authenticator interface contract.",
    )
    ev = contract.to_evidence_item(
        request_id="req-123",
        crawler_task_id="ctask-456",
        crawler_id="crawler.proj",
    )
    assert ev.evidence_id == "ev-contract-contract-test-auth"
    assert "Interface contract 'IAuthenticator' defined in src/auth.py" in ev.extracted_fact
    assert ev.metadata["members"] == ["authenticate", "refresh_token"]
    assert ev.metadata["contract_type"] == "interface"


def test_unsupported_language_fallback():
    extracted = ProjectSourceExtractor.extract_file(
        file_path="scripts/script.lua",
        content="function hello()\n  print('Hello')\nend",
    )
    assert extracted.is_supported is False
    assert extracted.parsing_status == CodeParsingStatus.FALLBACK_UNSUPPORTED
    assert len(extracted.symbols) == 0
    assert len(extracted.contracts) == 0
    assert extracted.content_hash != ""
    assert "not supported for AST extraction" in (extracted.error_message or "")


def test_binary_and_null_bytes_handling():
    # Known binary extension
    extracted_bin = ProjectSourceExtractor.extract_file(
        file_path="assets/logo.png",
        content="fake binary png data",
    )
    assert extracted_bin.is_supported is False
    assert extracted_bin.parsing_status == CodeParsingStatus.FALLBACK_UNSUPPORTED

    # Null byte in text
    extracted_null = ProjectSourceExtractor.extract_file(
        file_path="src/corrupted.py",
        content="def hello():\x00 pass",
    )
    assert extracted_null.is_supported is False
    assert extracted_null.parsing_status == CodeParsingStatus.FALLBACK_UNSUPPORTED


def test_malformed_syntax_resilience():
    bad_python = "def broken_func(:\n  this is invalid syntax !!!"
    extracted = ProjectSourceExtractor.extract_file(
        file_path="src/broken.py",
        content=bad_python,
    )
    assert extracted.is_supported is True
    # Should be handled gracefully with PARTIAL or MALFORMED_SYNTAX status
    assert extracted.parsing_status in (CodeParsingStatus.PARTIAL, CodeParsingStatus.MALFORMED_SYNTAX)
    assert extracted.error_message is not None


def test_size_budget_truncation():
    large_content = "x = 1\n" * 1000
    options = ProjectExtractionOptions(max_parse_bytes=100)
    extracted = ProjectSourceExtractor.extract_file(
        file_path="src/large.py",
        content=large_content,
        options=options,
    )
    assert extracted.parsing_status == CodeParsingStatus.TRUNCATED
    assert "exceeds maximum parse limit" in (extracted.error_message or "")
    assert len(extracted.symbols) == 0


def test_max_symbols_budget():
    code = "\n".join([f"def func_{i}(): pass" for i in range(15)])
    options = ProjectExtractionOptions(max_symbols_per_file=5)
    extracted = ProjectSourceExtractor.extract_file(
        file_path="src/many_funcs.py",
        content=code,
        options=options,
    )
    assert len(extracted.symbols) == 5


def test_cancellation_support():
    cancelled = True
    options = ProjectExtractionOptions(is_cancelled=lambda: cancelled)
    with pytest.raises(ProjectCancelledError) as exc_info:
        ProjectSourceExtractor.extract_file(
            file_path="src/test.py",
            content="def test(): pass",
            options=options,
        )
    assert "cancelled" in str(exc_info.value).lower()


def test_typescript_extraction():
    ts_code = """
export interface IUserService {
    getUser(id: string): Promise<User>;
}

export class UserManager implements IUserService {
    getUser(id: string): Promise<User> {
        return null;
    }
}
"""
    extracted = ProjectSourceExtractor.extract_file(
        file_path="src/user.ts",
        content=ts_code,
    )
    assert extracted.is_supported is True
    assert extracted.language in ("typescript", "ts")
    symbol_names = {s.name for s in extracted.symbols}
    assert "IUserService" in symbol_names or "UserManager" in symbol_names


def test_go_extraction():
    go_code = """
package server

type Service interface {
    Run() error
}

type Server struct {
    Host string
    Port int
}

func (s *Server) Run() error {
    return nil
}
"""
    extracted = ProjectSourceExtractor.extract_file(
        file_path="pkg/server.go",
        content=go_code,
    )
    assert extracted.is_supported is True
    assert extracted.language == "go"
    names = {s.name for s in extracted.symbols}
    assert "Service" in names or "Server" in names


def test_rust_extraction():
    rs_code = """
pub trait Worker {
    fn execute(&self) -> Result<(), String>;
}

pub struct BackgroundWorker {
    pub id: u64,
}

impl Worker for BackgroundWorker {
    fn execute(&self) -> Result<(), String> {
        Ok(())
    }
}
"""
    extracted = ProjectSourceExtractor.extract_file(
        file_path="src/worker.rs",
        content=rs_code,
    )
    assert extracted.is_supported is True
    assert extracted.language in ("rust", "rs")
    names = {s.name for s in extracted.symbols}
    assert "Worker" in names or "BackgroundWorker" in names


def test_populate_context_and_batch_extract():
    materials = [
        ProjectSourceMaterial(
            material_id="mat-1",
            file_path="src/a.py",
            content="class ContractA:\n    pass\n",
        ),
        ProjectSourceMaterial(
            material_id="mat-2",
            file_path="src/b.py",
            content="def func_b():\n    return 42\n",
        ),
    ]

    extracted_files = ProjectSourceExtractor.extract_materials(materials)
    assert len(extracted_files) == 2

    context = ProjectContext(
        identity=ProjectIdentity(project_id="p-test", project_root="/workspace"),
    )
    ProjectSourceExtractor.populate_context(context, extracted_files)

    sym_names = {s.name for s in context.symbols}
    assert "ContractA" in sym_names
    assert "func_b" in sym_names


def test_extracted_project_file_serialization_round_trip():
    original = ExtractedProjectFile(
        file_path="src/test.py",
        language="python",
        parsing_status=CodeParsingStatus.PARSED,
        is_supported=True,
        symbols=[
            ProjectSymbol(
                name="TestClass",
                symbol_type=SymbolKind.CLASS,
                file_path="src/test.py",
                line_range=LineRange(start_line=1, end_line=10),
            )
        ],
        contracts=[
            ProjectContract(
                contract_id="contract-1",
                name="TestClass",
                contract_type=ProjectContractType.TYPED_MODEL,
                file_path="src/test.py",
            )
        ],
        line_count=10,
        size_bytes=250,
        content_hash="abc123hash",
    )
    data = original.to_dict()
    restored = ExtractedProjectFile.from_dict(data)

    assert restored.file_path == original.file_path
    assert restored.language == original.language
    assert restored.parsing_status == original.parsing_status
    assert len(restored.symbols) == 1
    assert restored.symbols[0].name == "TestClass"
    assert len(restored.contracts) == 1
    assert restored.contracts[0].contract_type == ProjectContractType.TYPED_MODEL
