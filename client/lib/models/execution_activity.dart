enum ActivityStatus {
  pending('PENDING'),
  running('RUNNING'),
  waiting('WAITING'),
  stalled('STALLED'),
  completed('COMPLETED'),
  failed('FAILED'),
  cancelled('CANCELLED');

  final String value;
  const ActivityStatus(this.value);

  static ActivityStatus fromString(String val) {
    return ActivityStatus.values.firstWhere(
      (e) => e.value.toUpperCase() == val.toUpperCase() || e.name.toUpperCase() == val.toUpperCase(),
      orElse: () => ActivityStatus.pending,
    );
  }
}

enum FileOperationType {
  read('READ'),
  created('CREATED'),
  modified('MODIFIED'),
  deleted('DELETED');

  final String value;
  const FileOperationType(this.value);

  static FileOperationType fromString(String val) {
    return FileOperationType.values.firstWhere(
      (e) => e.value.toUpperCase() == val.toUpperCase() || e.name.toUpperCase() == val.toUpperCase(),
      orElse: () => FileOperationType.read,
    );
  }
}

class CommandLineItem {
  final String command;
  final int? exitCode;
  final double? durationMs;
  final bool isRunning;
  final String outputPreview;
  final String timestamp;

  const CommandLineItem({
    required this.command,
    this.exitCode,
    this.durationMs,
    this.isRunning = false,
    this.outputPreview = '',
    this.timestamp = '',
  });

  factory CommandLineItem.fromJson(Map<String, dynamic> json) {
    return CommandLineItem(
      command: json['command'] as String? ?? '',
      exitCode: json['exit_code'] as int?,
      durationMs: (json['duration_ms'] as num?)?.toDouble(),
      isRunning: json['is_running'] as bool? ?? false,
      outputPreview: json['output_preview'] as String? ?? '',
      timestamp: json['timestamp'] as String? ?? '',
    );
  }

  Map<String, dynamic> toJson() => {
    'command': command,
    'exit_code': exitCode,
    'duration_ms': durationMs,
    'is_running': isRunning,
    'output_preview': outputPreview,
    'timestamp': timestamp,
  };
}

class FileActivityItem {
  final String path;
  final FileOperationType operation;
  final int? sizeBytes;
  final String timestamp;

  const FileActivityItem({
    required this.path,
    required this.operation,
    this.sizeBytes,
    this.timestamp = '',
  });

  factory FileActivityItem.fromJson(Map<String, dynamic> json) {
    return FileActivityItem(
      path: json['path'] as String? ?? '',
      operation: FileOperationType.fromString(json['operation'] as String? ?? 'READ'),
      sizeBytes: json['size_bytes'] as int?,
      timestamp: json['timestamp'] as String? ?? '',
    );
  }

  Map<String, dynamic> toJson() => {
    'path': path,
    'operation': operation.value,
    'size_bytes': sizeBytes,
    'timestamp': timestamp,
  };
}

class WorkerActivityItem {
  final String workerId;
  final String name;
  final String role;
  final String status;
  final String currentAction;
  final String taskTarget;
  final String? error;
  final String startedAt;
  final String? completedAt;

  const WorkerActivityItem({
    required this.workerId,
    required this.name,
    required this.role,
    this.status = 'IDLE',
    this.currentAction = '',
    this.taskTarget = '',
    this.error,
    this.startedAt = '',
    this.completedAt,
  });

  factory WorkerActivityItem.fromJson(Map<String, dynamic> json) {
    return WorkerActivityItem(
      workerId: json['worker_id'] as String? ?? '',
      name: json['name'] as String? ?? 'Worker',
      role: json['role'] as String? ?? 'Specialist',
      status: json['status'] as String? ?? 'IDLE',
      currentAction: json['current_action'] as String? ?? '',
      taskTarget: json['task_target'] as String? ?? '',
      error: json['error'] as String?,
      startedAt: json['started_at'] as String? ?? '',
      completedAt: json['completed_at'] as String?,
    );
  }

  Map<String, dynamic> toJson() => {
    'worker_id': workerId,
    'name': name,
    'role': role,
    'status': status,
    'current_action': currentAction,
    'task_target': taskTarget,
    'error': error,
    'started_at': startedAt,
    'completed_at': completedAt,
  };
}

class ExecutionActivity {
  final String activityId;
  final String projectId;
  final String taskId;
  final String correlationId;
  final String workerId;
  final String workerType;
  final String title;
  final String description;
  final ActivityStatus status;
  final String startTime;
  final String? endTime;
  final double? durationMs;
  final String currentAction;
  final List<String> completedActions;
  final List<CommandLineItem> commands;
  final List<String> filesRead;
  final List<FileActivityItem> filesChanged;
  final List<WorkerActivityItem> workers;
  final Map<String, dynamic> metrics;
  final String? errorSummary;
  final String? waitingReason;
  final bool isLive;

  const ExecutionActivity({
    required this.activityId,
    required this.projectId,
    this.taskId = '',
    this.correlationId = '',
    this.workerId = '',
    this.workerType = 'Manager',
    this.title = 'Workforce Execution',
    this.description = '',
    this.status = ActivityStatus.pending,
    this.startTime = '',
    this.endTime,
    this.durationMs,
    this.currentAction = 'Idle',
    this.completedActions = const [],
    this.commands = const [],
    this.filesRead = const [],
    this.filesChanged = const [],
    this.workers = const [],
    this.metrics = const {},
    this.errorSummary,
    this.waitingReason,
    this.isLive = true,
  });

  factory ExecutionActivity.fromJson(Map<String, dynamic> json) {
    return ExecutionActivity(
      activityId: json['activity_id'] as String? ?? '',
      projectId: json['project_id'] as String? ?? '',
      taskId: json['task_id'] as String? ?? '',
      correlationId: json['correlation_id'] as String? ?? '',
      workerId: json['worker_id'] as String? ?? '',
      workerType: json['worker_type'] as String? ?? 'Manager',
      title: json['title'] as String? ?? 'Workforce Execution',
      description: json['description'] as String? ?? '',
      status: ActivityStatus.fromString(json['status'] as String? ?? 'PENDING'),
      startTime: json['start_time'] as String? ?? '',
      endTime: json['end_time'] as String?,
      durationMs: (json['duration_ms'] as num?)?.toDouble(),
      currentAction: json['current_action'] as String? ?? 'Idle',
      completedActions: (json['completed_actions'] as List<dynamic>?)?.cast<String>() ?? const [],
      commands: (json['commands'] as List<dynamic>?)
              ?.map((c) => CommandLineItem.fromJson(c as Map<String, dynamic>))
              .toList() ??
          const [],
      filesRead: (json['files_read'] as List<dynamic>?)?.cast<String>() ?? const [],
      filesChanged: (json['files_changed'] as List<dynamic>?)
              ?.map((f) => FileActivityItem.fromJson(f as Map<String, dynamic>))
              .toList() ??
          const [],
      workers: (json['workers'] as List<dynamic>?)
              ?.map((w) => WorkerActivityItem.fromJson(w as Map<String, dynamic>))
              .toList() ??
          const [],
      metrics: (json['metrics'] as Map<String, dynamic>?) ?? const {},
      errorSummary: json['error_summary'] as String?,
      waitingReason: json['waiting_reason'] as String?,
      isLive: json['is_live'] as bool? ?? true,
    );
  }

  Map<String, dynamic> toJson() => {
    'activity_id': activityId,
    'project_id': projectId,
    'task_id': taskId,
    'correlation_id': correlationId,
    'worker_id': workerId,
    'worker_type': workerType,
    'title': title,
    'description': description,
    'status': status.value,
    'start_time': startTime,
    'end_time': endTime,
    'duration_ms': durationMs,
    'current_action': currentAction,
    'completed_actions': completedActions,
    'commands': commands.map((c) => c.toJson()).toList(),
    'files_read': filesRead,
    'files_changed': filesChanged.map((f) => f.toJson()).toList(),
    'workers': workers.map((w) => w.toJson()).toList(),
    'metrics': metrics,
    'error_summary': errorSummary,
    'waiting_reason': waitingReason,
    'is_live': isLive,
  };
}
