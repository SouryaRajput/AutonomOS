class ManagerStatusModel {
  final String projectId;
  final bool isActive;
  final String currentActivity;
  final String? activePlanId;
  final bool pendingUserInput;
  final bool stagnationDetected;
  final int totalCycles;
  final double totalCost;
  final String summaryText;

  const ManagerStatusModel({
    required this.projectId,
    this.isActive = false,
    this.currentActivity = 'Idle',
    this.activePlanId,
    this.pendingUserInput = false,
    this.stagnationDetected = false,
    this.totalCycles = 0,
    this.totalCost = 0.0,
    this.summaryText = '',
  });

  factory ManagerStatusModel.fromJson(Map<String, dynamic> json) {
    return ManagerStatusModel(
      projectId: json['project_id'] as String? ?? '',
      isActive: json['is_active'] as bool? ?? false,
      currentActivity: json['current_activity'] as String? ?? 'Idle',
      activePlanId: json['active_plan_id'] as String?,
      pendingUserInput: json['pending_user_input'] as bool? ?? false,
      stagnationDetected: json['stagnation_detected'] as bool? ?? false,
      totalCycles: json['total_cycles'] as int? ?? 0,
      totalCost: (json['total_cost'] as num?)?.toDouble() ?? 0.0,
      summaryText: json['summary_text'] as String? ?? '',
    );
  }

  Map<String, dynamic> toJson() => {
    'project_id': projectId,
    'is_active': isActive,
    'current_activity': currentActivity,
    'active_plan_id': activePlanId,
    'pending_user_input': pendingUserInput,
    'stagnation_detected': stagnationDetected,
    'total_cycles': totalCycles,
    'total_cost': totalCost,
    'summary_text': summaryText,
  };
}

class PlanModel {
  final String id;
  final String projectId;
  final String objective;
  final List<String> milestones;
  final String status;
  final int version;
  final List<Map<String, dynamic>> tasks;

  const PlanModel({
    required this.id,
    required this.projectId,
    required this.objective,
    this.milestones = const [],
    this.status = 'ACTIVE',
    this.version = 1,
    this.tasks = const [],
  });

  factory PlanModel.fromJson(Map<String, dynamic> json) {
    return PlanModel(
      id: json['id'] as String? ?? '',
      projectId: json['project_id'] as String? ?? '',
      objective: json['objective'] as String? ?? '',
      milestones: (json['milestones'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? const [],
      status: json['status'] as String? ?? 'ACTIVE',
      version: json['version'] as int? ?? 1,
      tasks: (json['tasks'] as List<dynamic>?)?.map((e) => e as Map<String, dynamic>).toList() ?? const [],
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'project_id': projectId,
    'objective': objective,
    'milestones': milestones,
    'status': status,
    'version': version,
    'tasks': tasks,
  };
}
