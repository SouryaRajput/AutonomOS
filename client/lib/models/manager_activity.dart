import 'package:flutter/material.dart';

enum ActivityEventStatus {
  inProgress,
  completed,
  disabled,
  warning,
  failed,
}

class ManagerActivityStep {
  final String id;
  final String title;
  final ActivityEventStatus status;
  final String? timestamp;
  final String? decision;
  final String? reason;
  final List<String> selectedContext;
  final List<Map<String, dynamic>> tasks;
  final Map<String, dynamic>? auditInfo;
  final String? workerPrompt;
  final Map<String, dynamic>? workerStatus;
  final bool isExpanded;

  const ManagerActivityStep({
    required this.id,
    required this.title,
    required this.status,
    this.timestamp,
    this.decision,
    this.reason,
    this.selectedContext = const [],
    this.tasks = const [],
    this.auditInfo,
    this.workerPrompt,
    this.workerStatus,
    this.isExpanded = false,
  });

  ManagerActivityStep copyWith({
    String? id,
    String? title,
    ActivityEventStatus? status,
    String? timestamp,
    String? decision,
    String? reason,
    List<String>? selectedContext,
    List<Map<String, dynamic>>? tasks,
    Map<String, dynamic>? auditInfo,
    String? workerPrompt,
    Map<String, dynamic>? workerStatus,
    bool? isExpanded,
  }) {
    return ManagerActivityStep(
      id: id ?? this.id,
      title: title ?? this.title,
      status: status ?? this.status,
      timestamp: timestamp ?? this.timestamp,
      decision: decision ?? this.decision,
      reason: reason ?? this.reason,
      selectedContext: selectedContext ?? this.selectedContext,
      tasks: tasks ?? this.tasks,
      auditInfo: auditInfo ?? this.auditInfo,
      workerPrompt: workerPrompt ?? this.workerPrompt,
      workerStatus: workerStatus ?? this.workerStatus,
      isExpanded: isExpanded ?? this.isExpanded,
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'title': title,
      'status': status.name,
      'timestamp': timestamp,
      'decision': decision,
      'reason': reason,
      'selectedContext': selectedContext,
      'tasks': tasks,
      'auditInfo': auditInfo,
      'workerPrompt': workerPrompt,
      'workerStatus': workerStatus,
      'isExpanded': isExpanded,
    };
  }

  factory ManagerActivityStep.fromJson(Map<String, dynamic> json) {
    return ManagerActivityStep(
      id: json['id'] as String,
      title: json['title'] as String,
      status: ActivityEventStatus.values.firstWhere(
        (e) => e.name == json['status'],
        orElse: () => ActivityEventStatus.completed,
      ),
      timestamp: json['timestamp'] as String?,
      decision: json['decision'] as String?,
      reason: json['reason'] as String?,
      selectedContext: (json['selectedContext'] as List<dynamic>?)?.cast<String>() ?? const [],
      tasks: (json['tasks'] as List<dynamic>?)?.cast<Map<String, dynamic>>() ?? const [],
      auditInfo: json['auditInfo'] as Map<String, dynamic>?,
      workerPrompt: json['workerPrompt'] as String?,
      workerStatus: json['workerStatus'] as Map<String, dynamic>?,
      isExpanded: json['isExpanded'] as bool? ?? false,
    );
  }
}
