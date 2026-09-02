class TaskItem {
  final String id;
  final String projectId;
  final String title;
  final String objective;
  final String status;
  final int priority;
  final String risk;
  final String? assignedWorker;
  final String? assignedWorkerName;
  final List<String> dependencies;
  final List<String> artifacts;
  final int attempts;
  final int maxAttempts;
  final String createdAt;
  final String? startedAt;
  final String? completedAt;

  const TaskItem({
    required this.id,
    required this.projectId,
    required this.title,
    this.objective = '',
    this.status = 'PENDING',
    this.priority = 1,
    this.risk = 'LOW',
    this.assignedWorker,
    this.assignedWorkerName,
    this.dependencies = const [],
    this.artifacts = const [],
    this.attempts = 0,
    this.maxAttempts = 3,
    required this.createdAt,
    this.startedAt,
    this.completedAt,
  });

  factory TaskItem.fromJson(Map<String, dynamic> json) {
    return TaskItem(
      id: json['id'] as String? ?? '',
      projectId: json['project_id'] as String? ?? '',
      title: json['title'] as String? ?? '',
      objective: json['objective'] as String? ?? '',
      status: json['status'] as String? ?? 'PENDING',
      priority: json['priority'] as int? ?? 1,
      risk: json['risk'] as String? ?? 'LOW',
      assignedWorker: json['assigned_worker'] as String?,
      assignedWorkerName: json['assigned_worker_name'] as String?,
      dependencies: (json['dependencies'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? const [],
      artifacts: (json['artifacts'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? const [],
      attempts: json['attempts'] as int? ?? 0,
      maxAttempts: json['max_attempts'] as int? ?? 3,
      createdAt: json['created_at'] as String? ?? '',
      startedAt: json['started_at'] as String?,
      completedAt: json['completed_at'] as String?,
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'project_id': projectId,
    'title': title,
    'objective': objective,
    'status': status,
    'priority': priority,
    'risk': risk,
    'assigned_worker': assignedWorker,
    'assigned_worker_name': assignedWorkerName,
    'dependencies': dependencies,
    'artifacts': artifacts,
    'attempts': attempts,
    'max_attempts': maxAttempts,
    'created_at': createdAt,
    'started_at': startedAt,
    'completed_at': completedAt,
  };
}
