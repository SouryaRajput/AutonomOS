import 'dart:async';
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

/// Abstract API Client interface defining the boundary between Flutter and AutonomOS.
abstract class AutonomOSApiClient {
  // Project operations
  Future<Project> createProject({required String name, required String rootPath, String description = ''});
  Future<Project> getProject(String projectId);
  Future<List<Project>> listProjects();
  Future<Project> updateProject(String projectId, {String? name, String? description});
  Future<Project> archiveProject(String projectId);

  // Task operations
  Future<TaskItem> createTask({required String projectId, required String title, String objective = '', int priority = 1, String risk = 'LOW'});
  Future<TaskItem> getTask(String taskId);
  Future<List<TaskItem>> listTasks(String projectId, {String? status});
  Future<List<EventModel>> getTaskTimeline(String taskId);

  // Worker operations
  Future<List<WorkerInfo>> listWorkers();
  Future<WorkerInfo> getWorker(String workerId);

  // Provider operations
  Future<Map<String, dynamic>> setProviderKey(String providerId, String key);

  // Conversation operations
  Future<ChatConversation> getOrCreateActiveConversation(String projectId, {String title = 'Workforce Chat'});
  Future<ChatConversation> createConversation(String projectId, {String title = 'New Conversation'});
  Future<ChatConversation> getConversation(String conversationId);
  Future<List<ChatConversation>> listConversations(String projectId);
  Future<ChatConversation> renameConversation(String conversationId, String newTitle);
  Future<void> deleteConversation(String conversationId);
  Future<List<ChatMessage>> postUserMessage(String conversationId, String content, {bool dispatchManager = true});

  // Workflow & Manager operations
  Future<ManagerStatusModel> getManagerStatus(String projectId);
  Future<PlanModel?> getActivePlan(String projectId);
  Future<Map<String, dynamic>> stepManager(String projectId, {String? feedback});
  Future<Map<String, dynamic>> runOrchestration(String projectId, {int maxCycles = 15});
  Future<List<WorkflowInfo>> listWorkflows(String projectId);

  // Approval & Autonomy operations
  Future<List<ApprovalItem>> listPendingApprovals(String projectId);
  Future<ApprovalItem> grantApproval(String approvalId, {String decidedBy = 'User'});
  Future<ApprovalItem> rejectApproval(String approvalId, {String reason = '', String decidedBy = 'User'});
  Future<List<UserInputItem>> listPendingUserInputs(String projectId);
  Future<UserInputItem> respondToUserInput(String inputId, String answer);
  Future<List<DecisionItem>> listPendingDecisions(String projectId);
  Future<DecisionItem> respondToDecision(String decisionId, String chosenOption, {String rationale = ''});
  Future<AutonomyPolicyModel> getPolicy(String projectId);
  Future<AutonomyPolicyModel> updatePolicy(String projectId, {String? autonomyLevel});
  Future<bool> emergencyStop(String projectId, {String reason = 'User initiated'});
  Future<bool> clearEmergencyStop(String projectId);

  // Activity & Events
  Future<List<ActivityItemModel>> getActivityFeed({int limit = 50});
  Future<List<ActivityItemModel>> getProjectActivity(String projectId, {int limit = 50});
  Stream<EventModel> subscribeEvents({int sinceSequence = 0});

  // Artifacts, Evidence & Verification
  Future<List<ArtifactModel>> listArtifacts(String projectId, {String? taskId});
  Future<ArtifactModel> getArtifact(String artifactId);
  Future<String?> getArtifactContent(String artifactId);
  Future<List<EvidenceModel>> listEvidence(String taskId);
  Future<VerificationReportModel?> getVerificationReport(String taskId);
}
