import json
from pathlib import Path
import sqlite3
import threading
from typing import Optional

from core.enums import ArtifactType, DependencyType, ProjectStatus, RiskLevel, TaskStatus, WorkerStatus
from core.errors import PersistenceError
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
            autocommit=False,
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

            self._conn.commit()

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
                self._conn.commit()
            except Exception as e:
                self._conn.rollback()
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
                self._conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                self._conn.rollback()
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
                self._conn.commit()
            except Exception as e:
                self._conn.rollback()
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
                self._conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                self._conn.rollback()
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
                self._conn.commit()
            except Exception as e:
                self._conn.rollback()
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
                self._conn.commit()
            except Exception as e:
                self._conn.rollback()
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
                self._conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                self._conn.rollback()
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
                self._conn.commit()
            except Exception as e:
                self._conn.rollback()
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
                self._conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                self._conn.rollback()
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
                self._conn.commit()
            except Exception as e:
                self._conn.rollback()
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

    def close(self) -> None:
        with self._lock:
            self._conn.close()
