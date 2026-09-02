class EvidenceModel {
  final String id;
  final String taskId;
  final String evidenceType;
  final String data;
  final String? sourceWorkerId;
  final String createdAt;
  final Map<String, dynamic> metadata;

  const EvidenceModel({
    required this.id,
    required this.taskId,
    required this.evidenceType,
    required this.data,
    this.sourceWorkerId,
    required this.createdAt,
    this.metadata = const {},
  });

  factory EvidenceModel.fromJson(Map<String, dynamic> json) {
    return EvidenceModel(
      id: json['id'] as String? ?? '',
      taskId: json['task_id'] as String? ?? '',
      evidenceType: json['evidence_type'] as String? ?? 'GENERAL',
      data: json['data'] as String? ?? json['data_preview'] as String? ?? '',
      sourceWorkerId: json['source_worker_id'] as String? ?? json['worker_id'] as String?,
      createdAt: json['created_at'] as String? ?? '',
      metadata: (json['metadata'] as Map<String, dynamic>?) ?? const {},
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'task_id': taskId,
    'evidence_type': evidenceType,
    'data': data,
    'source_worker_id': sourceWorkerId,
    'created_at': createdAt,
    'metadata': metadata,
  };
}

class EvidenceTraceItem {
  final String requirement;
  final String checkDescription;
  final String checkType;
  final String checkStatus;
  final String workerName;
  final String evidenceSnippet;
  final String? artifactPath;

  const EvidenceTraceItem({
    required this.requirement,
    required this.checkDescription,
    required this.checkType,
    required this.checkStatus,
    required this.workerName,
    required this.evidenceSnippet,
    this.artifactPath,
  });
}
