import json
from pathlib import Path
import sqlite3
import threading
from typing import Optional

from core.enums import ArtifactType, DependencyType, MemoryType, ProjectStatus, RiskLevel, TaskStatus, WorkerStatus
from core.errors import PersistenceError
from core.events.model import Event
from core.events.types import EventSource, EventType
from core.memory.model import MemoryDocument
from core.models import Artifact, Dependency, Project, Task, WorkerManifest, utc_now
from core.storage.base import Store


class SQLiteStore(Store):
    """Embedded SQLite persistence implementation for AutonomOS."""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._lock = threading.RLock()
        
        if db_path != ":memory:":
            path_obj = Path(db_path)
            path_obj.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
            isolation_level=None,  # Autocommit mode for explicit transaction control
        )
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("PRAGMA foreign_keys = ON;")
            cursor.execute("PRAGMA journal_mode = WAL;")
            cursor.execute("PRAGMA synchronous = NORMAL;")

            # Projects table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    root_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    configuration TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

            # Workers table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS workers (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    description TEXT NOT NULL,
                    version TEXT NOT NULL,
                    capabilities TEXT NOT NULL,
                    permissions TEXT NOT NULL,
                    tools TEXT NOT NULL,
                    model_policy TEXT NOT NULL,
                    status TEXT NOT NULL,
                    active_task_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

            # Tasks table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    parent_task_id TEXT,
                    title TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    risk TEXT NOT NULL,
                    assigned_worker TEXT,
                    dependencies TEXT NOT NULL,
                    success_criteria TEXT NOT NULL,
                    context_references TEXT NOT NULL,
                    artifacts TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                    FOREIGN KEY (parent_task_id) REFERENCES tasks(id) ON DELETE SET NULL,
                    FOREIGN KEY (assigned_worker) REFERENCES workers(id) ON DELETE SET NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_project_id ON tasks(project_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id);")

            # Dependencies table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS dependencies (
                    id TEXT PRIMARY KEY,
                    dependent_task_id TEXT NOT NULL,
                    prerequisite_task_id TEXT NOT NULL,
                    dependency_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (dependent_task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                    FOREIGN KEY (prerequisite_task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                    UNIQUE(dependent_task_id, prerequisite_task_id)
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_deps_dependent ON dependencies(dependent_task_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_deps_prereq ON dependencies(prerequisite_task_id);")

            # Artifacts table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    path TEXT NOT NULL,
                    description TEXT NOT NULL,
                    checksum TEXT,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                    FOREIGN KEY (worker_id) REFERENCES workers(id) ON DELETE CASCADE
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_project ON artifacts(project_id);")

            # Events table (Stage 2)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    sequence_number INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    source TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    causation_id TEXT,
                    project_id TEXT,
                    task_id TEXT,
                    worker_id TEXT,
                    artifact_id TEXT,
                    schema_version INTEGER NOT NULL DEFAULT 1,
                    payload TEXT NOT NULL,
                    metadata TEXT NOT NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_event_id ON events(event_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_worker ON events(worker_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_correlation ON events(correlation_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_causation ON events(causation_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);")

            # Memory Documents table (Stage 3)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS memory_documents (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    content TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    references_json TEXT NOT NULL,
                    related_task_id TEXT,
                    related_worker_id TEXT,
                    version INTEGER NOT NULL DEFAULT 1,
                    checksum TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                    UNIQUE(project_id, relative_path)
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_project ON memory_documents(project_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_documents(memory_type);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_task ON memory_documents(related_task_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_path ON memory_documents(project_id, relative_path);")

            # Plans table (Stage 10)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS plans (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    tasks TEXT NOT NULL,
                    milestones TEXT NOT NULL,
                    dependencies TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_plans_project ON plans(project_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_plans_status ON plans(status);")

            # Manager Decisions table (Stage 10)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS manager_decisions (
                    decision_id TEXT PRIMARY KEY,
                    cycle_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    reasoning_summary TEXT NOT NULL,
                    actions TEXT NOT NULL,
                    assumptions TEXT NOT NULL,
                    risks TEXT NOT NULL,
                    confidence_level TEXT NOT NULL,
                    plan_update TEXT,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_decisions_project ON manager_decisions(project_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_decisions_cycle ON manager_decisions(cycle_id);")

    # Project Operations
    def save_project(self, project: Project) -> None:
        with self._lock:
            try:
                cursor = self._conn.cursor()
                cursor.execute("""
                    INSERT INTO projects (id, name, description, root_path, status, configuration, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        description = excluded.description,
                        root_path = excluded.root_path,
                        status = excluded.status,
                        configuration = excluded.configuration,
                        updated_at = excluded.updated_at;
                """, (
                    project.id,
                    project.name,
                    project.description,
                    project.root_path,
                    project.status.value if isinstance(project.status, ProjectStatus) else project.status,
                    json.dumps(project.configuration),
                    project.created_at,
                    project.updated_at or utc_now(),
                ))
            except Exception as e:
                raise PersistenceError("save_project", str(e)) from e

    def get_project(self, project_id: str) -> Optional[Project]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM projects WHERE id = ?;", (project_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return Project(
                id=row["id"],
                name=row["name"],
                description=row["description"],
                root_path=row["root_path"],
                status=ProjectStatus(row["status"]),
                configuration=json.loads(row["configuration"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def list_projects(self) -> list[Project]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM projects ORDER BY created_at ASC;")
            rows = cursor.fetchall()
            return [
                Project(
                    id=row["id"],
                    name=row["name"],
                    description=row["description"],
                    root_path=row["root_path"],
                    status=ProjectStatus(row["status"]),
                    configuration=json.loads(row["configuration"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
                for row in rows
            ]

    def delete_project(self, project_id: str) -> bool:
        with self._lock:
            try:
                cursor = self._conn.cursor()
                cursor.execute("DELETE FROM projects WHERE id = ?;", (project_id,))
                return cursor.rowcount > 0
            except Exception as e:
                raise PersistenceError("delete_project", str(e)) from e

    # Worker Operations
    def save_worker(self, worker: WorkerManifest) -> None:
        with self._lock:
            try:
                cursor = self._conn.cursor()
                cursor.execute("""
                    INSERT INTO workers (id, name, role, description, version, capabilities, permissions, tools, model_policy, status, active_task_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        role = excluded.role,
                        description = excluded.description,
                        version = excluded.version,
                        capabilities = excluded.capabilities,
                        permissions = excluded.permissions,
                        tools = excluded.tools,
                        model_policy = excluded.model_policy,
                        status = excluded.status,
                        active_task_id = excluded.active_task_id,
                        updated_at = excluded.updated_at;
                """, (
                    worker.id,
                    worker.name,
                    worker.role,
                    worker.description,
                    worker.version,
                    json.dumps(worker.capabilities),
                    json.dumps(worker.permissions),
                    json.dumps(worker.tools),
                    json.dumps(worker.model_policy),
                    worker.status.value if isinstance(worker.status, WorkerStatus) else worker.status,
                    worker.active_task_id,
                    worker.created_at,
                    worker.updated_at or utc_now(),
                ))
            except Exception as e:
                raise PersistenceError("save_worker", str(e)) from e

    def get_worker(self, worker_id: str) -> Optional[WorkerManifest]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM workers WHERE id = ?;", (worker_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return WorkerManifest(
                id=row["id"],
                name=row["name"],
                role=row["role"],
                description=row["description"],
                version=row["version"],
                capabilities=json.loads(row["capabilities"]),
                permissions=json.loads(row["permissions"]),
                tools=json.loads(row["tools"]),
                model_policy=json.loads(row["model_policy"]),
                status=WorkerStatus(row["status"]),
                active_task_id=row["active_task_id"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def list_workers(self) -> list[WorkerManifest]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM workers ORDER BY name ASC;")
            rows = cursor.fetchall()
            return [
                WorkerManifest(
                    id=row["id"],
                    name=row["name"],
                    role=row["role"],
                    description=row["description"],
                    version=row["version"],
                    capabilities=json.loads(row["capabilities"]),
                    permissions=json.loads(row["permissions"]),
                    tools=json.loads(row["tools"]),
                    model_policy=json.loads(row["model_policy"]),
                    status=WorkerStatus(row["status"]),
                    active_task_id=row["active_task_id"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
                for row in rows
            ]

    def delete_worker(self, worker_id: str) -> bool:
        with self._lock:
            try:
                cursor = self._conn.cursor()
                cursor.execute("DELETE FROM workers WHERE id = ?;", (worker_id,))
                return cursor.rowcount > 0
            except Exception as e:
                raise PersistenceError("delete_worker", str(e)) from e

    def update_worker_status(self, worker_id: str, status: WorkerStatus, active_task_id: Optional[str] = None) -> None:
        with self._lock:
            try:
                cursor = self._conn.cursor()
                status_str = status.value if isinstance(status, WorkerStatus) else status
                cursor.execute("""
                    UPDATE workers
                    SET status = ?, active_task_id = ?, updated_at = ?
                    WHERE id = ?;
                """, (status_str, active_task_id, utc_now(), worker_id))
            except Exception as e:
                raise PersistenceError("update_worker_status", str(e)) from e

    # Task Operations
    def save_task(self, task: Task) -> None:
        with self._lock:
            try:
                cursor = self._conn.cursor()
                cursor.execute("""
                    INSERT INTO tasks (
                        id, project_id, parent_task_id, title, objective, status, priority, risk,
                        assigned_worker, dependencies, success_criteria, context_references, artifacts,
                        attempts, max_attempts, created_at, started_at, completed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        parent_task_id = excluded.parent_task_id,
                        title = excluded.title,
                        objective = excluded.objective,
                        status = excluded.status,
                        priority = excluded.priority,
                        risk = excluded.risk,
                        assigned_worker = excluded.assigned_worker,
                        dependencies = excluded.dependencies,
                        success_criteria = excluded.success_criteria,
                        context_references = excluded.context_references,
                        artifacts = excluded.artifacts,
                        attempts = excluded.attempts,
                        max_attempts = excluded.max_attempts,
                        started_at = excluded.started_at,
                        completed_at = excluded.completed_at;
                """, (
                    task.id,
                    task.project_id,
                    task.parent_task_id,
                    task.title,
                    task.objective,
                    task.status.value if isinstance(task.status, TaskStatus) else task.status,
                    task.priority,
                    task.risk.value if isinstance(task.risk, RiskLevel) else task.risk,
                    task.assigned_worker,
                    json.dumps(task.dependencies),
                    json.dumps(task.success_criteria),
                    json.dumps(task.context_references),
                    json.dumps(task.artifacts),
                    task.attempts,
                    task.max_attempts,
                    task.created_at,
                    task.started_at,
                    task.completed_at,
                ))
            except Exception as e:
                raise PersistenceError("save_task", str(e)) from e

    def get_task(self, task_id: str) -> Optional[Task]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_task(row)

    def list_tasks(self, project_id: Optional[str] = None, status: Optional[TaskStatus] = None) -> list[Task]:
        with self._lock:
            cursor = self._conn.cursor()
            query = "SELECT * FROM tasks WHERE 1=1"
            params = []
            if project_id:
                query += " AND project_id = ?"
                params.append(project_id)
            if status:
                status_str = status.value if isinstance(status, TaskStatus) else status
                query += " AND status = ?"
                params.append(status_str)
            query += " ORDER BY priority DESC, created_at ASC;"
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [self._row_to_task(row) for row in rows]

    def get_tasks_by_parent(self, parent_task_id: str) -> list[Task]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE parent_task_id = ? ORDER BY created_at ASC;", (parent_task_id,))
            rows = cursor.fetchall()
            return [self._row_to_task(row) for row in rows]

    def delete_task(self, task_id: str) -> bool:
        with self._lock:
            try:
                cursor = self._conn.cursor()
                cursor.execute("DELETE FROM tasks WHERE id = ?;", (task_id,))
                return cursor.rowcount > 0
            except Exception as e:
                raise PersistenceError("delete_task", str(e)) from e

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            project_id=row["project_id"],
            title=row["title"],
            objective=row["objective"],
            parent_task_id=row["parent_task_id"],
            status=TaskStatus(row["status"]),
            priority=row["priority"],
            risk=RiskLevel(row["risk"]),
            assigned_worker=row["assigned_worker"],
            dependencies=json.loads(row["dependencies"]),
            success_criteria=json.loads(row["success_criteria"]),
            context_references=json.loads(row["context_references"]),
            artifacts=json.loads(row["artifacts"]),
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

    # Dependency Operations
    def add_dependency(self, dependency: Dependency) -> None:
        with self._lock:
            try:
                cursor = self._conn.cursor()
                cursor.execute("""
                    INSERT INTO dependencies (id, dependent_task_id, prerequisite_task_id, dependency_type, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(dependent_task_id, prerequisite_task_id) DO UPDATE SET
                        dependency_type = excluded.dependency_type;
                """, (
                    dependency.id,
                    dependency.dependent_task_id,
                    dependency.prerequisite_task_id,
                    dependency.dependency_type.value if isinstance(dependency.dependency_type, DependencyType) else dependency.dependency_type,
                    dependency.created_at,
                ))
            except Exception as e:
                raise PersistenceError("add_dependency", str(e)) from e

    def get_dependencies_for_task(self, task_id: str) -> list[Dependency]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM dependencies WHERE dependent_task_id = ?;", (task_id,))
            rows = cursor.fetchall()
            return [
                Dependency(
                    id=row["id"],
                    dependent_task_id=row["dependent_task_id"],
                    prerequisite_task_id=row["prerequisite_task_id"],
                    dependency_type=DependencyType(row["dependency_type"]),
                    created_at=row["created_at"],
                )
                for row in rows
            ]

    def get_dependents_for_task(self, task_id: str) -> list[Dependency]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM dependencies WHERE prerequisite_task_id = ?;", (task_id,))
            rows = cursor.fetchall()
            return [
                Dependency(
                    id=row["id"],
                    dependent_task_id=row["dependent_task_id"],
                    prerequisite_task_id=row["prerequisite_task_id"],
                    dependency_type=DependencyType(row["dependency_type"]),
                    created_at=row["created_at"],
                )
                for row in rows
            ]

    def remove_dependency(self, dependency_id: str) -> bool:
        with self._lock:
            try:
                cursor = self._conn.cursor()
                cursor.execute("DELETE FROM dependencies WHERE id = ?;", (dependency_id,))
                return cursor.rowcount > 0
            except Exception as e:
                raise PersistenceError("remove_dependency", str(e)) from e

    # Artifact Operations
    def save_artifact(self, artifact: Artifact) -> None:
        with self._lock:
            try:
                cursor = self._conn.cursor()
                cursor.execute("""
                    INSERT INTO artifacts (id, project_id, task_id, worker_id, type, path, description, checksum, metadata, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        path = excluded.path,
                        description = excluded.description,
                        checksum = excluded.checksum,
                        metadata = excluded.metadata;
                """, (
                    artifact.id,
                    artifact.project_id,
                    artifact.task_id,
                    artifact.worker_id,
                    artifact.type.value if isinstance(artifact.type, ArtifactType) else artifact.type,
                    artifact.path,
                    artifact.description,
                    artifact.checksum,
                    json.dumps(artifact.metadata),
                    artifact.created_at,
                ))
            except Exception as e:
                raise PersistenceError("save_artifact", str(e)) from e

    def get_artifact(self, artifact_id: str) -> Optional[Artifact]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM artifacts WHERE id = ?;", (artifact_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return Artifact(
                id=row["id"],
                project_id=row["project_id"],
                task_id=row["task_id"],
                worker_id=row["worker_id"],
                type=ArtifactType(row["type"]),
                path=row["path"],
                description=row["description"],
                checksum=row["checksum"],
                metadata=json.loads(row["metadata"]),
                created_at=row["created_at"],
            )

    def list_artifacts_for_task(self, task_id: str) -> list[Artifact]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM artifacts WHERE task_id = ? ORDER BY created_at ASC;", (task_id,))
            rows = cursor.fetchall()
            return [
                Artifact(
                    id=row["id"],
                    project_id=row["project_id"],
                    task_id=row["task_id"],
                    worker_id=row["worker_id"],
                    type=ArtifactType(row["type"]),
                    path=row["path"],
                    description=row["description"],
                    checksum=row["checksum"],
                    metadata=json.loads(row["metadata"]),
                    created_at=row["created_at"],
                )
                for row in rows
            ]

    def list_artifacts_for_project(self, project_id: str) -> list[Artifact]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM artifacts WHERE project_id = ? ORDER BY created_at ASC;", (project_id,))
            rows = cursor.fetchall()
            return [
                Artifact(
                    id=row["id"],
                    project_id=row["project_id"],
                    task_id=row["task_id"],
                    worker_id=row["worker_id"],
                    type=ArtifactType(row["type"]),
                    path=row["path"],
                    description=row["description"],
                    checksum=row["checksum"],
                    metadata=json.loads(row["metadata"]),
                    created_at=row["created_at"],
                )
                for row in rows
            ]

    # Event Store Operations (Stage 2)
    def append_event(self, event: Event) -> Event:
        """Append an immutable event to the event store. Returns the event with its assigned sequence number."""
        with self._lock:
            try:
                cursor = self._conn.cursor()
                cursor.execute("""
                    INSERT INTO events (
                        event_id, event_type, timestamp, source, correlation_id, causation_id,
                        project_id, task_id, worker_id, artifact_id, schema_version, payload, metadata
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    event.event_id,
                    event.event_type.value if isinstance(event.event_type, EventType) else event.event_type,
                    event.timestamp,
                    event.source.value if isinstance(event.source, EventSource) else event.source,
                    event.correlation_id,
                    event.causation_id,
                    event.project_id,
                    event.task_id,
                    event.worker_id,
                    event.artifact_id,
                    event.schema_version,
                    json.dumps(event.payload),
                    json.dumps(event.metadata),
                ))
                seq = cursor.lastrowid
                event.sequence_number = seq
                return event
            except sqlite3.IntegrityError as e:
                raise PersistenceError("append_event", f"Event with ID '{event.event_id}' already exists (immutability violation): {e}") from e
            except Exception as e:
                raise PersistenceError("append_event", str(e)) from e

    def get_event(self, event_id: str) -> Optional[Event]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM events WHERE event_id = ?;", (event_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_event(row)

    def list_events(
        self,
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        event_types: Optional[list[EventType]] = None,
        since_sequence: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[Event]:
        with self._lock:
            cursor = self._conn.cursor()
            query = "SELECT * FROM events WHERE 1=1"
            params = []
            if project_id:
                query += " AND project_id = ?"
                params.append(project_id)
            if task_id:
                query += " AND task_id = ?"
                params.append(task_id)
            if worker_id:
                query += " AND worker_id = ?"
                params.append(worker_id)
            if correlation_id:
                query += " AND correlation_id = ?"
                params.append(correlation_id)
            if event_types:
                placeholders = ",".join("?" for _ in event_types)
                query += f" AND event_type IN ({placeholders})"
                for et in event_types:
                    params.append(et.value if isinstance(et, EventType) else et)
            if since_sequence is not None:
                query += " AND sequence_number > ?"
                params.append(since_sequence)

            query += " ORDER BY sequence_number ASC"
            if limit:
                query += " LIMIT ?"
                params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [self._row_to_event(row) for row in rows]

    def get_events_by_correlation_id(self, correlation_id: str) -> list[Event]:
        return self.list_events(correlation_id=correlation_id)

    def get_events_by_task(self, task_id: str) -> list[Event]:
        return self.list_events(task_id=task_id)

    def get_events_by_project(self, project_id: str) -> list[Event]:
        return self.list_events(project_id=project_id)

    def get_causal_chain(self, event_id: str) -> list[Event]:
        """Traverse backwards via causation_id to assemble the causal lineage leading to this event."""
        with self._lock:
            chain = []
            curr_id = event_id
            visited = set()
            while curr_id and curr_id not in visited:
                visited.add(curr_id)
                evt = self.get_event(curr_id)
                if not evt:
                    break
                chain.append(evt)
                curr_id = evt.causation_id
            chain.reverse()  # Root cause first
            return chain

    def _row_to_event(self, row: sqlite3.Row) -> Event:
        return Event(
            event_id=row["event_id"],
            sequence_number=row["sequence_number"],
            event_type=EventType(row["event_type"]),
            timestamp=row["timestamp"],
            source=EventSource(row["source"]),
            correlation_id=row["correlation_id"],
            causation_id=row["causation_id"],
            project_id=row["project_id"],
            task_id=row["task_id"],
            worker_id=row["worker_id"],
            artifact_id=row["artifact_id"],
            schema_version=row["schema_version"],
            payload=json.loads(row["payload"]),
            metadata=json.loads(row["metadata"]),
        )

    # Memory Store Operations (Stage 3)
    def save_memory_document(self, doc: MemoryDocument) -> None:
        with self._lock:
            try:
                cursor = self._conn.cursor()
                cursor.execute("""
                    INSERT INTO memory_documents (
                        id, project_id, memory_type, title, relative_path, content, summary,
                        tags, references_json, related_task_id, related_worker_id, version,
                        checksum, metadata, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        relative_path = excluded.relative_path,
                        content = excluded.content,
                        summary = excluded.summary,
                        tags = excluded.tags,
                        references_json = excluded.references_json,
                        related_task_id = excluded.related_task_id,
                        related_worker_id = excluded.related_worker_id,
                        version = excluded.version,
                        checksum = excluded.checksum,
                        metadata = excluded.metadata,
                        updated_at = excluded.updated_at;
                """, (
                    doc.id,
                    doc.project_id,
                    doc.memory_type.value if isinstance(doc.memory_type, MemoryType) else doc.memory_type,
                    doc.title,
                    doc.relative_path,
                    doc.content,
                    doc.summary,
                    json.dumps(doc.tags),
                    json.dumps(doc.references),
                    doc.related_task_id,
                    doc.related_worker_id,
                    doc.version,
                    doc.checksum,
                    json.dumps(doc.metadata),
                    doc.created_at,
                    doc.updated_at or utc_now(),
                ))
            except Exception as e:
                raise PersistenceError("save_memory_document", str(e)) from e

    def get_memory_document(self, memory_id: str) -> Optional[MemoryDocument]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM memory_documents WHERE id = ?;", (memory_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_memory_doc(row)

    def get_memory_document_by_path(self, project_id: str, relative_path: str) -> Optional[MemoryDocument]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT * FROM memory_documents WHERE project_id = ? AND relative_path = ?;",
                (project_id, relative_path),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_memory_doc(row)

    def list_memory_documents(
        self,
        project_id: Optional[str] = None,
        memory_type: Optional[MemoryType] = None,
        tag: Optional[str] = None,
        related_task_id: Optional[str] = None,
    ) -> list[MemoryDocument]:
        with self._lock:
            cursor = self._conn.cursor()
            query = "SELECT * FROM memory_documents WHERE 1=1"
            params = []
            if project_id:
                query += " AND project_id = ?"
                params.append(project_id)
            if memory_type:
                query += " AND memory_type = ?"
                params.append(memory_type.value if isinstance(memory_type, MemoryType) else memory_type)
            if related_task_id:
                query += " AND related_task_id = ?"
                params.append(related_task_id)

            query += " ORDER BY updated_at DESC;"
            cursor.execute(query, params)
            rows = cursor.fetchall()
            docs = [self._row_to_memory_doc(row) for row in rows]
            if tag:
                docs = [d for d in docs if tag in d.tags]
            return docs

    def delete_memory_document(self, memory_id: str) -> bool:
        with self._lock:
            try:
                cursor = self._conn.cursor()
                cursor.execute("DELETE FROM memory_documents WHERE id = ?;", (memory_id,))
                return cursor.rowcount > 0
            except Exception as e:
                raise PersistenceError("delete_memory_document", str(e)) from e

    def _row_to_memory_doc(self, row: sqlite3.Row) -> MemoryDocument:
        return MemoryDocument(
            id=row["id"],
            project_id=row["project_id"],
            memory_type=MemoryType(row["memory_type"]),
            title=row["title"],
            relative_path=row["relative_path"],
            content=row["content"],
            summary=row["summary"],
            tags=json.loads(row["tags"]),
            references=json.loads(row["references_json"]),
            related_task_id=row["related_task_id"],
            related_worker_id=row["related_worker_id"],
            version=row["version"],
            checksum=row["checksum"],
            metadata=json.loads(row["metadata"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # Manager Plan & Decision Operations (Stage 10)
    def save_plan(self, plan: "Plan") -> None:
        from core.manager.model import Plan
        with self._lock:
            try:
                cursor = self._conn.cursor()
                cursor.execute("""
                    INSERT INTO plans (id, project_id, objective, tasks, milestones, dependencies, status, version, created_at, updated_at, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        objective = excluded.objective,
                        tasks = excluded.tasks,
                        milestones = excluded.milestones,
                        dependencies = excluded.dependencies,
                        status = excluded.status,
                        version = excluded.version,
                        updated_at = excluded.updated_at,
                        metadata = excluded.metadata;
                """, (
                    plan.id,
                    plan.project_id,
                    plan.objective,
                    json.dumps(plan.tasks),
                    json.dumps(plan.milestones),
                    json.dumps(plan.dependencies),
                    plan.status.value if hasattr(plan.status, "value") else str(plan.status),
                    plan.version,
                    plan.created_at,
                    plan.updated_at,
                    json.dumps(plan.metadata),
                ))
            except Exception as e:
                raise PersistenceError("save_plan", str(e)) from e

    def get_plan(self, plan_id: str) -> Optional["Plan"]:
        from core.manager.model import Plan
        from core.manager.types import PlanStatus
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM plans WHERE id = ?;", (plan_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return Plan(
                id=row["id"],
                project_id=row["project_id"],
                objective=row["objective"],
                tasks=json.loads(row["tasks"]),
                milestones=json.loads(row["milestones"]),
                dependencies=json.loads(row["dependencies"]),
                status=PlanStatus(row["status"]),
                version=row["version"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                metadata=json.loads(row["metadata"]),
            )

    def list_plans_for_project(self, project_id: str) -> list["Plan"]:
        from core.manager.model import Plan
        from core.manager.types import PlanStatus
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM plans WHERE project_id = ? ORDER BY version ASC;", (project_id,))
            rows = cursor.fetchall()
            return [
                Plan(
                    id=row["id"],
                    project_id=row["project_id"],
                    objective=row["objective"],
                    tasks=json.loads(row["tasks"]),
                    milestones=json.loads(row["milestones"]),
                    dependencies=json.loads(row["dependencies"]),
                    status=PlanStatus(row["status"]),
                    version=row["version"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    metadata=json.loads(row["metadata"]),
                )
                for row in rows
            ]

    def save_manager_decision(self, decision: "ManagerDecision") -> None:
        from core.manager.model import ManagerDecision
        with self._lock:
            try:
                cursor = self._conn.cursor()
                cursor.execute("""
                    INSERT INTO manager_decisions (decision_id, cycle_id, project_id, reasoning_summary, actions, assumptions, risks, confidence_level, plan_update, metadata, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(decision_id) DO UPDATE SET
                        reasoning_summary = excluded.reasoning_summary,
                        actions = excluded.actions,
                        assumptions = excluded.assumptions,
                        risks = excluded.risks,
                        confidence_level = excluded.confidence_level,
                        plan_update = excluded.plan_update,
                        metadata = excluded.metadata;
                """, (
                    decision.decision_id,
                    decision.cycle_id,
                    decision.project_id,
                    decision.reasoning_summary,
                    json.dumps([a.to_dict() if hasattr(a, "to_dict") else a for a in decision.actions]),
                    json.dumps(decision.assumptions),
                    json.dumps(decision.risks),
                    decision.confidence_level.value if hasattr(decision.confidence_level, "value") else str(decision.confidence_level),
                    json.dumps(decision.plan_update) if decision.plan_update else None,
                    json.dumps(decision.metadata),
                    decision.created_at,
                ))
            except Exception as e:
                raise PersistenceError("save_manager_decision", str(e)) from e

    def get_manager_decision(self, decision_id: str) -> Optional["ManagerDecision"]:
        from core.manager.model import ManagerAction, ManagerDecision
        from core.manager.types import ConfidenceLevel
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM manager_decisions WHERE decision_id = ?;", (decision_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return ManagerDecision(
                decision_id=row["decision_id"],
                cycle_id=row["cycle_id"],
                project_id=row["project_id"],
                reasoning_summary=row["reasoning_summary"],
                actions=[ManagerAction.from_dict(a) for a in json.loads(row["actions"])],
                assumptions=json.loads(row["assumptions"]),
                risks=json.loads(row["risks"]),
                confidence_level=ConfidenceLevel(row["confidence_level"]),
                plan_update=json.loads(row["plan_update"]) if row["plan_update"] else None,
                metadata=json.loads(row["metadata"]),
                created_at=row["created_at"],
            )

    def list_manager_decisions_for_project(self, project_id: str, limit: Optional[int] = None) -> list["ManagerDecision"]:
        from core.manager.model import ManagerAction, ManagerDecision
        from core.manager.types import ConfidenceLevel
        with self._lock:
            cursor = self._conn.cursor()
            query = "SELECT * FROM manager_decisions WHERE project_id = ? ORDER BY created_at ASC"
            params = [project_id]
            if limit:
                query += " LIMIT ?"
                params.append(limit)
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [
                ManagerDecision(
                    decision_id=row["decision_id"],
                    cycle_id=row["cycle_id"],
                    project_id=row["project_id"],
                    reasoning_summary=row["reasoning_summary"],
                    actions=[ManagerAction.from_dict(a) for a in json.loads(row["actions"])],
                    assumptions=json.loads(row["assumptions"]),
                    risks=json.loads(row["risks"]),
                    confidence_level=ConfidenceLevel(row["confidence_level"]),
                    plan_update=json.loads(row["plan_update"]) if row["plan_update"] else None,
                    metadata=json.loads(row["metadata"]),
                    created_at=row["created_at"],
                )
                for row in rows
            ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
