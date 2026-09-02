import '../../models/event.dart';

/// Converts technical backend runtime events into clean, human-readable product summaries.
class ActivityTranslator {
  ActivityTranslator._();

  static String getWorkerFriendlyName(String? workerId) {
    if (workerId == null || workerId.isEmpty) return 'System';
    final lower = workerId.toLowerCase();
    if (lower.contains('manager') || lower.contains('orchestrator')) return 'Manager';
    if (lower.contains('researcher') || lower.contains('research')) return 'Researcher';
    if (lower.contains('programmer') || lower.contains('prog') || lower.contains('coder')) return 'Programmer';
    if (lower.contains('tester') || lower.contains('qa')) return 'Tester';
    return workerId;
  }

  static String getHumanReadableTitle(EventModel event) {
    final worker = getWorkerFriendlyName(event.workerId);
    final eventType = event.eventType.toUpperCase();

    switch (eventType) {
      case 'PROJECT_CREATED':
        return 'Project created';
      case 'TASK_CREATED':
        final title = event.payload['title'] ?? event.payload['name'] ?? 'task';
        return 'Task "$title" scheduled';
      case 'TASK_ASSIGNED':
        return '$worker assigned to task';
      case 'TASK_STARTED':
      case 'WORKER_TASK_STARTED':
        return '$worker started task';
      case 'PROGRAMMER_CODE_MODIFIED':
      case 'FILE_MODIFIED':
      case 'WORKSPACE_MODIFIED':
        final path = event.payload['path'] ?? event.payload['file'] ?? 'source file';
        return '$worker modified $path';
      case 'RESEARCH_COMPLETED':
      case 'RESEARCH_QUERY_EXECUTED':
        return '$worker finished investigation';
      case 'TEST_STARTED':
      case 'TESTER_STARTED':
        return '$worker started test suite';
      case 'TEST_COMPLETED':
        final passed = event.payload['passed_count'] ?? event.payload['passed'] ?? 0;
        return '$worker verified $passed tests';
      case 'DEFECT_DETECTED':
        return '$worker discovered defect';
      case 'REGRESSION_DETECTED':
        return '$worker detected regression';
      case 'VERIFICATION_PASSED':
      case 'VERIFICATION_COMPLETED':
        return 'Deterministic verification PASSED';
      case 'VERIFICATION_FAILED':
        return 'Deterministic verification FAILED';
      case 'APPROVAL_REQUESTED':
        final action = event.payload['action'] ?? 'action';
        return 'Approval requested for $action';
      case 'APPROVAL_GRANTED':
        return 'Action authorization granted';
      case 'APPROVAL_REJECTED':
        return 'Action authorization rejected';
      case 'EMERGENCY_STOP_ACTIVATED':
        return 'Emergency workforce halt activated';
      case 'EMERGENCY_STOP_CLEARED':
        return 'Emergency stop cleared';
      case 'TASK_COMPLETED':
        return '$worker completed task';
      case 'TASK_FAILED':
        return '$worker reported task failure';
      case 'TASK_STATUS_TRANSITION':
        final fromStatus = event.payload['from_status'] ?? event.payload['old_status'];
        final toStatus = event.payload['to_status'] ?? event.payload['new_status'];
        if (toStatus == 'REPORTING' || toStatus == 'COMPLETED') {
          return '$worker finished execution and submitted result';
        }
        if (toStatus == 'RUNNING') {
          return '$worker is executing task';
        }
        if (toStatus == 'BLOCKED') {
          return '$worker is blocked';
        }
        return 'Task status changed to $toStatus';
      default:
        final clean = eventType.replaceAll('_', ' ').toLowerCase();
        return '$worker $clean';
    }
  }

  static String getHumanReadableSubtitle(EventModel event) {
    final payload = event.payload;
    if (payload.containsKey('summary')) {
      return payload['summary'].toString();
    }
    if (payload.containsKey('reason')) {
      return payload['reason'].toString();
    }
    if (payload.containsKey('objective')) {
      return payload['objective'].toString();
    }
    if (payload.containsKey('notes')) {
      return payload['notes'].toString();
    }
    if (payload.containsKey('error')) {
      return 'Error: ${payload['error']}';
    }
    if (payload.containsKey('diff_summary')) {
      return payload['diff_summary'].toString();
    }
    return '';
  }
}
