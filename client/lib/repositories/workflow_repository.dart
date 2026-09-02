import '../models/manager.dart';
import '../models/task.dart';
import '../models/workflow.dart';
import '../services/api_client.dart';

class WorkflowRepository {
  final AutonomOSApiClient _api;

  WorkflowRepository(this._api);

  Future<ManagerStatusModel> getManagerStatus(String projectId) =>
      _api.getManagerStatus(projectId);

  Future<PlanModel?> getActivePlan(String projectId) =>
      _api.getActivePlan(projectId);

  Future<Map<String, dynamic>> stepManager(String projectId, {String? feedback}) =>
      _api.stepManager(projectId, feedback: feedback);

  Future<Map<String, dynamic>> runOrchestration(String projectId, {int maxCycles = 15}) =>
      _api.runOrchestration(projectId, maxCycles: maxCycles);

  Future<List<WorkflowInfo>> listWorkflows(String projectId) =>
      _api.listWorkflows(projectId);

  Future<List<TaskItem>> listTasks(String projectId, {String? status}) =>
      _api.listTasks(projectId, status: status);

  Future<List<TaskItem>> getTasks(String projectId) => listTasks(projectId);

  Future<TaskItem> getTask(String taskId) => _api.getTask(taskId);
}
