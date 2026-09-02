import 'package:flutter/material.dart';
import '../models/manager.dart';
import '../models/task.dart';
import '../models/workflow.dart';
import '../repositories/workflow_repository.dart';

class WorkflowController extends ChangeNotifier {
  final WorkflowRepository repository;
  final String projectId;

  ManagerStatusModel? _managerStatus;
  ManagerStatusModel? get managerStatus => _managerStatus;

  PlanModel? _activePlan;
  PlanModel? get activePlan => _activePlan;

  List<WorkflowInfo> _workflows = [];
  List<WorkflowInfo> get workflows => _workflows;

  List<TaskItem> _tasks = [];
  List<TaskItem> get tasks => _tasks;

  TaskItem? _selectedTask;
  TaskItem? get selectedTask => _selectedTask;

  bool _isLoading = false;
  bool get isLoading => _isLoading;

  String? _errorMessage;
  String? get errorMessage => _errorMessage;

  WorkflowController({required this.repository, required this.projectId}) {
    loadWorkflowData();
  }

  Future<void> loadWorkflowData() async {
    _isLoading = true;
    notifyListeners();
    try {
      final results = await Future.wait([
        repository.getManagerStatus(projectId),
        repository.getActivePlan(projectId),
        repository.listWorkflows(projectId),
        repository.listTasks(projectId),
      ]);

      _managerStatus = results[0] as ManagerStatusModel;
      _activePlan = results[1] as PlanModel?;
      _workflows = results[2] as List<WorkflowInfo>;
      _tasks = results[3] as List<TaskItem>;
      _errorMessage = null;
    } catch (e) {
      _errorMessage = 'Failed to load workflow state: $e';
    } finally {
      _isLoading = false;
      notifyListeners();
    }
  }

  void selectTask(TaskItem? task) {
    _selectedTask = task;
    notifyListeners();
  }

  Future<void> stepManager() async {
    try {
      await repository.stepManager(projectId);
      await loadWorkflowData();
    } catch (e) {
      _errorMessage = 'Manager step failed: $e';
      notifyListeners();
    }
  }
}
