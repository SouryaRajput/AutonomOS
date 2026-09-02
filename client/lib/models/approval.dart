class ApprovalItem {
  final String id;
  final String projectId;
  final String taskId;
  final String workerId;
  final String action;
  final String riskLevel;
  final String status;
  final String reason;
  final String requestedScope;
  final List<String> affectedResources;
  final List<String> evidence;
  final String createdAt;
  final String? decidedAt;
  final String? decidedBy;
  final String? rejectionReason;

  const ApprovalItem({
    required this.id,
    required this.projectId,
    required this.taskId,
    required this.workerId,
    required this.action,
    this.riskLevel = 'HIGH',
    this.status = 'PENDING',
    this.reason = '',
    this.requestedScope = '',
    this.affectedResources = const [],
    this.evidence = const [],
    required this.createdAt,
    this.decidedAt,
    this.decidedBy,
    this.rejectionReason,
  });

  factory ApprovalItem.fromJson(Map<String, dynamic> json) {
    return ApprovalItem(
      id: json['id'] as String? ?? '',
      projectId: json['project_id'] as String? ?? '',
      taskId: json['task_id'] as String? ?? '',
      workerId: json['worker_id'] as String? ?? '',
      action: json['action'] as String? ?? '',
      riskLevel: json['risk_level'] as String? ?? 'HIGH',
      status: json['status'] as String? ?? 'PENDING',
      reason: json['reason'] as String? ?? '',
      requestedScope: json['requested_scope'] as String? ?? '',
      affectedResources: (json['affected_resources'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? const [],
      evidence: (json['evidence'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? const [],
      createdAt: json['created_at'] as String? ?? '',
      decidedAt: json['decided_at'] as String?,
      decidedBy: json['decided_by'] as String?,
      rejectionReason: json['rejection_reason'] as String?,
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'project_id': projectId,
    'task_id': taskId,
    'worker_id': workerId,
    'action': action,
    'risk_level': riskLevel,
    'status': status,
    'reason': reason,
    'requested_scope': requestedScope,
    'affected_resources': affectedResources,
    'evidence': evidence,
    'created_at': createdAt,
    'decided_at': decidedAt,
    'decided_by': decidedBy,
    'rejection_reason': rejectionReason,
  };
}

class UserInputItem {
  final String id;
  final String projectId;
  final String taskId;
  final String question;
  final String context;
  final String? answer;
  final String status;
  final String createdAt;

  const UserInputItem({
    required this.id,
    required this.projectId,
    required this.taskId,
    required this.question,
    this.context = '',
    this.answer,
    this.status = 'PENDING',
    required this.createdAt,
  });

  factory UserInputItem.fromJson(Map<String, dynamic> json) {
    return UserInputItem(
      id: json['id'] as String? ?? '',
      projectId: json['project_id'] as String? ?? '',
      taskId: json['task_id'] as String? ?? '',
      question: json['question'] as String? ?? '',
      context: json['context'] as String? ?? '',
      answer: json['answer'] as String?,
      status: json['status'] as String? ?? 'PENDING',
      createdAt: json['created_at'] as String? ?? '',
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'project_id': projectId,
    'task_id': taskId,
    'question': question,
    'context': context,
    'answer': answer,
    'status': status,
    'created_at': createdAt,
  };
}

class DecisionItem {
  final String id;
  final String projectId;
  final String taskId;
  final String title;
  final List<String> options;
  final String? chosenOption;
  final String rationale;
  final String status;
  final String createdAt;

  const DecisionItem({
    required this.id,
    required this.projectId,
    required this.taskId,
    required this.title,
    this.options = const [],
    this.chosenOption,
    this.rationale = '',
    this.status = 'PENDING',
    required this.createdAt,
  });

  factory DecisionItem.fromJson(Map<String, dynamic> json) {
    return DecisionItem(
      id: json['id'] as String? ?? '',
      projectId: json['project_id'] as String? ?? '',
      taskId: json['task_id'] as String? ?? '',
      title: json['title'] as String? ?? '',
      options: (json['options'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? const [],
      chosenOption: json['chosen_option'] as String?,
      rationale: json['rationale'] as String? ?? '',
      status: json['status'] as String? ?? 'PENDING',
      createdAt: json['created_at'] as String? ?? '',
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'project_id': projectId,
    'task_id': taskId,
    'title': title,
    'options': options,
    'chosen_option': chosenOption,
    'rationale': rationale,
    'status': status,
    'created_at': createdAt,
  };
}
