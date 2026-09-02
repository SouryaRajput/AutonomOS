import 'dart:async';
import 'dart:convert';
import 'dart:io';

import '../models/approval.dart';
import '../models/conversation.dart';
import '../models/event.dart';
import '../models/manager.dart';
import '../models/policy.dart';
import '../models/project.dart';
import '../models/task.dart';
import '../models/worker.dart';
import '../models/workflow.dart';
import '../models/artifact.dart';
import '../models/evidence.dart';
import '../models/verification.dart';
import 'api_client.dart';

/// Real HTTP and Server-Sent Events (SSE) client connecting Flutter to AutonomOS server.
class HttpAutonomOSApiClient implements AutonomOSApiClient {
  final String baseUrl;
  final HttpClient _httpClient = HttpClient();

  HttpAutonomOSApiClient({this.baseUrl = 'http://127.0.0.1:8000'});

  Future<dynamic> _get(String path, [Map<String, String>? queryParams]) async {
    final uri = Uri.parse('$baseUrl$path').replace(queryParameters: queryParams);
    final request = await _httpClient.getUrl(uri);
    request.headers.set(HttpHeaders.contentTypeHeader, 'application/json');
    final response = await request.close();
    final responseBody = await response.transform(utf8.decoder).join();

    if (response.statusCode >= 400) {
      throw Exception('HTTP ${response.statusCode}: $responseBody');
    }
    return responseBody.isNotEmpty ? json.decode(responseBody) : null;
  }

  Future<dynamic> _post(String path, [Map<String, dynamic>? body]) async {
    final uri = Uri.parse('$baseUrl$path');
    final request = await _httpClient.postUrl(uri);
    request.headers.set(HttpHeaders.contentTypeHeader, 'application/json');
    if (body != null) {
      request.write(json.encode(body));
    }
    final response = await request.close();
    final responseBody = await response.transform(utf8.decoder).join();

    if (response.statusCode >= 400) {
      throw Exception('HTTP ${response.statusCode}: $responseBody');
    }
    return responseBody.isNotEmpty ? json.decode(responseBody) : null;
  }

  // --- Project Operations ---
  @override
  Future<Project> createProject({required String name, required String rootPath, String description = ''}) async {
    final data = await _post('/api/projects', {
      'name': name,
      'root_path': rootPath,
      'description': description,
    });
    return Project.fromJson(data);
  }

  @override
  Future<Project> getProject(String projectId) async {
    final data = await _get('/api/projects/$projectId');
    return Project.fromJson(data);
  }

  @override
  Future<List<Project>> listProjects() async {
    final data = await _get('/api/projects') as List;
    return data.map((item) => Project.fromJson(item)).toList();
  }

  @override
  Future<Project> updateProject(String projectId, {String? name, String? description}) async {
    final data = await _post('/api/projects/$projectId/update', {
      if (name != null) 'name': name,
      if (description != null) 'description': description,
    });
    return Project.fromJson(data);
  }

  @override
  Future<Project> archiveProject(String projectId) async {
    final data = await _post('/api/projects/$projectId/archive');
    return Project.fromJson(data);
  }

  // --- Task Operations ---
  @override
  Future<TaskItem> createTask({required String projectId, required String title, String objective = '', int priority = 1, String risk = 'LOW'}) async {
    final data = await _post('/api/tasks', {
      'project_id': projectId,
      'title': title,
      'objective': objective,
      'priority': priority,
      'risk': risk,
    });
    return TaskItem.fromJson(data);
  }

  @override
  Future<TaskItem> getTask(String taskId) async {
    final data = await _get('/api/tasks/$taskId');
    return TaskItem.fromJson(data);
  }

  @override
  Future<List<TaskItem>> listTasks(String projectId, {String? status}) async {
    final query = {'project_id': projectId, if (status != null) 'status': status};
    final data = await _get('/api/tasks', query) as List;
    return data.map((item) => TaskItem.fromJson(item)).toList();
  }

  @override
  Future<List<EventModel>> getTaskTimeline(String taskId) async {
    final data = await _get('/api/tasks/$taskId/timeline') as List;
    return data.map((item) => EventModel.fromJson(item)).toList();
  }

  // --- Worker Operations ---
  @override
  Future<List<WorkerInfo>> listWorkers() async {
    final data = await _get('/api/workers') as List;
    return data.map((item) => WorkerInfo.fromJson(item)).toList();
  }

  @override
  Future<WorkerInfo> getWorker(String workerId) async {
    final data = await _get('/api/workers/$workerId');
    return WorkerInfo.fromJson(data);
  }

  // --- Provider Operations ---
  @override
  Future<Map<String, dynamic>> setProviderKey(String providerId, String key) async {
    final data = await _post('/api/providers/$providerId/key', {'key': key});
    return (data as Map<String, dynamic>?) ?? {'provider_id': providerId, 'configured': true};
  }

  // --- Conversation Operations ---
  @override
  Future<ChatConversation> getOrCreateActiveConversation(String projectId, {String title = 'Workforce Chat'}) async {
    final data = await _get('/api/conversations/active', {'project_id': projectId});
    return ChatConversation.fromJson(data);
  }

  @override
  Future<ChatConversation> createConversation(String projectId, {String title = 'New Conversation'}) async {
    final data = await _post('/api/conversations', {'project_id': projectId, 'title': title});
    return ChatConversation.fromJson(data);
  }

  @override
  Future<ChatConversation> getConversation(String conversationId) async {
    final data = await _get('/api/conversations/$conversationId');
    return ChatConversation.fromJson(data);
  }

  @override
  Future<List<ChatConversation>> listConversations(String projectId) async {
    final data = await _get('/api/conversations', {'project_id': projectId}) as List;
    return data.map((item) => ChatConversation.fromJson(item)).toList();
  }

  @override
  Future<ChatConversation> renameConversation(String conversationId, String newTitle) async {
    final data = await _post('/api/conversations/$conversationId/rename', {'title': newTitle});
    return ChatConversation.fromJson(data);
  }

  @override
  Future<void> deleteConversation(String conversationId) async {
    await _post('/api/conversations/$conversationId/delete', {});
  }

  @override
  Future<List<ChatMessage>> postUserMessage(String conversationId, String content, {bool dispatchManager = true}) async {
    final data = await _post('/api/conversations/$conversationId/messages', {
      'content': content,
      'dispatch_manager': dispatchManager,
    }) as List;
    return data.map((item) => ChatMessage.fromJson(item)).toList();
  }

  // --- Workflow & Manager Operations ---
  @override
  Future<ManagerStatusModel> getManagerStatus(String projectId) async {
    final data = await _get('/api/manager/status', {'project_id': projectId});
    return ManagerStatusModel.fromJson(data);
  }

  @override
  Future<PlanModel?> getActivePlan(String projectId) async {
    final data = await _get('/api/manager/plan', {'project_id': projectId});
    return data != null ? PlanModel.fromJson(data) : null;
  }

  @override
  Future<Map<String, dynamic>> stepManager(String projectId, {String? feedback}) async {
    final data = await _post('/api/manager/step', {
      'project_id': projectId,
      if (feedback != null) 'feedback': feedback,
    });
    return Map<String, dynamic>.from(data);
  }

  @override
  Future<Map<String, dynamic>> runOrchestration(String projectId, {int maxCycles = 15}) async {
    final data = await _post('/api/manager/orchestrate', {
      'project_id': projectId,
      'max_cycles': maxCycles,
    });
    return Map<String, dynamic>.from(data);
  }

  @override
  Future<List<WorkflowInfo>> listWorkflows(String projectId) async {
    final data = await _get('/api/workflows', {'project_id': projectId}) as List;
    return data.map((item) => WorkflowInfo.fromJson(item)).toList();
  }

  // --- Approvals & Autonomy Operations ---
  @override
  Future<List<ApprovalItem>> listPendingApprovals(String projectId) async {
    final data = await _get('/api/approvals', {'project_id': projectId}) as List;
    return data.map((item) => ApprovalItem.fromJson(item)).toList();
  }

  @override
  Future<ApprovalItem> grantApproval(String approvalId, {String decidedBy = 'User'}) async {
    final data = await _post('/api/approvals/$approvalId/grant', {'decided_by': decidedBy});
    return ApprovalItem.fromJson(data);
  }

  @override
  Future<ApprovalItem> rejectApproval(String approvalId, {String reason = '', String decidedBy = 'User'}) async {
    final data = await _post('/api/approvals/$approvalId/reject', {'reason': reason, 'decided_by': decidedBy});
    return ApprovalItem.fromJson(data);
  }

  @override
  Future<List<UserInputItem>> listPendingUserInputs(String projectId) async {
    final data = await _get('/api/user_inputs', {'project_id': projectId}) as List;
    return data.map((item) => UserInputItem.fromJson(item)).toList();
  }

  @override
  Future<UserInputItem> respondToUserInput(String inputId, String answer) async {
    final data = await _post('/api/user_inputs/$inputId/respond', {'answer': answer});
    return UserInputItem.fromJson(data);
  }

  @override
  Future<List<DecisionItem>> listPendingDecisions(String projectId) async {
    final data = await _get('/api/decisions', {'project_id': projectId}) as List;
    return data.map((item) => DecisionItem.fromJson(item)).toList();
  }

  @override
  Future<DecisionItem> respondToDecision(String decisionId, String chosenOption, {String rationale = ''}) async {
    final data = await _post('/api/decisions/$decisionId/respond', {
      'chosen_option': chosenOption,
      'rationale': rationale,
    });
    return DecisionItem.fromJson(data);
  }

  @override
  Future<AutonomyPolicyModel> getPolicy(String projectId) async {
    final data = await _get('/api/policies/$projectId');
    return AutonomyPolicyModel.fromJson(data);
  }

  @override
  Future<AutonomyPolicyModel> updatePolicy(String projectId, {String? autonomyLevel}) async {
    final data = await _post('/api/policies/$projectId/update', {
      if (autonomyLevel != null) 'autonomy_level': autonomyLevel,
    });
    return AutonomyPolicyModel.fromJson(data);
  }

  @override
  Future<bool> emergencyStop(String projectId, {String reason = 'User initiated'}) async {
    final data = await _post('/api/policies/$projectId/emergency_stop', {'reason': reason});
    return data['stopped'] == true;
  }

  @override
  Future<bool> clearEmergencyStop(String projectId) async {
    final data = await _post('/api/policies/$projectId/clear_emergency_stop');
    return data['stopped'] == false;
  }

  // --- Activity & Events Operations ---
  @override
  Future<List<ActivityItemModel>> getActivityFeed({int limit = 50}) async {
    final data = await _get('/api/activity', {'limit': limit.toString()}) as List;
    return data.map((item) => ActivityItemModel.fromJson(item)).toList();
  }

  @override
  Future<List<ActivityItemModel>> getProjectActivity(String projectId, {int limit = 50}) async {
    final data = await _get('/api/activity', {'project_id': projectId, 'limit': limit.toString()}) as List;
    return data.map((item) => ActivityItemModel.fromJson(item)).toList();
  }

  @override
  Stream<EventModel> subscribeEvents({int sinceSequence = 0}) {
    final controller = StreamController<EventModel>.broadcast();
    final uri = Uri.parse('$baseUrl/api/events/stream');

    _httpClient.getUrl(uri).then((request) {
      request.headers.set('Accept', 'text/event-stream');
      return request.close();
    }).then((response) {
      response.transform(utf8.decoder).transform(const LineSplitter()).listen((line) {
        if (line.startsWith('data: ')) {
          final rawData = line.substring(6).trim();
          try {
            final jsonMap = json.decode(rawData);
            if (jsonMap is Map<String, dynamic> && jsonMap.containsKey('event_id')) {
              controller.add(EventModel.fromJson(jsonMap));
            }
          } catch (_) {}
        }
      }, onError: (err) {
        controller.addError(err);
      }, onDone: () {
        controller.close();
      });
    }).catchError((err) {
      controller.addError(err);
    });

    return controller.stream;
  }

  // --- Artifacts, Evidence & Verification Operations ---
  @override
  Future<List<ArtifactModel>> listArtifacts(String projectId, {String? taskId}) async {
    final query = {'project_id': projectId, if (taskId != null) 'task_id': taskId};
    final data = await _get('/api/artifacts', query) as List;
    return data.map((item) => ArtifactModel.fromJson(item)).toList();
  }

  @override
  Future<ArtifactModel> getArtifact(String artifactId) async {
    final data = await _get('/api/artifacts/$artifactId');
    return ArtifactModel.fromJson(data);
  }

  @override
  Future<String?> getArtifactContent(String artifactId) async {
    final data = await _get('/api/artifacts/$artifactId/content');
    return data['content'];
  }

  @override
  Future<List<EvidenceModel>> listEvidence(String taskId) async {
    final data = await _get('/api/evidence', {'task_id': taskId}) as List;
    return data.map((item) => EvidenceModel.fromJson(item)).toList();
  }

  @override
  Future<VerificationReportModel?> getVerificationReport(String taskId) async {
    final data = await _get('/api/verification', {'task_id': taskId});
    return data != null ? VerificationReportModel.fromJson(data) : null;
  }
}
