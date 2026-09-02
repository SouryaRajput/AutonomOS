import 'dart:async';
import 'package:intl/intl.dart';
import '../models/project.dart';
import '../models/task.dart';
import '../models/worker.dart';
import '../models/conversation.dart';
import '../models/workflow.dart';
import '../models/approval.dart';
import '../models/event.dart';
import '../models/manager.dart';
import '../models/policy.dart';
import '../models/artifact.dart';
import '../models/evidence.dart';
import '../models/verification.dart';
import 'api_client.dart';

/// In-memory implementation of AutonomOSApiClient mirroring the deterministic backend runtime.
class MockAutonomOSApiClient implements AutonomOSApiClient {
  final List<Project> _projects = [];
  final List<TaskItem> _tasks = [];
  final List<WorkerInfo> _workers = [];
  final List<ChatConversation> _conversations = [];
  final List<WorkflowInfo> _workflows = [];
  final List<ApprovalItem> _approvals = [];
  final List<UserInputItem> _userInputs = [];
  final List<DecisionItem> _decisions = [];
  final List<ActivityItemModel> _activities = [];
  final List<ArtifactModel> _artifacts = [];
  final List<EvidenceModel> _evidenceList = [];
  final Map<String, VerificationReportModel> _verificationReports = {};
  final Map<String, AutonomyPolicyModel> _policies = {};
  final Map<String, ManagerStatusModel> _managerStatuses = {};
  final Map<String, PlanModel> _plans = {};
  final StreamController<EventModel> _eventStreamController = StreamController<EventModel>.broadcast();

  bool _isEmergencyStopped = false;

  MockAutonomOSApiClient() {
    _seedInitialData();
  }

  void _seedInitialData() {
    final now = DateFormat("yyyy-MM-ddTHH:mm:ss'Z'").format(DateTime.now().toUtc());

    // Seed default project
    final defaultProj = Project(
      id: 'proj-autonomos-demo',
      name: 'AutonomOS Core',
      description: 'Distributed workforce orchestrator and intelligent agents',
      rootPath: '/workspaces/AutonomOS',
      status: 'ACTIVE',
      taskCount: 4,
      workerCount: 4,
      createdAt: now,
      updatedAt: now,
    );
    _projects.add(defaultProj);

    // Seed workers
    _workers.addAll([
      const WorkerInfo(
        id: 'worker.manager.orchestrator',
        name: 'Manager',
        role: 'Orchestrator',
        description: 'Understands user goals, plans DAG workflows, and assigns specialist workers',
        status: 'IDLE',
        capabilities: ['REASONING', 'WORKFLOW_PLANNING', 'DECOMPOSITION'],
        permissions: ['READ_ALL', 'ASSIGN_TASKS'],
        tools: ['plan.create', 'plan.update', 'task.assign'],
      ),
      const WorkerInfo(
        id: 'worker.researcher',
        name: 'Researcher',
        role: 'Specialist',
        description: 'Explores codebases, documentation, dependencies, and gathers domain context',
        status: 'IDLE',
        capabilities: ['CODE_SEARCH', 'WEB_SEARCH', 'CONTEXT_SYNTHESIS'],
        permissions: ['READ_ONLY', 'NETWORK_SEARCH'],
        tools: ['search.code', 'web.fetch', 'memory.read'],
      ),
      const WorkerInfo(
        id: 'worker.programmer',
        name: 'Programmer',
        role: 'Specialist',
        description: 'Implements production code, refactors modules, and edits files cleanly',
        status: 'IDLE',
        capabilities: ['CODE_GENERATION', 'DIFF_APPLICATION', 'BUILD_VALIDATION'],
        permissions: ['WORKSPACE_WRITE', 'EXECUTE_LOCAL'],
        tools: ['file.write', 'git.diff', 'build.run'],
      ),
      const WorkerInfo(
        id: 'worker.tester',
        name: 'Tester',
        role: 'Specialist',
        description: 'Independently evaluates implementations, runs tests, and detects regressions',
        status: 'IDLE',
        capabilities: ['TEST_EXECUTION', 'DEFECT_INVESTIGATION', 'REGRESSION_DETECTION'],
        permissions: ['WORKSPACE_READ', 'EXECUTE_TESTS'],
        tools: ['test.run', 'coverage.check', 'defect.report'],
      ),
    ]);

    // Seed default conversation
    _conversations.add(ChatConversation(
      id: 'conv-init-1',
      projectId: defaultProj.id,
      title: 'Workforce Chat',
      messages: [
        ChatMessage(
          id: 'msg-seed-1',
          conversationId: 'conv-init-1',
          messageType: MessageType.managerMessage,
          content: 'Hello! I am the AutonomOS Manager. Describe what you want to build, and I will formulate a plan and coordinate the workforce.',
          sender: 'Manager',
          timestamp: now,
        ),
      ],
      createdAt: now,
      updatedAt: now,
      isActive: true,
    ));

    // Seed default tasks
    _tasks.addAll([
      TaskItem(
        id: 'task-101',
        projectId: defaultProj.id,
        title: 'Research OAuth 2.0 PKCE specification',
        objective: 'Analyze standard RFC 7636 requirements for authentication',
        status: 'COMPLETED',
        priority: 1,
        risk: 'LOW',
        assignedWorker: 'worker.researcher',
        assignedWorkerName: 'Researcher',
        createdAt: now,
        startedAt: now,
        completedAt: now,
      ),
      TaskItem(
        id: 'task-102',
        projectId: defaultProj.id,
        title: 'Implement Token Verifier middleware',
        objective: 'Write JWT verification and signature checks',
        status: 'COMPLETED',
        priority: 1,
        risk: 'MEDIUM',
        assignedWorker: 'worker.programmer',
        assignedWorkerName: 'Programmer',
        dependencies: ['task-101'],
        createdAt: now,
        startedAt: now,
        completedAt: now,
      ),
      TaskItem(
        id: 'task-103',
        projectId: defaultProj.id,
        title: 'Run Integration and Security Tests',
        objective: 'Verify token expiration, invalid signatures, and replay attack immunity',
        status: 'RUNNING',
        priority: 1,
        risk: 'LOW',
        assignedWorker: 'worker.tester',
        assignedWorkerName: 'Tester',
        dependencies: ['task-102'],
        createdAt: now,
        startedAt: now,
      ),
      TaskItem(
        id: 'task-104',
        projectId: defaultProj.id,
        title: 'Deterministic Verification Gate',
        objective: 'Authoritative system check on test suite exit codes and artifacts',
        status: 'PENDING',
        priority: 1,
        risk: 'HIGH',
        dependencies: ['task-103'],
        createdAt: now,
      ),
    ]);

    // Seed Manager Status & Plan
    _managerStatuses[defaultProj.id] = ManagerStatusModel(
      projectId: defaultProj.id,
      isActive: true,
      currentActivity: 'Orchestrating test verification cycle',
      totalCycles: 3,
      totalCost: 0.042,
      summaryText: 'Authentication module implementation verified. Tester worker active.',
    );

    _plans[defaultProj.id] = PlanModel(
      id: 'plan-auth-1',
      projectId: defaultProj.id,
      objective: 'Build resilient authentication & session management microservice',
      milestones: ['1. Research', '2. Implementation', '3. Testing', '4. Verification'],
      status: 'ACTIVE',
      version: 1,
      tasks: [
        {'id': 'task-101', 'title': 'Research OAuth 2.0 PKCE', 'status': 'COMPLETED'},
        {'id': 'task-102', 'title': 'Implement Token Verifier', 'status': 'COMPLETED'},
        {'id': 'task-103', 'title': 'Run Integration Tests', 'status': 'RUNNING'},
        {'id': 'task-104', 'title': 'Deterministic Verification Gate', 'status': 'PENDING'},
      ],
    );

    // Seed Workflow
    _workflows.add(WorkflowInfo(
      id: 'wf-auth-01',
      projectId: defaultProj.id,
      title: 'Building Authentication',
      status: 'RUNNING',
      taskIds: ['task-101', 'task-102', 'task-103', 'task-104'],
      currentStep: 2,
      iterations: 3,
      createdAt: now,
    ));

    // Seed Activity
    _activities.addAll([
      ActivityItemModel(
        icon: '🧪',
        title: 'Test Suite Started',
        subtitle: 'Tester running auth_token_test.py (28 cases)',
        level: 'INFO',
        timestamp: 'Just now',
        eventType: 'TEST_STARTED',
        projectId: defaultProj.id,
        workerId: 'worker.tester',
      ),
      ActivityItemModel(
        icon: '💻',
        title: 'Code Modified',
        subtitle: 'Programmer updated auth/jwt_verifier.py (+142 -12 lines)',
        level: 'INFO',
        timestamp: '5m ago',
        eventType: 'PROGRAMMER_CODE_MODIFIED',
        projectId: defaultProj.id,
        workerId: 'worker.programmer',
      ),
      ActivityItemModel(
        icon: '🔍',
        title: 'Research Completed',
        subtitle: 'Researcher synthesized OAuth 2.0 PKCE guidelines',
        level: 'SUCCESS',
        timestamp: '12m ago',
        eventType: 'RESEARCH_COMPLETED',
        projectId: defaultProj.id,
        workerId: 'worker.researcher',
      ),
      ActivityItemModel(
        icon: '🧠',
        title: 'Manager Plan Formulated',
        subtitle: 'Manager decomposed objective into 4 DAG tasks',
        level: 'INFO',
        timestamp: '15m ago',
        eventType: 'MANAGER_PLAN_CREATED',
        projectId: defaultProj.id,
        workerId: 'worker.manager.orchestrator',
      ),
    ]);

    // Seed Policy
    _policies[defaultProj.id] = AutonomyPolicyModel(
      id: 'pol-default',
      projectId: defaultProj.id,
      autonomyLevel: 'BALANCED',
      allowedTools: ['filesystem.read', 'search.code', 'test.run', 'memory.read'],
      deniedTools: ['shell.rm_rf', 'git.force_push'],
      maxCostLimit: 10.0,
      maxIterations: 15,
      version: 1,
    );

    // Seed Artifacts
    _artifacts.addAll([
      ArtifactModel(
        id: 'art-01',
        projectId: defaultProj.id,
        taskId: 'task-101',
        workerId: 'worker.researcher',
        kind: ArtifactKind.report,
        path: '.autonomos/memory/research/oauth2_pkce_spec.md',
        description: 'Comprehensive OAuth 2.0 PKCE implementation guide and architectural considerations',
        content: '''# OAuth 2.0 PKCE Architectural Research

## Overview
Proof Key for Code Exchange (RFC 7636) prevents authorization code interception attacks on public and SPA clients.

### Key Components
* **Code Verifier**: High-entropy cryptographic random string with minimum 43 characters.
* **Code Challenge**: `BASE64URL-ENCODE(SHA256(ASCII(code_verifier)))`
* **Transform**: `S256` mandatory for secure clients.

| Parameter | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `code_challenge` | string | Yes | The challenge string derived from verifier |
| `code_challenge_method` | string | Yes | Must be `S256` |

> Recommendation: Enforce `S256` transform and reject `plain` challenges in production.
''',
        createdAt: now,
      ),
      ArtifactModel(
        id: 'art-02',
        projectId: defaultProj.id,
        taskId: 'task-102',
        workerId: 'worker.programmer',
        kind: ArtifactKind.diff,
        path: 'diffs/task-102-jwt-verifier.diff',
        description: 'Programmer changes adding TokenVerifier middleware and header validation',
        content: '''--- a/src/auth/jwt_verifier.py
+++ b/src/auth/jwt_verifier.py
@@ -12,6 +12,18 @@ class JWTVerifier:
     def __init__(self, public_key: str, algorithm: str = "RS256"):
         self.public_key = public_key
         self.algorithm = algorithm
+        self.leeway_seconds = 10
 
+    def verify_token(self, token: str) -> dict:
+        if not token:
+            raise AuthenticationError("Missing token")
+        # Validate signature and claims
+        claims = jwt.decode(token, self.public_key, algorithms=[self.algorithm], leeway=self.leeway_seconds)
+        if "exp" not in claims:
+            raise AuthenticationError("Token missing expiration claim")
+        return claims
''',
        createdAt: now,
      ),
      ArtifactModel(
        id: 'art-03',
        projectId: defaultProj.id,
        taskId: 'task-102',
        workerId: 'worker.programmer',
        kind: ArtifactKind.sourceCode,
        path: 'src/auth/jwt_verifier.py',
        description: 'Production implementation of JWT token verifier',
        content: '''import jwt
import time
from typing import Optional

class AuthenticationError(Exception):
    pass

class JWTVerifier:
    """Verifies RS256 signed JSON Web Tokens."""

    def __init__(self, public_key: str, algorithm: str = "RS256"):
        self.public_key = public_key
        self.algorithm = algorithm
        self.leeway_seconds = 10

    def verify_token(self, token: str) -> dict:
        if not token:
            raise AuthenticationError("Missing token")
        claims = jwt.decode(token, self.public_key, algorithms=[self.algorithm], leeway=self.leeway_seconds)
        if "exp" not in claims:
            raise AuthenticationError("Token missing expiration claim")
        return claims
''',
        createdAt: now,
      ),
      ArtifactModel(
        id: 'art-04',
        projectId: defaultProj.id,
        taskId: 'task-103',
        workerId: 'worker.tester',
        kind: ArtifactKind.testReport,
        path: 'reports/qa_evaluation_report.md',
        description: 'Tester independent QA evaluation summary',
        content: '''# QA Evaluation Report: Authentication Module

## Summary
* **Status**: VERIFIED
* **Total Tests Executed**: 28
* **Passed**: 28
* **Failed**: 0
* **Regressions Detected**: 0

### Test Categories
* Unit Tests: 18 passed
* Security Invariants: 6 passed (Replay immunity, expired token rejection)
* Persistence Checks: 4 passed
''',
        createdAt: now,
      ),
    ]);

    // Seed Evidence
    _evidenceList.addAll([
      EvidenceModel(
        id: 'ev-01',
        taskId: 'task-103',
        evidenceType: 'COMMAND_OUTPUT',
        data: 'pytest tests/test_auth.py --verbose\n28 passed in 0.42s [100%]\nExit code: 0',
        sourceWorkerId: 'worker.tester',
        createdAt: now,
      ),
      EvidenceModel(
        id: 'ev-02',
        taskId: 'task-102',
        evidenceType: 'FILE_CHECKSUM',
        data: 'sha256: 7d4a2b9f3e1a0c8b6d4e2f0a8b6c4d2e0f8a6b4c2d0e8f6a4b2c0d8e6f4a2b0c  src/auth/jwt_verifier.py',
        sourceWorkerId: 'worker.programmer',
        createdAt: now,
      ),
    ]);

    // Seed Verification Report
    _verificationReports['task-103'] = VerificationReportModel(
      id: 'ver-auth-103',
      projectId: defaultProj.id,
      taskId: 'task-103',
      state: VerificationState.verified,
      totalChecks: 4,
      passedChecks: 4,
      failedChecks: 0,
      summary: 'All 4 deterministic verification checks passed: file exists, syntax compiles, 28/28 tests passed, and checksum matches.',
      timestamp: now,
      checks: [
        const VerificationCheckModel(
          id: 'chk-01',
          checkType: 'FILE_EXISTS',
          description: 'Verify src/auth/jwt_verifier.py exists on disk',
          status: 'PASSED',
          durationMs: 1.2,
        ),
        const VerificationCheckModel(
          id: 'chk-02',
          checkType: 'COMPILE',
          description: 'Validate Python AST syntax compilation without syntax errors',
          status: 'PASSED',
          durationMs: 14.8,
        ),
        const VerificationCheckModel(
          id: 'chk-03',
          checkType: 'TEST_SUITE',
          description: 'Execute test suite test_auth.py with zero exit code',
          status: 'PASSED',
          durationMs: 420.5,
        ),
        const VerificationCheckModel(
          id: 'chk-04',
          checkType: 'ARTIFACT_HASH',
          description: 'Verify cryptographic SHA256 integrity of modified modules',
          status: 'PASSED',
          durationMs: 2.1,
        ),
      ],
    );
  }

  // --- Project Operations ---
  @override
  Future<Project> createProject({required String name, required String rootPath, String description = ''}) async {
    final now = DateFormat("yyyy-MM-ddTHH:mm:ss'Z'").format(DateTime.now().toUtc());
    final p = Project(
      id: 'proj-${DateTime.now().millisecondsSinceEpoch}',
      name: name,
      description: description,
      rootPath: rootPath,
      createdAt: now,
      updatedAt: now,
    );
    _projects.add(p);
    return p;
  }

  @override
  Future<Project> getProject(String projectId) async {
    return _projects.firstWhere((p) => p.id == projectId, orElse: () => _projects.first);
  }

  @override
  Future<List<Project>> listProjects() async => List.from(_projects);

  @override
  Future<Project> updateProject(String projectId, {String? name, String? description}) async {
    final idx = _projects.indexWhere((p) => p.id == projectId);
    if (idx != -1) {
      final old = _projects[idx];
      final updated = Project(
        id: old.id,
        name: name ?? old.name,
        description: description ?? old.description,
        rootPath: old.rootPath,
        status: old.status,
        taskCount: old.taskCount,
        workerCount: old.workerCount,
        createdAt: old.createdAt,
        updatedAt: DateFormat("yyyy-MM-ddTHH:mm:ss'Z'").format(DateTime.now().toUtc()),
      );
      _projects[idx] = updated;
      return updated;
    }
    throw Exception('Project not found');
  }

  @override
  Future<Project> archiveProject(String projectId) async {
    final idx = _projects.indexWhere((p) => p.id == projectId);
    if (idx != -1) {
      final old = _projects[idx];
      final archived = Project(
        id: old.id,
        name: old.name,
        description: old.description,
        rootPath: old.rootPath,
        status: 'ARCHIVED',
        taskCount: old.taskCount,
        workerCount: old.workerCount,
        createdAt: old.createdAt,
        updatedAt: DateFormat("yyyy-MM-ddTHH:mm:ss'Z'").format(DateTime.now().toUtc()),
      );
      _projects[idx] = archived;
      return archived;
    }
    throw Exception('Project not found');
  }

  // --- Task Operations ---
  @override
  Future<TaskItem> createTask({required String projectId, required String title, String objective = '', int priority = 1, String risk = 'LOW'}) async {
    final now = DateFormat("yyyy-MM-ddTHH:mm:ss'Z'").format(DateTime.now().toUtc());
    final t = TaskItem(
      id: 'task-${DateTime.now().millisecondsSinceEpoch}',
      projectId: projectId,
      title: title,
      objective: objective,
      priority: priority,
      risk: risk,
      status: 'READY',
      createdAt: now,
    );
    _tasks.add(t);
    return t;
  }

  @override
  Future<TaskItem> getTask(String taskId) async {
    return _tasks.firstWhere((t) => t.id == taskId, orElse: () => _tasks.first);
  }

  @override
  Future<List<TaskItem>> listTasks(String projectId, {String? status}) async {
    return _tasks.where((t) => t.projectId == projectId && (status == null || t.status == status)).toList();
  }

  @override
  Future<List<EventModel>> getTaskTimeline(String taskId) async => [];

  // --- Worker Operations ---
  @override
  Future<List<WorkerInfo>> listWorkers() async => List.from(_workers);

  @override
  Future<WorkerInfo> getWorker(String workerId) async => _workers.firstWhere((w) => w.id == workerId);

  // --- Provider Operations ---
  @override
  Future<Map<String, dynamic>> setProviderKey(String providerId, String key) async =>
      {'provider_id': providerId, 'configured': true};

  // --- Conversation Operations ---
  @override
  Future<ChatConversation> getOrCreateActiveConversation(String projectId, {String title = 'Workforce Chat'}) async {
    final existing = _conversations.where((c) => c.projectId == projectId && c.isActive).toList();
    if (existing.isNotEmpty) return existing.first;
    return createConversation(projectId, title: title);
  }

  @override
  Future<ChatConversation> createConversation(String projectId, {String title = 'New Conversation'}) async {
    final now = DateFormat("yyyy-MM-ddTHH:mm:ss'Z'").format(DateTime.now().toUtc());
    final c = ChatConversation(
      id: 'conv-${DateTime.now().millisecondsSinceEpoch}',
      projectId: projectId,
      title: title,
      messages: [],
      createdAt: now,
      updatedAt: now,
      isActive: true,
    );
    _conversations.add(c);
    return c;
  }

  @override
  Future<ChatConversation> getConversation(String conversationId) async {
    return _conversations.firstWhere((c) => c.id == conversationId);
  }

  @override
  Future<List<ChatConversation>> listConversations(String projectId) async {
    return _conversations.where((c) => c.projectId == projectId).toList();
  }

  @override
  Future<ChatConversation> renameConversation(String conversationId, String newTitle) async {
    final index = _conversations.indexWhere((c) => c.id == conversationId);
    if (index != -1) {
      final now = DateFormat("yyyy-MM-ddTHH:mm:ss'Z'").format(DateTime.now().toUtc());
      final existing = _conversations[index];
      final updated = ChatConversation(
        id: existing.id,
        projectId: existing.projectId,
        title: newTitle.trim(),
        messages: existing.messages,
        createdAt: existing.createdAt,
        updatedAt: now,
        isActive: existing.isActive,
      );
      _conversations[index] = updated;
      return updated;
    }
    throw Exception('Conversation $conversationId not found');
  }

  @override
  Future<void> deleteConversation(String conversationId) async {
    _conversations.removeWhere((c) => c.id == conversationId);
  }

  @override
  Future<List<ChatMessage>> postUserMessage(String conversationId, String content, {bool dispatchManager = true}) async {
    final now = DateFormat("yyyy-MM-ddTHH:mm:ss'Z'").format(DateTime.now().toUtc());
    final convIdx = _conversations.indexWhere((c) => c.id == conversationId);

    final userMsg = ChatMessage(
      id: 'msg-${DateTime.now().millisecondsSinceEpoch}',
      conversationId: conversationId,
      messageType: MessageType.userMessage,
      content: content,
      sender: 'user',
      timestamp: now,
    );

    final newMessages = <ChatMessage>[userMsg];

    if (dispatchManager) {
      final mgrMsg = ChatMessage(
        id: 'msg-${DateTime.now().millisecondsSinceEpoch + 1}',
        conversationId: conversationId,
        messageType: MessageType.managerMessage,
        content: 'I have analyzed your objective: "$content". I am formulating a task decomposition and dispatching the Researcher and Programmer workers.',
        sender: 'Manager',
        timestamp: now,
      );
      newMessages.add(mgrMsg);

      final wfMsg = ChatMessage(
        id: 'msg-${DateTime.now().millisecondsSinceEpoch + 2}',
        conversationId: conversationId,
        messageType: MessageType.workflowUpdate,
        content: 'Active Workflow Updated: Step 1 Research -> Step 2 Implementation -> Step 3 Testing',
        sender: 'WorkflowEngine',
        timestamp: now,
      );
      newMessages.add(wfMsg);
    }

    if (convIdx != -1) {
      final old = _conversations[convIdx];
      final updatedList = List<ChatMessage>.from(old.messages)..addAll(newMessages);
      _conversations[convIdx] = ChatConversation(
        id: old.id,
        projectId: old.projectId,
        title: old.title,
        messages: updatedList,
        createdAt: old.createdAt,
        updatedAt: now,
        isActive: old.isActive,
      );
    }

    return newMessages;
  }

  // --- Workflow & Manager Operations ---
  @override
  Future<ManagerStatusModel> getManagerStatus(String projectId) async {
    return _managerStatuses[projectId] ?? ManagerStatusModel(projectId: projectId, isActive: true);
  }

  @override
  Future<PlanModel?> getActivePlan(String projectId) async {
    return _plans[projectId];
  }

  @override
  Future<Map<String, dynamic>> stepManager(String projectId, {String? feedback}) async {
    return {'progress_detected': true, 'status': 'Cycle completed'};
  }

  @override
  Future<Map<String, dynamic>> runOrchestration(String projectId, {int maxCycles = 15}) async {
    return {'completed': true, 'cycles': 3};
  }

  @override
  Future<List<WorkflowInfo>> listWorkflows(String projectId) async =>
      _workflows.where((w) => w.projectId == projectId).toList();

  // --- Approvals & Autonomy ---
  @override
  Future<List<ApprovalItem>> listPendingApprovals(String projectId) async =>
      _approvals.where((a) => a.projectId == projectId && a.status == 'PENDING').toList();

  @override
  Future<ApprovalItem> grantApproval(String approvalId, {String decidedBy = 'User'}) async {
    final idx = _approvals.indexWhere((a) => a.id == approvalId);
    if (idx != -1) {
      final old = _approvals[idx];
      final updated = ApprovalItem(
        id: old.id,
        projectId: old.projectId,
        taskId: old.taskId,
        workerId: old.workerId,
        action: old.action,
        riskLevel: old.riskLevel,
        status: 'APPROVED',
        reason: old.reason,
        requestedScope: old.requestedScope,
        createdAt: old.createdAt,
        decidedAt: DateFormat("yyyy-MM-ddTHH:mm:ss'Z'").format(DateTime.now().toUtc()),
        decidedBy: decidedBy,
      );
      _approvals[idx] = updated;
      return updated;
    }
    throw Exception('Approval not found');
  }

  @override
  Future<ApprovalItem> rejectApproval(String approvalId, {String reason = '', String decidedBy = 'User'}) async {
    final idx = _approvals.indexWhere((a) => a.id == approvalId);
    if (idx != -1) {
      final old = _approvals[idx];
      final updated = ApprovalItem(
        id: old.id,
        projectId: old.projectId,
        taskId: old.taskId,
        workerId: old.workerId,
        action: old.action,
        riskLevel: old.riskLevel,
        status: 'REJECTED',
        reason: old.reason,
        requestedScope: old.requestedScope,
        createdAt: old.createdAt,
        decidedAt: DateFormat("yyyy-MM-ddTHH:mm:ss'Z'").format(DateTime.now().toUtc()),
        decidedBy: decidedBy,
        rejectionReason: reason,
      );
      _approvals[idx] = updated;
      return updated;
    }
    throw Exception('Approval not found');
  }

  @override
  Future<List<UserInputItem>> listPendingUserInputs(String projectId) async =>
      _userInputs.where((u) => u.projectId == projectId && u.status == 'PENDING').toList();

  @override
  Future<UserInputItem> respondToUserInput(String inputId, String answer) async {
    final idx = _userInputs.indexWhere((u) => u.id == inputId);
    if (idx != -1) {
      final old = _userInputs[idx];
      final updated = UserInputItem(
        id: old.id,
        projectId: old.projectId,
        taskId: old.taskId,
        question: old.question,
        context: old.context,
        answer: answer,
        status: 'ANSWERED',
        createdAt: old.createdAt,
      );
      _userInputs[idx] = updated;
      return updated;
    }
    throw Exception('User input request not found');
  }

  @override
  Future<List<DecisionItem>> listPendingDecisions(String projectId) async =>
      _decisions.where((d) => d.projectId == projectId && d.status == 'PENDING').toList();

  @override
  Future<DecisionItem> respondToDecision(String decisionId, String chosenOption, {String rationale = ''}) async {
    final idx = _decisions.indexWhere((d) => d.id == decisionId);
    if (idx != -1) {
      final old = _decisions[idx];
      final updated = DecisionItem(
        id: old.id,
        projectId: old.projectId,
        taskId: old.taskId,
        title: old.title,
        options: old.options,
        chosenOption: chosenOption,
        rationale: rationale,
        status: 'DECIDED',
        createdAt: old.createdAt,
      );
      _decisions[idx] = updated;
      return updated;
    }
    throw Exception('Decision request not found');
  }

  @override
  Future<AutonomyPolicyModel> getPolicy(String projectId) async =>
      _policies[projectId] ?? AutonomyPolicyModel(id: 'pol-$projectId', projectId: projectId);

  @override
  Future<AutonomyPolicyModel> updatePolicy(String projectId, {String? autonomyLevel}) async {
    final old = await getPolicy(projectId);
    final updated = AutonomyPolicyModel(
      id: old.id,
      projectId: projectId,
      autonomyLevel: autonomyLevel ?? old.autonomyLevel,
      allowedTools: old.allowedTools,
      deniedTools: old.deniedTools,
      maxCostLimit: old.maxCostLimit,
      maxIterations: old.maxIterations,
      version: old.version + 1,
    );
    _policies[projectId] = updated;
    return updated;
  }

  @override
  Future<bool> emergencyStop(String projectId, {String reason = 'User initiated'}) async {
    _isEmergencyStopped = true;
    return true;
  }

  @override
  Future<bool> clearEmergencyStop(String projectId) async {
    _isEmergencyStopped = false;
    return true;
  }

  // --- Activity & Events ---
  @override
  Future<List<ActivityItemModel>> getActivityFeed({int limit = 50}) async {
    return _activities.take(limit).toList();
  }

  @override
  Future<List<ActivityItemModel>> getProjectActivity(String projectId, {int limit = 50}) async {
    return _activities.where((a) => a.projectId == projectId).take(limit).toList();
  }

  @override
  Stream<EventModel> subscribeEvents({int sinceSequence = 0}) {
    return _eventStreamController.stream;
  }

  // --- Artifacts, Evidence & Verification ---
  @override
  Future<List<ArtifactModel>> listArtifacts(String projectId, {String? taskId}) async {
    return _artifacts.where((a) => a.projectId == projectId && (taskId == null || a.taskId == taskId)).toList();
  }

  @override
  Future<ArtifactModel> getArtifact(String artifactId) async {
    return _artifacts.firstWhere((a) => a.id == artifactId, orElse: () => _artifacts.first);
  }

  @override
  Future<String?> getArtifactContent(String artifactId) async {
    final art = _artifacts.firstWhere((a) => a.id == artifactId, orElse: () => _artifacts.first);
    return art.content;
  }

  @override
  Future<List<EvidenceModel>> listEvidence(String taskId) async {
    return _evidenceList.where((e) => e.taskId == taskId).toList();
  }

  @override
  Future<VerificationReportModel?> getVerificationReport(String taskId) async {
    return _verificationReports[taskId];
  }
}
