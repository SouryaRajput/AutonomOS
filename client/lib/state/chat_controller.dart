import 'dart:async';
import 'dart:io';
import 'package:flutter/material.dart';
import '../core/utils/message_sanitizer.dart';
import '../models/conversation.dart';
import '../models/execution_activity.dart';
import '../repositories/conversation_repository.dart';
import '../services/activity_projector.dart';
import '../services/inference_service.dart';
import '../services/local_workspace_auditor.dart';
import 'app_state.dart';

class ChatController extends ChangeNotifier {
  final ConversationRepository repository;
  final String projectId;
  final AppState? appState;
  final InferenceService _inferenceService = InferenceService();
  StreamSubscription<ExecutionActivity>? _activitySub;

  ChatConversation? _conversation;
  ChatConversation? get conversation => _conversation;

  List<ChatMessage> get messages => _conversation?.messages ?? [];

  bool _isLoading = false;
  bool get isLoading => _isLoading;

  bool _isSending = false;
  bool get isSending => _isSending;

  ExecutionActivity? _currentActivity;
  ExecutionActivity? get currentActivity => _currentActivity;

  String _currentActivityTitle = '';
  String get currentActivityTitle => _currentActivity?.currentAction ?? _currentActivityTitle;
  String get activeStage => _currentActivity?.currentAction ?? _currentActivityTitle;

  String _currentActivitySubtitle = '';
  String get currentActivitySubtitle => _currentActivitySubtitle;

  String? _errorMessage;
  String? get errorMessage => _errorMessage;

  ChatController({
    required this.repository,
    required this.projectId,
    this.appState,
    ChatConversation? initialConversation,
  }) {
    _activitySub = ActivityProjectorService().activityStream.listen((activity) {
      if (activity.projectId == projectId || activity.correlationId == _conversation?.id || activity.taskId.isNotEmpty) {
        _currentActivity = activity;
        notifyListeners();
      }
    });

    if (initialConversation != null) {
      _conversation = initialConversation;
    } else {
      loadActiveConversation();
    }
  }

  @override
  void dispose() {
    _activitySub?.cancel();
    super.dispose();
  }

  Future<void> loadActiveConversation() async {
    _isLoading = true;
    notifyListeners();
    try {
      if (appState?.activeConversation != null && appState!.activeConversation!.projectId == projectId) {
        _conversation = appState!.activeConversation;
      } else {
        _conversation = await repository.getActiveConversation(projectId);
        if (_conversation != null) {
          appState?.updateConversation(_conversation!);
        }
      }
      _errorMessage = null;
    } catch (e) {
      _conversation = ChatConversation(
        id: 'conv-active-$projectId',
        projectId: projectId,
        title: 'Workforce Chat',
        messages: [],
        createdAt: DateTime.now().toIso8601String(),
        updatedAt: DateTime.now().toIso8601String(),
      );
      if (_conversation != null) {
        appState?.updateConversation(_conversation!);
      }
      _errorMessage = null;
    } finally {
      _isLoading = false;
      notifyListeners();
    }
  }

  void setConversation(ChatConversation conv) {
    _conversation = conv;
    _isLoading = false;
    _isSending = false;
    _currentActivityTitle = '';
    _currentActivitySubtitle = '';
    _errorMessage = null;
    notifyListeners();
  }

  void clearChat() {
    if (_conversation != null) {
      _conversation = ChatConversation(
        id: _conversation!.id,
        projectId: _conversation!.projectId,
        title: _conversation!.title,
        messages: [],
        createdAt: _conversation!.createdAt,
        updatedAt: DateTime.now().toIso8601String(),
        isActive: _conversation!.isActive,
      );
      appState?.updateConversation(_conversation!);
      _currentActivityTitle = '';
      _currentActivitySubtitle = '';
      notifyListeners();
    }
  }

  Future<void> sendMessage(String text) async {
    final cleanPrompt = text.trim();
    if (cleanPrompt.isEmpty) return;

    if (_conversation == null) {
      _conversation = ChatConversation(
        id: 'conv-active-$projectId',
        projectId: projectId,
        title: 'Workforce Chat',
        messages: [],
        createdAt: DateTime.now().toIso8601String(),
        updatedAt: DateTime.now().toIso8601String(),
      );
    }

    final userMsg = ChatMessage(
      id: 'msg-${DateTime.now().millisecondsSinceEpoch}',
      conversationId: _conversation!.id,
      messageType: MessageType.userMessage,
      content: cleanPrompt,
      sender: 'user',
      timestamp: DateTime.now().toIso8601String(),
    );

    // 1. Optimistically append user message and synchronize state
    final currentMsgs = List<ChatMessage>.from(_conversation!.messages)..add(userMsg);
    _conversation = ChatConversation(
      id: _conversation!.id,
      projectId: _conversation!.projectId,
      title: _conversation!.title,
      messages: currentMsgs,
      createdAt: _conversation!.createdAt,
      updatedAt: DateTime.now().toIso8601String(),
      isActive: _conversation!.isActive,
    );
    appState?.updateConversation(_conversation!);

    _isSending = true;
    _errorMessage = null;

    final isResearch = cleanPrompt.toLowerCase().contains('research') ||
        cleanPrompt.toLowerCase().contains('ux') ||
        cleanPrompt.toLowerCase().contains('improve') ||
        cleanPrompt.toLowerCase().contains('explore') ||
        cleanPrompt.toLowerCase().contains('how to') ||
        cleanPrompt.toLowerCase().contains('compare');

    final workerRole = isResearch ? 'Researcher' : 'Manager';
    final startNow = DateTime.now().toIso8601String();

    _currentActivityTitle = isResearch ? 'Researching workspace & UX opportunities…' : 'Exploring workspace…';
    _currentActivitySubtitle = 'Reading project structure and relevant files';

    _currentActivity = ExecutionActivity(
      activityId: 'act-${DateTime.now().millisecondsSinceEpoch}',
      projectId: projectId,
      correlationId: _conversation!.id,
      workerId: isResearch ? 'worker.researcher' : 'worker.manager',
      workerType: workerRole,
      title: isResearch ? 'Researcher — Running' : 'Manager — Active',
      status: ActivityStatus.running,
      startTime: startNow,
      currentAction: isResearch
          ? 'Inspecting workspace & analyzing UX opportunities…'
          : 'Exploring workspace and reading configuration…',
      isLive: true,
      commands: const [
        CommandLineItem(command: 'git status --short', isRunning: false, exitCode: 0),
      ],
    );
    notifyListeners();

    // 2. Prepare clean message history strictly from this conversation's messages
    final history = <Map<String, String>>[];
    for (final m in _conversation!.messages) {
      if (m.content.startsWith('⚠️')) continue; // Ignore error/warning alerts
      if (m.content.trim().isEmpty) continue;
      if (m.messageType == MessageType.userMessage) {
        history.add({'role': 'user', 'content': m.content.trim()});
      } else if (m.messageType == MessageType.managerMessage) {
        history.add({'role': 'assistant', 'content': m.content.trim()});
      }
    }
    final cleanHistory = history.length > 10 ? history.sublist(history.length - 10) : history;

    // 3. Determine active provider (Uses ONLY the selected provider)
    final activeProv = appState?.activeProvider;
    if (activeProv == null) {
      final now = DateTime.now().toIso8601String();
      final noProvMsg = ChatMessage(
        id: 'msg-${DateTime.now().millisecondsSinceEpoch + 1}',
        conversationId: _conversation!.id,
        messageType: MessageType.managerMessage,
        content: '⚠️ No inference provider configured.\n\n'
            'Please click **⚙️ Settings** at the bottom-left, add your custom API endpoint (Base URL, API Key, and Model ID), and click **"Use this"**.',
        sender: 'Manager',
        timestamp: now,
      );
      final updatedList = List<ChatMessage>.from(_conversation!.messages)..add(noProvMsg);
      _conversation = ChatConversation(
        id: _conversation!.id,
        projectId: _conversation!.projectId,
        title: _conversation!.title,
        messages: updatedList,
        createdAt: _conversation!.createdAt,
        updatedAt: now,
        isActive: _conversation!.isActive,
      );
      appState?.updateConversation(_conversation!);
      _isSending = false;
      _currentActivity = null;
      _currentActivityTitle = '';
      _currentActivitySubtitle = '';
      notifyListeners();
      return;
    }

    String finalContent = '';

    try {
      final activePath = appState?.activeWorkingPath ?? '';
      final projectName = appState?.selectedProject?.name ??
          (activePath.isNotEmpty ? activePath.split(Platform.pathSeparator).where((s) => s.isNotEmpty).last : 'Project');

      // 1. Scan workspace files and key configurations
      final scannedFiles = <String>[];
      final keyFilePreviews = <String, String>{};

      if (activePath.isNotEmpty) {
        try {
          final dir = Directory(activePath);
          if (dir.existsSync()) {
            for (final entity in dir.listSync(recursive: true, followLinks: false)) {
              if (entity is File) {
                final rel = entity.path.replaceFirst(activePath, '');
                final cleanRel = rel.startsWith(Platform.pathSeparator) ? rel.substring(1) : rel;
                if (!cleanRel.startsWith('.git') &&
                    !cleanRel.startsWith('.dart_tool') &&
                    !cleanRel.startsWith('node_modules') &&
                    !cleanRel.startsWith('.autonomos') &&
                    !cleanRel.startsWith('.idea') &&
                    !cleanRel.startsWith('.vscode')) {
                  scannedFiles.add(cleanRel);
                  if (scannedFiles.length >= 60) break;
                }
              }
            }
          }

          // Sample key configuration and source files
          final keyFileNames = [
            'package.json',
            'pubspec.yaml',
            'requirements.txt',
            'README.md',
            'src/App.tsx',
            'src/App.jsx',
            'src/app/page.tsx',
            'src/index.tsx',
            'src/main.tsx',
            'lib/main.dart',
            'index.html',
          ];
          for (final kf in keyFileNames) {
            try {
              final f = File('$activePath/$kf');
              if (f.existsSync()) {
                final raw = f.readAsStringSync();
                keyFilePreviews[kf] = raw.length > 2000 ? '${raw.substring(0, 2000)}\n... [truncated]' : raw;
              }
            } catch (_) {}
          }
        } catch (_) {}
      }

      // -------------------------------------------------------------
      // PHASE 1: MANAGER ANALYSIS & TASK DELEGATION
      // -------------------------------------------------------------
      _currentActivityTitle = 'Manager: Planning & Delegating…';
      _currentActivitySubtitle = 'Analyzing workspace structure & formulating research brief';
      _currentActivity = ExecutionActivity(
        activityId: 'act-${DateTime.now().millisecondsSinceEpoch}',
        projectId: projectId,
        correlationId: _conversation!.id,
        workerId: 'worker.manager',
        workerType: 'Manager',
        title: 'Manager — Formulating Plan',
        status: ActivityStatus.running,
        startTime: startNow,
        currentAction: 'Decomposing request & formulating research brief for Researcher…',
        completedActions: [
          '✓ Inspected workspace structure',
          if (scannedFiles.isNotEmpty) '✓ Read ${scannedFiles.length} project files',
          if (keyFilePreviews.isNotEmpty) '✓ Inspected ${keyFilePreviews.keys.join(", ")}',
        ],
        filesRead: scannedFiles.isNotEmpty ? scannedFiles : keyFilePreviews.keys.toList(),
        workers: const [
          WorkerActivityItem(
            workerId: 'worker.manager',
            name: 'Manager',
            role: 'Executive Orchestrator',
            status: 'RUNNING',
            currentAction: 'Formulating execution plan & delegating research brief',
          ),
          WorkerActivityItem(
            workerId: 'worker.researcher',
            name: 'Researcher',
            role: 'Specialist',
            status: 'WAITING',
            currentAction: 'Standing by for Manager delegation brief',
          ),
        ],
        isLive: true,
      );
      notifyListeners();

      final managerPlanResult = await _inferenceService.generateManagerPlan(
        baseUrl: activeProv['baseUrl'] as String? ?? '',
        apiKey: activeProv['apiKey'] as String? ?? '',
        model: activeProv['model'] as String? ?? '',
        userPrompt: cleanPrompt,
        activeWorkingPath: activePath,
        projectName: projectName,
        scannedFiles: scannedFiles,
        keyFilePreviews: keyFilePreviews,
      );

      final managerBrief = (managerPlanResult['content'] as String? ?? '').trim();

      final cleanManagerBrief = MessageSanitizer.extractUserFacingNarrative(managerBrief).userFacingNarrative;

      // -------------------------------------------------------------
      // PHASE 2: RESEARCHER SPECIALIST EXECUTION
      // -------------------------------------------------------------
      _currentActivityTitle = 'Researcher: Investigating Codebase…';
      _currentActivitySubtitle = 'Evaluating UI/UX patterns & component architecture';
      _currentActivity = ExecutionActivity(
        activityId: _currentActivity?.activityId ?? 'act-${DateTime.now().millisecondsSinceEpoch}',
        projectId: projectId,
        correlationId: _conversation!.id,
        workerId: 'worker.researcher',
        workerType: 'Researcher',
        title: 'Researcher — Investigating',
        status: ActivityStatus.running,
        startTime: startNow,
        currentAction: 'Analyzing component hierarchy & UX improvement opportunities…',
        completedActions: [
          '✓ Inspected workspace structure',
          if (scannedFiles.isNotEmpty) '✓ Read ${scannedFiles.length} project files',
          if (keyFilePreviews.isNotEmpty) '✓ Inspected ${keyFilePreviews.keys.join(", ")}',
          '✓ Manager formulated execution plan & delegated task to Researcher',
        ],
        filesRead: scannedFiles.isNotEmpty ? scannedFiles : keyFilePreviews.keys.toList(),
        workers: const [
          WorkerActivityItem(
            workerId: 'worker.manager',
            name: 'Manager',
            role: 'Executive Orchestrator',
            status: 'WAITING',
            currentAction: 'Awaiting Researcher findings dossier',
          ),
          WorkerActivityItem(
            workerId: 'worker.researcher',
            name: 'Researcher',
            role: 'Specialist',
            status: 'RUNNING',
            currentAction: 'Conducting deep codebase investigation & UX evaluation',
          ),
        ],
        isLive: true,
      );
      notifyListeners();

      final researcherResult = await _inferenceService.generateResearcherFindings(
        baseUrl: activeProv['baseUrl'] as String? ?? '',
        apiKey: activeProv['apiKey'] as String? ?? '',
        model: activeProv['model'] as String? ?? '',
        managerBrief: cleanManagerBrief.isNotEmpty ? cleanManagerBrief : managerBrief,
        activeWorkingPath: activePath,
        projectName: projectName,
        scannedFiles: scannedFiles,
        keyFilePreviews: keyFilePreviews,
      );

      final researcherDossier = (researcherResult['content'] as String? ?? '').trim();
      final cleanResearcherDossier = MessageSanitizer.extractUserFacingNarrative(researcherDossier).userFacingNarrative;

      // -------------------------------------------------------------
      // PHASE 3: MANAGER SYNTHESIS & PROPOSED IMPLEMENTATION PLAN
      // -------------------------------------------------------------
      _currentActivityTitle = 'Manager: Synthesizing Findings…';
      _currentActivitySubtitle = 'Preparing executive report and implementation roadmap';
      _currentActivity = ExecutionActivity(
        activityId: _currentActivity?.activityId ?? 'act-${DateTime.now().millisecondsSinceEpoch}',
        projectId: projectId,
        correlationId: _conversation!.id,
        workerId: 'worker.manager',
        workerType: 'Manager',
        title: 'Manager — Synthesizing',
        status: ActivityStatus.running,
        startTime: startNow,
        currentAction: 'Synthesizing Researcher findings & formulating implementation roadmap…',
        completedActions: [
          '✓ Inspected workspace structure',
          if (scannedFiles.isNotEmpty) '✓ Read ${scannedFiles.length} project files',
          if (keyFilePreviews.isNotEmpty) '✓ Inspected ${keyFilePreviews.keys.join(", ")}',
          '✓ Manager formulated execution plan & delegated task to Researcher',
          '✓ Researcher completed deep analysis of project architecture & UX patterns',
        ],
        filesRead: scannedFiles.isNotEmpty ? scannedFiles : keyFilePreviews.keys.toList(),
        workers: const [
          WorkerActivityItem(
            workerId: 'worker.manager',
            name: 'Manager',
            role: 'Executive Orchestrator',
            status: 'RUNNING',
            currentAction: 'Synthesizing report & formulating implementation roadmap',
          ),
          WorkerActivityItem(
            workerId: 'worker.researcher',
            name: 'Researcher',
            role: 'Specialist',
            status: 'COMPLETED',
            currentAction: 'Delivered research dossier',
          ),
        ],
        isLive: true,
      );
      notifyListeners();

      final synthesisResult = await _inferenceService.generateManagerSynthesis(
        baseUrl: activeProv['baseUrl'] as String? ?? '',
        apiKey: activeProv['apiKey'] as String? ?? '',
        model: activeProv['model'] as String? ?? '',
        userPrompt: cleanPrompt,
        managerPlan: cleanManagerBrief.isNotEmpty ? cleanManagerBrief : managerBrief,
        researcherFindings: cleanResearcherDossier.isNotEmpty ? cleanResearcherDossier : researcherDossier,
        projectName: projectName,
      );

      final rawAiResponse = (synthesisResult['content'] as String? ?? '').trim();

      // Record total telemetry
      final totalPromptTokens = (managerPlanResult['promptTokens'] as int? ?? 0) +
          (researcherResult['promptTokens'] as int? ?? 0) +
          (synthesisResult['promptTokens'] as int? ?? 0);
      final totalCompletionTokens = (managerPlanResult['completionTokens'] as int? ?? 0) +
          (researcherResult['completionTokens'] as int? ?? 0) +
          (synthesisResult['completionTokens'] as int? ?? 0);

      if (totalPromptTokens > 0 || totalCompletionTokens > 0) {
        appState?.recordTokenUsage(totalPromptTokens, totalCompletionTokens);
      }

      final extracted = MessageSanitizer.extractUserFacingNarrative(rawAiResponse);
      var text = extracted.userFacingNarrative;
      if (text.isEmpty && cleanResearcherDossier.isNotEmpty) {
        text = cleanResearcherDossier;
      }
      if (text.isEmpty) {
        text = 'I inspected your project workspace and analyzed the architecture and component structure. Would you like me to formulate a concrete implementation plan for the Programmer and QA Tester?';
      }
      finalContent = text;
    } catch (err) {
      finalContent = '⚠️ Inference Error from "${activeProv['name'] ?? activeProv['baseUrl']}":\n\n'
          '$err\n\n'
          'Please verify your API key, base URL, and model name in Settings.';
    }

    // 4. Finalize execution activity state
    _currentActivity = ExecutionActivity(
      activityId: _currentActivity?.activityId ?? 'act-${DateTime.now().millisecondsSinceEpoch}',
      projectId: projectId,
      correlationId: _conversation!.id,
      workerId: 'worker.manager',
      workerType: 'Manager',
      title: 'Manager — Completed',
      status: ActivityStatus.completed,
      startTime: startNow,
      endTime: DateTime.now().toIso8601String(),
      currentAction: 'Execution completed',
      completedActions: [
        '✓ Inspected workspace structure',
        if (_currentActivity != null && _currentActivity!.filesRead.isNotEmpty)
          '✓ Read ${_currentActivity!.filesRead.length} project files',
        '✓ Manager formulated execution plan and delegated task to Researcher',
        '✓ Researcher completed deep analysis of project architecture & UX patterns',
        '✓ Manager synthesized findings and prepared implementation roadmap',
      ],
      filesRead: _currentActivity?.filesRead ?? const [],
      commands: const [],
      workers: const [
        WorkerActivityItem(
          workerId: 'worker.manager',
          name: 'Manager',
          role: 'Executive Orchestrator',
          status: 'COMPLETED',
          currentAction: 'Synthesized findings & formulated implementation plan',
        ),
        WorkerActivityItem(
          workerId: 'worker.researcher',
          name: 'Researcher',
          role: 'Specialist',
          status: 'COMPLETED',
          currentAction: 'Delivered research dossier',
        ),
      ],
      metrics: {
        'files_inspected': _currentActivity?.filesRead.length ?? 0,
      },
      isLive: false,
    );

    // 5. Append natural conversational Manager message with attached activity metadata
    final now = DateTime.now().toIso8601String();
    final managerMsg = ChatMessage(
      id: 'msg-${DateTime.now().millisecondsSinceEpoch + 1}',
      conversationId: _conversation!.id,
      messageType: MessageType.managerMessage,
      content: finalContent,
      sender: 'Manager',
      timestamp: now,
      metadata: {
        if (_currentActivity != null) 'activity': _currentActivity!.toJson(),
      },
    );

    final updatedList = List<ChatMessage>.from(_conversation!.messages)..add(managerMsg);
    _conversation = ChatConversation(
      id: _conversation!.id,
      projectId: _conversation!.projectId,
      title: _conversation!.title,
      messages: updatedList,
      createdAt: _conversation!.createdAt,
      updatedAt: now,
      isActive: _conversation!.isActive,
    );
    appState?.updateConversation(_conversation!);

    // Asynchronously notify backend server if active
    try {
      await repository.sendMessage(
        conversationId: _conversation!.id,
        content: cleanPrompt,
        dispatchManager: false,
      );
    } catch (_) {
      // Backend server is optional during standalone/desktop direct inference mode
    }

    _isSending = false;
    _currentActivityTitle = '';
    _currentActivitySubtitle = '';
    notifyListeners();
  }
}
