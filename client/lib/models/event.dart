class ActivityItemModel {
  final String icon;
  final String title;
  final String subtitle;
  final String level;
  final String timestamp;
  final String? eventType;
  final String? projectId;
  final String? taskId;
  final String? workerId;

  const ActivityItemModel({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.level,
    required this.timestamp,
    this.eventType,
    this.projectId,
    this.taskId,
    this.workerId,
  });

  factory ActivityItemModel.fromJson(Map<String, dynamic> json) {
    return ActivityItemModel(
      icon: json['icon'] as String? ?? '●',
      title: json['title'] as String? ?? '',
      subtitle: json['subtitle'] as String? ?? '',
      level: json['level'] as String? ?? 'INFO',
      timestamp: json['timestamp'] as String? ?? '',
      eventType: json['event_type'] as String?,
      projectId: json['project_id'] as String?,
      taskId: json['task_id'] as String?,
      workerId: json['worker_id'] as String?,
    );
  }

  Map<String, dynamic> toJson() => {
    'icon': icon,
    'title': title,
    'subtitle': subtitle,
    'level': level,
    'timestamp': timestamp,
    'event_type': eventType,
    'project_id': projectId,
    'task_id': taskId,
    'worker_id': workerId,
  };
}

class EventModel {
  final String eventId;
  final String eventType;
  final String timestamp;
  final String source;
  final String? projectId;
  final String? taskId;
  final String? workerId;
  final String? correlationId;
  final String? causationId;
  final Map<String, dynamic> payload;
  final int? sequenceNumber;

  const EventModel({
    required this.eventId,
    required this.eventType,
    required this.timestamp,
    required this.source,
    this.projectId,
    this.taskId,
    this.workerId,
    this.correlationId,
    this.causationId,
    this.payload = const {},
    this.sequenceNumber,
  });

  factory EventModel.fromJson(Map<String, dynamic> json) {
    return EventModel(
      eventId: json['event_id'] as String? ?? '',
      eventType: json['event_type'] as String? ?? '',
      timestamp: json['timestamp'] as String? ?? '',
      source: json['source'] as String? ?? 'RUNTIME',
      projectId: json['project_id'] as String?,
      taskId: json['task_id'] as String?,
      workerId: json['worker_id'] as String?,
      correlationId: json['correlation_id'] as String?,
      causationId: json['causation_id'] as String?,
      payload: (json['payload'] as Map<String, dynamic>?) ?? const {},
      sequenceNumber: json['sequence_number'] as int?,
    );
  }

  Map<String, dynamic> toJson() => {
    'event_id': eventId,
    'event_type': eventType,
    'timestamp': timestamp,
    'source': source,
    'project_id': projectId,
    'task_id': taskId,
    'worker_id': workerId,
    'correlation_id': correlationId,
    'causation_id': causationId,
    'payload': payload,
    'sequence_number': sequenceNumber,
  };
}
