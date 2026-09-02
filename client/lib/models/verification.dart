enum VerificationState {
  verified('VERIFIED'),
  failed('FAILED'),
  partiallyVerified('PARTIALLY_VERIFIED'),
  blocked('BLOCKED'),
  inconclusive('INCONCLUSIVE'),
  pending('PENDING'),
  running('RUNNING');

  final String value;
  const VerificationState(this.value);

  static VerificationState fromString(String val) {
    final upper = val.toUpperCase();
    if (upper == 'PASSED' || upper == 'VERIFIED' || upper == 'SUCCESS') {
      return VerificationState.verified;
    }
    if (upper == 'FAILED') {
      return VerificationState.failed;
    }
    if (upper == 'UNCERTAIN' || upper == 'PARTIALLY_VERIFIED') {
      return VerificationState.partiallyVerified;
    }
    if (upper == 'BLOCKED') {
      return VerificationState.blocked;
    }
    if (upper == 'RUNNING') {
      return VerificationState.running;
    }
    if (upper == 'PENDING') {
      return VerificationState.pending;
    }
    return VerificationState.inconclusive;
  }
}

class VerificationCheckModel {
  final String id;
  final String checkType;
  final String description;
  final String status;
  final bool required;
  final dynamic expectedResult;
  final dynamic actualResult;
  final List<String> evidenceIds;
  final String? errorMessage;
  final double durationMs;

  const VerificationCheckModel({
    required this.id,
    required this.checkType,
    required this.description,
    this.status = 'NOT_RUN',
    this.required = true,
    this.expectedResult,
    this.actualResult,
    this.evidenceIds = const [],
    this.errorMessage,
    this.durationMs = 0.0,
  });

  factory VerificationCheckModel.fromJson(Map<String, dynamic> json) {
    return VerificationCheckModel(
      id: json['id'] as String? ?? '',
      checkType: json['check_type'] as String? ?? 'GENERAL',
      description: json['description'] as String? ?? '',
      status: json['status'] as String? ?? 'NOT_RUN',
      required: json['required'] as bool? ?? true,
      expectedResult: json['expected_result'],
      actualResult: json['actual_result'],
      evidenceIds: (json['evidence_ids'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? const [],
      errorMessage: json['error_message'] as String?,
      durationMs: (json['duration_ms'] as num?)?.toDouble() ?? 0.0,
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'check_type': checkType,
    'description': description,
    'status': status,
    'required': required,
    'expected_result': expectedResult,
    'actual_result': actualResult,
    'evidence_ids': evidenceIds,
    'error_message': errorMessage,
    'duration_ms': durationMs,
  };
}

class VerificationReportModel {
  final String id;
  final String projectId;
  final String taskId;
  final VerificationState state;
  final List<VerificationCheckModel> checks;
  final int totalChecks;
  final int passedChecks;
  final int failedChecks;
  final String summary;
  final String timestamp;

  const VerificationReportModel({
    required this.id,
    required this.projectId,
    required this.taskId,
    required this.state,
    this.checks = const [],
    this.totalChecks = 0,
    this.passedChecks = 0,
    this.failedChecks = 0,
    this.summary = '',
    required this.timestamp,
  });

  factory VerificationReportModel.fromJson(Map<String, dynamic> json) {
    final rawChecks = json['checks'] as List<dynamic>? ?? [];
    final checksList = rawChecks.map((c) => VerificationCheckModel.fromJson(c as Map<String, dynamic>)).toList();
    final statusStr = json['status'] as String? ?? json['state'] as String? ?? 'PENDING';

    return VerificationReportModel(
      id: json['id'] as String? ?? '',
      projectId: json['project_id'] as String? ?? '',
      taskId: json['task_id'] as String? ?? '',
      state: VerificationState.fromString(statusStr),
      checks: checksList,
      totalChecks: json['total_checks'] as int? ?? checksList.length,
      passedChecks: json['passed_checks'] as int? ?? checksList.where((c) => c.status.toUpperCase() == 'PASSED').length,
      failedChecks: json['failed_checks'] as int? ?? checksList.where((c) => c.status.toUpperCase() == 'FAILED').length,
      summary: json['summary'] as String? ?? '',
      timestamp: json['timestamp'] as String? ?? '',
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'project_id': projectId,
    'task_id': taskId,
    'state': state.value,
    'checks': checks.map((c) => c.toJson()).toList(),
    'total_checks': totalChecks,
    'passed_checks': passedChecks,
    'failed_checks': failedChecks,
    'summary': summary,
    'timestamp': timestamp,
  };
}
