class WorkflowInfo {
  final String id;
  final String projectId;
  final String title;
  final String status;
  final List<String> taskIds;
  final int currentStep;
  final int iterations;
  final String createdAt;

  const WorkflowInfo({
    required this.id,
    required this.projectId,
    required this.title,
    this.status = 'PENDING',
    this.taskIds = const [],
    this.currentStep = 0,
    this.iterations = 0,
    required this.createdAt,
  });

  factory WorkflowInfo.fromJson(Map<String, dynamic> json) {
    return WorkflowInfo(
      id: json['id'] as String? ?? '',
      projectId: json['project_id'] as String? ?? '',
      title: json['title'] as String? ?? '',
      status: json['status'] as String? ?? 'PENDING',
      taskIds: (json['task_ids'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? const [],
      currentStep: json['current_step'] as int? ?? 0,
      iterations: json['iterations'] as int? ?? 0,
      createdAt: json['created_at'] as String? ?? '',
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'project_id': projectId,
    'title': title,
    'status': status,
    'task_ids': taskIds,
    'current_step': currentStep,
    'iterations': iterations,
    'created_at': createdAt,
  };
}

class HandoffInfo {
  final String id;
  final String sourceWorker;
  final String? destinationWorker;
  final String summary;
  final String handoffType;
  final String createdAt;

  const HandoffInfo({
    required this.id,
    required this.sourceWorker,
    this.destinationWorker,
    required this.summary,
    this.handoffType = 'GENERAL',
    required this.createdAt,
  });

  factory HandoffInfo.fromJson(Map<String, dynamic> json) {
    return HandoffInfo(
      id: json['id'] as String? ?? '',
      sourceWorker: json['source_worker'] as String? ?? '',
      destinationWorker: json['destination_worker'] as String?,
      summary: json['summary'] as String? ?? '',
      handoffType: json['handoff_type'] as String? ?? 'GENERAL',
      createdAt: json['created_at'] as String? ?? '',
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'source_worker': sourceWorker,
    'destination_worker': destinationWorker,
    'summary': summary,
    'handoff_type': handoffType,
    'created_at': createdAt,
  };
}
