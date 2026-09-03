import 'dart:async';
import '../models/event.dart';
import '../models/execution_activity.dart';

/// Secret redaction patterns
final List<RegExp> _redactPatterns = [
  RegExp(r'(--(?:api[-_]?key|key|token|password|secret|auth)\s+[^\s]+)', caseSensitive: false),
  RegExp(r'((?:api[-_]?key|key|token|password|secret|auth)=[^\s]+)', caseSensitive: false),
  RegExp(r'(Bearer\s+[A-Za-z0-9_\-\.]+)', caseSensitive: false),
  RegExp(r'(Authorization:\s*[^\s]+)', caseSensitive: false),
];

String redactCommand(String cmd) {
  if (cmd.isEmpty) return '';
  var result = cmd;
  for (final pattern in _redactPatterns) {
    result = result.replaceAll(pattern, '[REDACTED]');
  }
  return result;
}

/// Client-side Workforce Activity Projector.
/// Projects runtime EventModel stream into clean, human-visible ExecutionActivity state.
class ActivityProjectorService {
  static final ActivityProjectorService _instance = ActivityProjectorService._internal();
  factory ActivityProjectorService() => _instance;
  ActivityProjectorService._internal();

  final Map<String, ExecutionActivity> _activities = {};
  final StreamController<ExecutionActivity> _streamController = StreamController<ExecutionActivity>.broadcast();

  Stream<ExecutionActivity> get activityStream => _streamController.stream;

  ExecutionActivity? getActivity(String key) => _activities[key];

  ExecutionActivity getOrCreateActivity({
    required String projectId,
    String taskId = '',
    String correlationId = '',
    String workerId = '',
    String title = 'Workforce Execution',
  }) {
    final key = correlationId.isNotEmpty
        ? correlationId
        : (taskId.isNotEmpty ? taskId : (projectId.isNotEmpty ? '$projectId-main' : 'global-main'));

    if (!_activities.containsKey(key)) {
      final now = DateTime.now().toIso8601String();
      _activities[key] = ExecutionActivity(
        activityId: 'act-$key',
        projectId: projectId,
        taskId: taskId,
        correlationId: correlationId,
        workerId: workerId,
        title: title,
        status: ActivityStatus.pending,
        startTime: now,
        currentAction: 'Initialized',
        isLive: true,
      );
    }
    return _activities[key]!;
  }

  void updateActivity(ExecutionActivity updated) {
    final key = updated.correlationId.isNotEmpty
        ? updated.correlationId
        : (updated.taskId.isNotEmpty ? updated.taskId : (updated.projectId.isNotEmpty ? '${updated.projectId}-main' : 'global-main'));
    _activities[key] = updated;
    _streamController.add(updated);
  }

  /// Project an incoming runtime event into the activity state.
  ExecutionActivity projectEvent(EventModel event) {
    final act = getOrCreateActivity(
      projectId: event.projectId ?? '',
      taskId: event.taskId ?? '',
      correlationId: event.correlationId ?? '',
      workerId: event.workerId ?? '',
    );

    final p = event.payload;
    final t = event.eventType;

    var currentAction = act.currentAction;
    var status = act.status;
    var isLive = act.isLive;
    var errorSummary = act.errorSummary;
    var waitingReason = act.waitingReason;
    final completedActions = List<String>.from(act.completedActions);
    final commands = List<CommandLineItem>.from(act.commands);
    final filesRead = List<String>.from(act.filesRead);
    final filesChanged = List<FileActivityItem>.from(act.filesChanged);
    final workers = List<WorkerActivityItem>.from(act.workers);
    final metrics = Map<String, dynamic>.from(act.metrics);
    var workerType = act.workerType;
    var title = act.title;

    if (event.workerId != null && event.workerId!.isNotEmpty) {
      if (event.workerId!.toLowerCase().contains('researcher')) {
        workerType = 'Researcher';
        title = 'Researcher — Running';
      } else if (event.workerId!.toLowerCase().contains('programmer')) {
        workerType = 'Programmer';
        title = 'Programmer — Active';
      } else if (event.workerId!.toLowerCase().contains('tester')) {
        workerType = 'Tester';
        title = 'Tester — Active';
      }
    }

    // --- Process by Event Type ---
    switch (t) {
      case 'TASK_STARTED':
        status = ActivityStatus.running;
        isLive = true;
        currentAction = p['objective']?.toString() ?? 'Starting task: ${p['title'] ?? event.taskId}';
        _recordWorker(workers, event.workerId ?? 'worker.specialist', status: 'RUNNING', action: currentAction);
        break;

      case 'TASK_COMPLETED':
        status = ActivityStatus.completed;
        isLive = false;
        final summary = p['summary']?.toString() ?? 'Task completed successfully';
        if (!completedActions.contains('✓ $summary')) {
          completedActions.add('✓ $summary');
        }
        currentAction = 'Execution completed';
        for (var i = 0; i < workers.length; i++) {
          if (workers[i].status == 'RUNNING') {
            workers[i] = WorkerActivityItem(
              workerId: workers[i].workerId,
              name: workers[i].name,
              role: workers[i].role,
              status: 'COMPLETED',
              currentAction: 'Finished',
            );
          }
        }
        break;

      case 'TASK_FAILED':
      case 'EXECUTION_FAILED':
        status = ActivityStatus.failed;
        isLive = false;
        errorSummary = p['reason']?.toString() ?? p['error']?.toString() ?? 'Execution failed';
        currentAction = 'Failed: $errorSummary';
        break;

      case 'TASK_BLOCKED':
      case 'MANAGER_WAITING':
        status = ActivityStatus.waiting;
        waitingReason = p['reason']?.toString() ?? 'Waiting for prerequisite specialists';
        currentAction = 'Waiting: $waitingReason';
        break;

      case 'MANAGER_STAGNATION_DETECTED':
        status = ActivityStatus.stalled;
        currentAction = 'Stalled — no forward progress detected';
        break;

      case 'RESEARCH_STARTED':
        workerType = 'Researcher';
        title = 'Researcher — Running';
        status = ActivityStatus.running;
        currentAction = 'Inspecting project structure & domain context';
        _recordWorker(workers, 'worker.researcher', name: 'Researcher', role: 'Specialist', status: 'RUNNING');
        break;

      case 'RESEARCH_PLAN_CREATED':
        final qCount = p['questions_count'] ?? (p['questions'] as List?)?.length ?? 6;
        final step = '✓ Identified $qCount research questions';
        if (!completedActions.contains(step)) completedActions.add(step);
        currentAction = 'Allocating search and doc crawlers';
        break;

      case 'RESEARCH_SEARCH_PERFORMED':
        final query = p['query']?.toString() ?? '';
        final crawlerId = event.workerId ?? p['crawler_id']?.toString() ?? 'crawler.search';
        final count = (p['result_count'] as num?)?.toInt() ?? 0;
        metrics['sources_collected'] = ((metrics['sources_collected'] as num?)?.toInt() ?? 0) + count;
        currentAction = 'Web search: "${query.length > 35 ? query.substring(0, 35) + '...' : query}"';
        _recordWorker(
          workers,
          crawlerId,
          name: 'Crawler #${workers.where((w) => w.workerId.contains('crawler')).length + 1}',
          role: 'Web Search',
          status: 'RUNNING',
          action: 'Searching: "$query"',
        );
        break;

      case 'RESEARCH_SOURCE_FETCHED':
        metrics['sources_collected'] = ((metrics['sources_collected'] as num?)?.toInt() ?? 0) + 1;
        currentAction = 'Evaluating research sources';
        break;

      case 'RESEARCH_FINDING_CREATED':
        metrics['evidence_items'] = ((metrics['evidence_items'] as num?)?.toInt() ?? 0) + 1;
        final answered = metrics['evidence_items'] ?? 1;
        metrics['questions_answered'] = '$answered/6';
        currentAction = 'Evaluating evidence & cross-checking contradictions';
        break;

      case 'RESEARCH_COMPLETED':
        status = ActivityStatus.completed;
        isLive = false;
        final src = metrics['sources_collected'] ?? 0;
        final ev = metrics['evidence_items'] ?? 0;
        final done = '✓ Research completed ($src sources, $ev evidence items)';
        if (!completedActions.contains(done)) completedActions.add(done);
        currentAction = 'Research complete';
        break;

      case 'PROGRAMMER_STARTED':
        workerType = 'Programmer';
        title = 'Programmer — Active';
        status = ActivityStatus.running;
        currentAction = 'Analyzing AST and target code files';
        _recordWorker(workers, 'worker.programmer', name: 'Programmer', role: 'Specialist', status: 'RUNNING');
        break;

      case 'PROGRAMMER_CODE_MODIFIED':
        final filePath = p['file_path']?.toString() ?? '';
        if (filePath.isNotEmpty) {
          _recordFileChanged(filesChanged, filePath, FileOperationType.modified);
          final step = '✓ Modified $filePath';
          if (!completedActions.contains(step)) completedActions.add(step);
        }
        currentAction = 'Applying code modifications';
        break;

      case 'TOOL_REQUESTED':
      case 'TOOL_STARTED':
        final toolId = p['tool_id']?.toString() ?? '';
        final args = (p['arguments'] as Map<String, dynamic>?) ?? {};

        if (toolId.contains('shell') || toolId.contains('run_command')) {
          final rawCmd = args['command']?.toString() ?? args['cmd']?.toString() ?? toolId;
          final clean = redactCommand(rawCmd);
          currentAction = 'Running: \$ ${clean.length > 35 ? clean.substring(0, 35) + '...' : clean}';
          _recordCommand(commands, clean, isRunning: true);
        } else if (toolId.contains('read_file') || toolId.contains('stat_file')) {
          final path = args['path']?.toString() ?? args['file_path']?.toString() ?? '';
          if (path.isNotEmpty && !filesRead.contains(path)) {
            filesRead.add(path);
          }
          currentAction = 'Reading: $path';
        } else if (toolId.contains('write_file') || toolId.contains('create_file')) {
          final path = args['path']?.toString() ?? args['file_path']?.toString() ?? '';
          if (path.isNotEmpty) {
            _recordFileChanged(filesChanged, path, FileOperationType.created);
          }
          currentAction = 'Writing: $path';
        }
        break;

      case 'TOOL_COMPLETED':
        final toolId = p['tool_id']?.toString() ?? '';
        if (toolId.contains('shell') || toolId.contains('run_command')) {
          _completeLastCommand(commands, exitCode: (p['exit_code'] as num?)?.toInt() ?? 0);
        }
        break;
    }

    final updated = ExecutionActivity(
      activityId: act.activityId,
      projectId: act.projectId,
      taskId: act.taskId,
      correlationId: act.correlationId,
      workerId: act.workerId,
      workerType: workerType,
      title: title,
      description: act.description,
      status: status,
      startTime: act.startTime,
      endTime: isLive ? null : DateTime.now().toIso8601String(),
      currentAction: currentAction,
      completedActions: completedActions,
      commands: commands,
      filesRead: filesRead,
      filesChanged: filesChanged,
      workers: workers,
      metrics: metrics,
      errorSummary: errorSummary,
      waitingReason: waitingReason,
      isLive: isLive,
    );

    updateActivity(updated);
    return updated;
  }

  void _recordCommand(List<CommandLineItem> commands, String cmd, {bool isRunning = true}) {
    for (final c in commands) {
      if (c.command == cmd && c.isRunning) return;
    }
    commands.add(CommandLineItem(
      command: cmd,
      isRunning: isRunning,
      timestamp: DateTime.now().toIso8601String(),
    ));
  }

  void _completeLastCommand(List<CommandLineItem> commands, {int exitCode = 0}) {
    for (var i = commands.length - 1; i >= 0; i--) {
      if (commands[i].isRunning) {
        commands[i] = CommandLineItem(
          command: commands[i].command,
          exitCode: exitCode,
          durationMs: commands[i].durationMs,
          isRunning: false,
          outputPreview: commands[i].outputPreview,
          timestamp: commands[i].timestamp,
        );
        break;
      }
    }
  }

  void _recordFileChanged(List<FileActivityItem> list, String path, FileOperationType op) {
    final idx = list.indexWhere((f) => f.path == path);
    if (idx != -1) {
      list[idx] = FileActivityItem(path: path, operation: op, timestamp: DateTime.now().toIso8601String());
    } else {
      list.add(FileActivityItem(path: path, operation: op, timestamp: DateTime.now().toIso8601String()));
    }
  }

  void _recordWorker(
    List<WorkerActivityItem> list,
    String workerId, {
    String name = 'Specialist',
    String role = 'Specialist',
    String status = 'RUNNING',
    String action = 'Working',
  }) {
    final idx = list.indexWhere((w) => w.workerId == workerId);
    if (idx != -1) {
      list[idx] = WorkerActivityItem(
        workerId: workerId,
        name: list[idx].name,
        role: list[idx].role,
        status: status,
        currentAction: action,
        startedAt: list[idx].startedAt,
      );
    } else {
      list.add(WorkerActivityItem(
        workerId: workerId,
        name: name,
        role: role,
        status: status,
        currentAction: action,
        startedAt: DateTime.now().toIso8601String(),
      ));
    }
  }
}
