class AutonomyPolicyModel {
  final String id;
  final String projectId;
  final String autonomyLevel;
  final List<String> allowedTools;
  final List<String> deniedTools;
  final double maxCostLimit;
  final int maxIterations;
  final int version;

  const AutonomyPolicyModel({
    required this.id,
    required this.projectId,
    this.autonomyLevel = 'BALANCED',
    this.allowedTools = const [],
    this.deniedTools = const [],
    this.maxCostLimit = 10.0,
    this.maxIterations = 15,
    this.version = 1,
  });

  factory AutonomyPolicyModel.fromJson(Map<String, dynamic> json) {
    return AutonomyPolicyModel(
      id: json['id'] as String? ?? '',
      projectId: json['project_id'] as String? ?? '',
      autonomyLevel: json['autonomy_level'] as String? ?? 'BALANCED',
      allowedTools: (json['allowed_tools'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? const [],
      deniedTools: (json['denied_tools'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? const [],
      maxCostLimit: (json['max_cost_limit'] as num?)?.toDouble() ?? 10.0,
      maxIterations: json['max_iterations'] as int? ?? 15,
      version: json['version'] as int? ?? 1,
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'project_id': projectId,
    'autonomy_level': autonomyLevel,
    'allowed_tools': allowedTools,
    'denied_tools': deniedTools,
    'max_cost_limit': maxCostLimit,
    'max_iterations': maxIterations,
    'version': version,
  };
}
