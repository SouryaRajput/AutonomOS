# AutonomOS — Stage 1 Core Workforce Runtime Specification & Documentation

## 1. Overview & Purpose

Stage 1 establishes the **deterministic foundation** of AutonomOS. The primary goal is to provide a rock-solid, observable, and persistent runtime environment where tasks can be created, dependencies resolved, workers assigned, and work executed through formal lifecycle transitions—all without relying on LLM models or non-deterministic behavior.

In Stage 1, all execution is verified using the `DummyWorker`. Real intelligent workers (`Manager`, `Programmer`, `Researcher`, `Tester`) will be introduced in subsequent stages as drop-in implementations of the universal `Worker` interface.

---

## 2. Architecture & Components

```
AutonomOS Core Runtime
├── core/
│   ├── enums.py                    # Domain status enums (TaskStatus, WorkerStatus, etc.)
│   ├── errors.py                   # Structured error classes (AutonomOSError hierarchy)
│   ├── models.py                   # Domain dataclasses (Project, WorkerManifest, Task, etc.)
│   ├── storage/
│   │   ├── base.py                 # Abstract Store interface
│   │   ├── sqlite_store.py         # Thread-safe embedded SQLite persistence (WAL mode)
│   │   └── memory_store.py         # In-memory store for high-speed unit testing
│   ├── task/
│   │   ├── state_machine.py        # 11-State deterministic Task State Machine
│   │   └── dependencies.py         # Dependency graph with cycle detection (DFS)
│   ├── worker/
│   │   └── state_machine.py        # Worker lifecycle state machine
│   └── runtime/
│       ├── project_registry.py     # Project workspace management
│       ├── worker_registry.py      # Worker registration and state tracking
│       ├── artifact_registry.py    # Artifact registration, checksums, and disk writes
│       ├── task_engine.py          # Task CRUD, assignments, and hierarchical trees
│       └── workforce_runtime.py    # Central coordinator & event logger
├── pkg/
│   └── sdk/
│       └── worker.py               # Public Worker & WorkerRuntimeContext contracts
├── workers/
│   └── dummy_worker.py             # Deterministic test worker implementation
├── test/
│   ├── unit/                       # Unit tests for all individual subsystems
│   └── integration/                # End-to-End Golden Path integration tests
└── docs/
    └── stage_1_runtime.md          # This document
```

---

## 3. Core Entities & Data Models

### `Project`
Represents the root workspace boundary on the filesystem.
- `id`: Unique project identifier.
- `name`: Human-readable project name.
- `root_path`: Canonical filesystem path.
- `status`: `ACTIVE`, `PAUSED`, `ARCHIVED`.
- `configuration`: JSON dictionary for project-level settings (budgets, models, limits).

### `WorkerManifest`
Static metadata and security declaration for a worker.
- `id`: Unique identifier (e.g. `worker.dummy`, `worker.programmer`).
- `name`, `role`, `description`, `version`.
- `capabilities`: List of capability tags (e.g. `["simulation", "code_generation"]`).
- `permissions`: Granted permission boundaries.
- `tools`: Allowed tool calls.
- `model_policy`: Target model preferences.
- `status`: Current lifecycle state (`REGISTERED`, `IDLE`, `ASSIGNED`, `RUNNING`, `REPORTING`, `FAILED`, `ERROR`, `TERMINATED`).
- `active_task_id`: ID of currently executing task (if any).

### `Task`
A discrete unit of work within a project.
- `id`, `project_id`, `parent_task_id`.
- `title`, `objective`.
- `status`: `PENDING`, `READY`, `ASSIGNED`, `RUNNING`, `BLOCKED`, `VERIFYING`, `AWAITING_APPROVAL`, `COMPLETED`, `FAILED`, `CANCELLED`, `RETRYING`.
- `priority`: Priority integer (1-5).
- `risk`: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`.
- `assigned_worker`: Worker ID currently assigned.
- `dependencies`: List of prerequisite task IDs.
- `success_criteria`: Structured verification targets.
- `artifacts`: List of generated artifact IDs.
- `attempts`, `max_attempts`: Retry tracking counters.
- `started_at`, `completed_at`: Timestamps.

### `Artifact`
A tangible deliverable output produced during task execution.
- `id`, `project_id`, `task_id`, `worker_id`.
- `type`: `FILE`, `REPORT`, `PATCH`, `OUTPUT`, `RESOURCE`.
- `path`: Relative or absolute file path.
- `checksum`: SHA-256 hash of the content.
- `metadata`: Key-value properties.

---

## 4. State Machines & Deterministic Lifecycle

### Task State Transitions

```
[ PENDING ] ──(Dependencies Met)──> [ READY ] ──(Assign Worker)──> [ ASSIGNED ]
                                                                        │
                                                                 (Start Execution)
                                                                        ▼
[ COMPLETED ] <──(Success)────────── [ RUNNING ] <────────────── [ RETRYING ]
      ▲                                   │                              ▲
      │                              (Failure)                           │
      │                                   ▼                              │
      └─────────(Retry Policy Exceeded)── [ FAILED ] ──(Attempts < Max)──┘
```

- **Rule Enforced**: `COMPLETED` and `CANCELLED` are terminal states. A completed task cannot return to `RUNNING`.
- **Validation**: Any invalid transition raises a structured `InvalidTaskTransitionError`.

### Worker State Transitions

```
[ REGISTERED ] ──> [ IDLE ] ──(Assign Task)──> [ ASSIGNED ] ──(Start)──> [ RUNNING ]
                      ▲                                                       │
                      │                                                   (Finished)
                      │                                                       ▼
                      └──(Reset)────────── [ FAILED ] <──(Failure)── [ REPORTING ]
```

- **Concurrency Rule**: A worker in `RUNNING` or `ASSIGNED` state cannot be assigned another task concurrently (`WorkerBusyError`).

---

## 5. Persistence & Restart Survival

All entities and relational edges are persisted in an embedded SQLite database using WAL (Write-Ahead Logging) mode and foreign key constraints:
- `projects`
- `workers`
- `tasks`
- `dependencies`
- `artifacts`

**Process Restart Guarantee**: When the runtime shuts down and restarts, reopening the SQLite database reloads all existing projects, workers, tasks, dependencies, and artifacts with zero state degradation.

---

## 6. Verification of the 10 Deterministic Runtime Rules

| Rule # | Runtime Rule | Enforcement Mechanism |
| :--- | :--- | :--- |
| **Rule 1** | Nonexistent worker cannot receive a task | `TaskEngine.assign_task` checks `worker_registry.get_worker` -> raises `WorkerNotFoundError`. |
| **Rule 2** | Nonexistent task cannot be executed | `WorkforceRuntime.run_task` queries `task_engine.get_task` -> raises `TaskNotFoundError`. |
| **Rule 3** | Blocked task cannot run | `TaskStateMachine` forbids transition from `BLOCKED` or `PENDING` to `RUNNING`. |
| **Rule 4** | Incomplete dependencies prevent readiness | `DependencyResolver.validate_task_readiness` blocks assignment and execution -> raises `DependencyNotSatisfiedError`. |
| **Rule 5** | Completed task cannot silently return to running | `TaskStateMachine.ALLOWED_TRANSITIONS[TaskStatus.COMPLETED]` is empty -> raises `TaskAlreadyCompletedError`. |
| **Rule 6** | Worker cannot execute two tasks simultaneously | `TaskEngine.assign_task` verifies worker is in `IDLE` state -> raises `WorkerBusyError`. |
| **Rule 7** | Workers cannot bypass Task Engine | Workers only execute within `WorkerRuntimeContext`; all status changes are owned by `WorkforceRuntime`. |
| **Rule 8** | State transitions must be validated | `TaskStateMachine.validate_and_transition` and `WorkerStateMachine.validate_and_transition` validate all state changes against allowed graph. |
| **Rule 9** | Important state survives restart | SQLite persistence with WAL mode tested and verified in `test_persistence_and_process_restart`. |
| **Rule 10**| Runtime does not depend on an LLM | All operations in Stage 1 use deterministic code and `DummyWorker`. |

---

## 7. Stage 1 Verification & Test Results

```
test_complete_golden_path (test_end_to_end_golden_path) ................. ok
test_get_nonexistent_artifact_fails (test_artifact_registry) ............ ok
test_register_and_get_artifact_with_file_write (test_artifact_registry) . ok
test_cycle_detection_direct (test_dependencies) ......................... ok
test_cycle_detection_transitive (test_dependencies) ..................... ok
test_linear_dependency_resolution (test_dependencies) .................. ok
test_multiple_dependencies (test_dependencies) ......................... ok
test_create_and_get_project (test_project_registry) ..................... ok
test_create_duplicate_project_fails (test_project_registry) ............. ok
test_delete_project (test_project_registry) ............................. ok
test_get_nonexistent_project_fails (test_project_registry) .............. ok
test_list_projects (test_project_registry) .............................. ok
test_update_project (test_project_registry) ............................. ok
test_persistence_and_process_restart (test_sqlite_persistence) .......... ok
test_parent_and_child_task_creation (test_task_hierarchy) ............... ok
test_task_hierarchy_tree (test_task_hierarchy) .......................... ok
test_blocked_and_unblock_transitions (test_task_state_machine) .......... ok
test_cancellation_from_running (test_task_state_machine) ................ ok
test_completed_task_cannot_return_to_running (test_task_state_machine) .. ok
test_invalid_transition_from_pending_to_completed (task_state_machine) .. ok
test_retry_lifecycle (test_task_state_machine) .......................... ok
test_valid_forward_lifecycle (test_task_state_machine) .................. ok
test_get_nonexistent_worker_fails (test_worker_registry) ................ ok
test_invalid_worker_transition_fails (test_worker_registry) ............. ok
test_register_and_get_worker (test_worker_registry) ..................... ok
test_register_duplicate_worker_fails (test_worker_registry) ............. ok
test_worker_lifecycle_transitions (test_worker_registry) ................ ok
test_full_worker_lifecycle (test_worker_state_machine) .................. ok
test_invalid_transition_rejected (test_worker_state_machine) ............ ok
test_worker_failure_and_recovery (test_worker_state_machine) ............ ok

30 tests passed in 0.063s.
```

---

## 8. Readiness for Stage 2

The Stage 1 foundation is complete, fully tested, and ready for Stage 2. Stage 2 will introduce:
1. **Tool Runtime & Sandboxing**: OS-level sandboxing, file system isolation, and git checkpoint rollback.
2. **Deterministic Verification Engine**: Static analysis and test execution harnesses to validate AI claims with proof.
3. **Inference Gateway & Capability Router**: Pluggable model providers (OpenRouter, Gemini, Groq, local models) with token budgeting and fallback chains.
