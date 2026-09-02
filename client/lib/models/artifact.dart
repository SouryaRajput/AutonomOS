enum ArtifactKind {
  report('REPORT'),
  sourceCode('SOURCE_CODE'),
  testReport('TEST_REPORT'),
  diff('DIFF'),
  screenshot('SCREENSHOT'),
  evidence('EVIDENCE'),
  memoryDoc('MEMORY'),
  general('GENERAL');

  final String value;
  const ArtifactKind(this.value);

  static ArtifactKind fromString(String val) {
    final upper = val.toUpperCase();
    for (final k in ArtifactKind.values) {
      if (k.value == upper || k.name.toUpperCase() == upper) {
        return k;
      }
    }
    if (upper.contains('REPORT')) return ArtifactKind.report;
    if (upper.contains('TEST')) return ArtifactKind.testReport;
    if (upper.contains('DIFF') || upper.contains('PATCH')) return ArtifactKind.diff;
    if (upper.contains('IMAGE') || upper.contains('SCREENSHOT') || upper.contains('PNG') || upper.contains('JPG')) return ArtifactKind.screenshot;
    if (upper.contains('CODE') || upper.contains('SOURCE') || upper.contains('.PY') || upper.contains('.DART')) return ArtifactKind.sourceCode;
    return ArtifactKind.general;
  }
}

class ArtifactModel {
  final String id;
  final String projectId;
  final String? taskId;
  final String? workerId;
  final ArtifactKind kind;
  final String path;
  String get relativePath => path;
  final String description;
  final String? checksum;
  final String? content;
  final int sizeBytes;
  final String createdAt;
  final Map<String, dynamic> metadata;

  const ArtifactModel({
    required this.id,
    required this.projectId,
    this.taskId,
    this.workerId,
    required this.kind,
    required this.path,
    this.description = '',
    this.checksum,
    this.content,
    this.sizeBytes = 0,
    required this.createdAt,
    this.metadata = const {},
  });

  factory ArtifactModel.fromJson(Map<String, dynamic> json) {
    final typeRaw = json['type'] as String? ?? json['kind'] as String? ?? 'GENERAL';
    return ArtifactModel(
      id: json['id'] as String? ?? '',
      projectId: json['project_id'] as String? ?? '',
      taskId: json['task_id'] as String?,
      workerId: json['worker_id'] as String?,
      kind: ArtifactKind.fromString(typeRaw),
      path: json['path'] as String? ?? '',
      description: json['description'] as String? ?? '',
      checksum: json['checksum'] as String?,
      content: json['content'] as String?,
      sizeBytes: json['size_bytes'] as int? ?? 0,
      createdAt: json['created_at'] as String? ?? '',
      metadata: (json['metadata'] as Map<String, dynamic>?) ?? const {},
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'project_id': projectId,
    'task_id': taskId,
    'worker_id': workerId,
    'type': kind.value,
    'path': path,
    'description': description,
    'checksum': checksum,
    'content': content,
    'size_bytes': sizeBytes,
    'created_at': createdAt,
    'metadata': metadata,
  };
}
