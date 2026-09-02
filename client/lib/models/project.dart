class Project {
  final String id;
  final String name;
  final String description;
  final String rootPath;
  final String status;
  final int taskCount;
  final int workerCount;
  final String createdAt;
  final String updatedAt;

  const Project({
    required this.id,
    required this.name,
    this.description = '',
    required this.rootPath,
    this.status = 'ACTIVE',
    this.taskCount = 0,
    this.workerCount = 0,
    required this.createdAt,
    required this.updatedAt,
  });

  factory Project.fromJson(Map<String, dynamic> json) {
    return Project(
      id: json['id'] as String? ?? '',
      name: json['name'] as String? ?? '',
      description: json['description'] as String? ?? '',
      rootPath: json['root_path'] as String? ?? '',
      status: json['status'] as String? ?? 'ACTIVE',
      taskCount: json['task_count'] as int? ?? 0,
      workerCount: json['worker_count'] as int? ?? 0,
      createdAt: json['created_at'] as String? ?? '',
      updatedAt: json['updated_at'] as String? ?? '',
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'name': name,
    'description': description,
    'root_path': rootPath,
    'status': status,
    'task_count': taskCount,
    'worker_count': workerCount,
    'created_at': createdAt,
    'updated_at': updatedAt,
  };
}
