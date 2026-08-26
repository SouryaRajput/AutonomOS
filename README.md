# AutonomOS

**AutonomOS** is an open-source platform for operating virtual, autonomous AI workforces coordinated by a central Manager with human-in-the-loop governance.

---

## 🌟 Vision & Philosophy

AutonomOS is built on the principle that **the LLM is a reasoning engine inside a deterministic runtime, not the runtime itself**.

1. **Deterministic State & Execution**: State transitions, dependency graphs, and worker lifecycles are governed by deterministic state machines, not probabilistic model completions.
2. **Untrusted AI Claims**: Worker self-reports and claims are treated as non-authoritative evidence until verified by deterministic gates.
3. **Context is Not Memory**: Context windows are ephemeral. Persistent project knowledge is externalized in human-readable Markdown files (`.autonomos/`) and indexed in SQLite.
4. **Append-Only Auditing**: Every runtime action generates an immutable event with strict causal lineage (`causation_id`) and execution tracing (`correlation_id`).
5. **Reversible & Inspectable**: All operations produce transparent artifacts, reports, and logs suitable for local inspection and real-time client streaming.

---

## 🏗️ Architecture Overview

```
┌────────────────────────────────────────────────────────┐
│               Workforce Runtime Coordinator            │
│         (Orchestrates tasks, workers, and events)      │
└───────┬──────────────┬──────────────┬──────────────┬───┘
        │              │              │              │
┌───────▼──────┐┌──────▼──────┐┌──────▼──────┐┌──────▼──────┐
│ Task Engine  ││Worker Engine││ Memory Mgmt ││ Event Store │
│ • State Mach ││ • Lifecycle ││ • ProjectMap││ • Monotonic │
│ • DAG Resolver││ • Worker SDK││ • ADR / Repts││ • Causation │
└───────┬──────┘└──────┬──────┘└──────┬──────┘└──────┬──────┘
        │              │              │              │
┌───────▼──────────────▼──────────────▼──────────────▼───┐
│            Persistence Layer (SQLite / Memory)         │
│  WAL Mode • Foreign Keys • Transactional Consistency   │
└────────────────────────────────────────────────────────┘
```

---

## 📁 Repository Structure

```
AutonomOS/
├── core/
│   ├── enums.py             # Domain status, risk, memory, and artifact enums
│   ├── models.py            # Strongly typed dataclass domain models
│   ├── errors.py            # Structured runtime exceptions
│   ├── events/
│   │   ├── types.py         # Event taxonomy and origin sources
│   │   ├── model.py         # Immutable Event dataclass
│   │   └── activity.py      # Product-facing ActivityItem projector
│   ├── memory/
│   │   ├── model.py         # MemoryDocument & ValidationReport models
│   │   └── manager.py       # Atomic Markdown memory manager & reference validator
│   ├── runtime/
│   │   ├── workforce_runtime.py  # Central coordinator
│   │   ├── task_engine.py        # Task lifecycle & assignment engine
│   │   ├── worker_registry.py    # Worker manifest & instance registry
│   │   ├── project_registry.py   # Project workspace registry
│   │   └── artifact_registry.py  # Artifact tracker & file writer
│   ├── storage/
│   │   ├── base.py          # Abstract Store interface
│   │   ├── sqlite_store.py  # Embedded SQLite persistence (WAL mode)
│   │   └── memory_store.py  # Thread-safe in-memory store for unit tests
│   ├── task/
│   │   ├── state_machine.py # Deterministic task state transitions
│   │   └── dependencies.py  # DAG cycle detection & readiness resolver
│   └── worker/
│       └── state_machine.py # Deterministic worker state transitions
├── pkg/
│   └── sdk/
│       └── worker.py        # Worker base class & runtime context interfaces
├── workers/
│   └── dummy_worker.py      # Deterministic test worker implementation
└── test/
    ├── unit/                # 55 unit tests covering all components
    └── integration/         # End-to-end golden path lifecycle integration test
```

---

## 🚀 Quickstart & Testing

### Prerequisites
- Python 3.12+ (standard library only; no external dependencies required for core runtime)

### Running Tests
Run the entire suite of 56 unit and integration tests:

```bash
python3 -m unittest discover -s test -p "test_*.py" -v
```

### Basic Usage Example

```python
from core.runtime.workforce_runtime import WorkforceRuntime
from workers.dummy_worker import DummyWorker

# 1. Initialize runtime with SQLite persistence
runtime = WorkforceRuntime.with_sqlite("autonomos_state.db")

# 2. Create project workspace (automatically scaffolds .autonomos/ memory)
project = runtime.create_project(
    name="My First Project",
    root_path="./workspace",
    description="Automated software engineering workspace",
    project_id="proj-001"
)

# 3. Register an AI Worker
worker = DummyWorker(worker_id="worker.programmer.1")
runtime.register_worker(worker)

# 4. Create and run a task
task = runtime.create_task(
    project_id=project.id,
    title="Implement User Authentication",
    objective="Write JWT auth handler and unit tests",
    task_id="task-001"
)

runtime.assign_task(task.id, worker.get_manifest().id)
output = runtime.run_task(task.id)

print(f"Success: {output.success} | Summary: {output.summary}")

# 5. Inspect the immutable event stream
timeline = runtime.get_task_timeline(task.id)
for event in timeline:
    print(f"[{event.timestamp}] #{event.sequence_number:04d} {event.event_type.value}")
```

---

## 🗺️ Roadmap & Development Stages

- [x] **Stage 0**: Architecture, Contracts & Design Specification
- [x] **Stage 1**: Core Workforce Runtime & Deterministic State Machines
- [x] **Stage 2**: Event & Activity System with Causation Lineage
- [x] **Stage 3**: Persistent Project Memory (`.autonomos/` Markdown Knowledge Base)
- [ ] **Stage 4**: Context Engine & Dynamic Relevance Injection
- [ ] **Stage 5**: Tool Runtime, Permissions & OS Sandboxing
- [ ] **Stage 6**: Specialized AI Workers (Manager, Researcher, Programmer, Tester)
- [ ] **Stage 7**: Cross-Platform Flutter Application & Realtime Client

---

## 📄 License

Open-source under the Apache 2.0 License.
