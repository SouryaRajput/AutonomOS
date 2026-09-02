class WorkerInfo {
  final String id;
  final String name;
  final String role;
  final String description;
  final String status;
  final List<String> capabilities;
  final List<String> permissions;
  final List<String> tools;
  final String? activeTaskId;
  final String? activeTaskTitle;

  const WorkerInfo({
    required this.id,
    required this.name,
    required this.role,
    this.description = '',
    this.status = 'REGISTERED',
    this.capabilities = const [],
    this.permissions = const [],
    this.tools = const [],
    this.activeTaskId,
    this.activeTaskTitle,
  });

  factory WorkerInfo.fromJson(Map<String, dynamic> json) {
    return WorkerInfo(
      id: json['id'] as String? ?? '',
      name: json['name'] as String? ?? '',
      role: json['role'] as String? ?? '',
      description: json['description'] as String? ?? '',
      status: json['status'] as String? ?? 'REGISTERED',
      capabilities: (json['capabilities'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? const [],
      permissions: (json['permissions'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? const [],
      tools: (json['tools'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? const [],
      activeTaskId: json['active_task_id'] as String?,
      activeTaskTitle: json['active_task_title'] as String?,
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'name': name,
    'role': role,
    'description': description,
    'status': status,
    'capabilities': capabilities,
    'permissions': permissions,
    'tools': tools,
    'active_task_id': activeTaskId,
    'active_task_title': activeTaskTitle,
  };
}
