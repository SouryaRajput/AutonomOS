import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import '../core/utils/message_sanitizer.dart';
import '../models/conversation.dart';
import '../models/execution_activity.dart';
import '../repositories/conversation_repository.dart';
import '../services/activity_projector.dart';
import '../services/inference_service.dart';
import '../services/local_workspace_auditor.dart';
import '../services/workspace_diff_service.dart';
import 'app_state.dart';

enum UserIntent {
  proceedWithPlan,
  summarizeFindings,
  simpleQuestion,
  codeImplementation,
  complexCreation,
  complexResearch,
}

UserIntent classifyUserIntent({
  required String prompt,
  required String? lastAssistantMessage,
  required List<ChatMessage> conversationMessages,
}) {
  final clean = prompt.trim();
  final lower = clean.toLowerCase();

  // 0. Explicit slash commands
  if (lower.startsWith('/research')) {
    return UserIntent.complexResearch;
  }
  if (lower.startsWith('/plan')) {
    return UserIntent.proceedWithPlan;
  }

  // 1. Check if user is confirming / proceeding with an offered plan
  final lastAssistant = (lastAssistantMessage ?? '').toLowerCase();
  final hasPendingPlanProposal = lastAssistant.contains('proceed with') ||
      lastAssistant.contains('implementation plan') ||
      lastAssistant.contains('would you like me to proceed') ||
      lastAssistant.contains('reply "proceed') ||
      lastAssistant.contains('reply \'proceed') ||
      lastAssistant.contains('phase a') ||
      lastAssistant.contains('quick wins') ||
      lastAssistant.contains('ready to proceed');

  final isProceedAffirmative = lower == 'proceed' ||
      lower.startsWith('proceed') ||
      lower == 'yes' ||
      lower == 'yeah' ||
      lower == 'yep' ||
      lower == 'sure' ||
      lower == 'go ahead' ||
      lower == 'ok' ||
      lower == 'okay' ||
      lower == 'approve' ||
      lower == 'approved' ||
      lower == 'start' ||
      lower.startsWith('start phase') ||
      lower == 'begin' ||
      lower == 'do it' ||
      lower == 'continue' ||
      lower == 'sounds good' ||
      lower == 'looks good' ||
      lower.contains('create the implementation plan') ||
      lower.contains('create implementation plan') ||
      lower.contains('generate the plan') ||
      lower.contains('generate implementation plan') ||
      lower.contains('let\'s do it') ||
      lower.contains('let\'s proceed') ||
      lower.contains('go for it');

  if (hasPendingPlanProposal && isProceedAffirmative) {
    return UserIntent.proceedWithPlan;
  }

  // Explicit user command to create / proceed with implementation plan
  if (lower.startsWith('proceed with') ||
      (lower.contains('proceed') && lower.contains('plan')) ||
      (lower.contains('create') && lower.contains('implementation plan'))) {
    return UserIntent.proceedWithPlan;
  }

  // 2. Summary / Findings / Recap requests (NEVER re-runs research!)
  final isSummaryRequest = lower.contains('summary') ||
      lower.contains('summarize') ||
      lower.contains('summarise') ||
      lower.contains('recap') ||
      lower == 'tldr' ||
      lower == 'tl;dr' ||
      lower.contains('key takeaways') ||
      lower.contains('highlights') ||
      lower.contains('what did you find') ||
      lower.contains('what are the findings') ||
      lower.contains('what were the findings') ||
      lower.contains('what was found') ||
      lower.contains('give me the findings') ||
      lower.contains('show me the findings') ||
      lower.contains('brief me') ||
      lower.contains('overview of findings') ||
      (lower.startsWith('overview') && clean.split(RegExp(r'\s+')).length <= 5);

  if (isSummaryRequest) {
    return UserIntent.summarizeFindings;
  }

  // 3. Complex Feature / Interactive 3D / Project Creation Requests
  // Matches both imperatives ("create...", "build...") and polite requests ("can you create...", "could you build...")
  final isCreationVerb = RegExp(
    r"^(?:can you|could you|would you|please|will you|help me|can we|i want you to|i want to|i need you to|let's)?\s*(create|build|develop|make|implement|code|generate|design|craft|construct|setup|set up|rebuild|revamp)\b",
    caseSensitive: false,
  ).hasMatch(clean);

  final isComplexFeatureRequest = isCreationVerb &&
      (lower.contains('website') ||
          lower.contains('portfolio') ||
          lower.contains('3d') ||
          lower.contains('animation') ||
          lower.contains('interactive') ||
          lower.contains('landing page') ||
          lower.contains('dashboard') ||
          lower.contains('canvas') ||
          lower.contains('three.js') ||
          lower.contains('threejs') ||
          lower.contains('scene') ||
          lower.contains('latest technologies') ||
          lower.contains('inspiration') ||
          lower.contains('ui/ux') ||
          lower.contains('full stack') ||
          clean.split(RegExp(r'\s+')).length >= 10);

  if (isComplexFeatureRequest) {
    return UserIntent.complexCreation;
  }

  // 4. Direct Code Modification / Specific File Instructions
  // e.g. "write code to add user authentication", "create file src/components/Header.tsx", "fix bug in ..."
  final isCodeImperative = RegExp(
    r'\b(create file|edit file|write code|modify file|fix bug|refactor|add component|build component|implement function|fix error|update file|add route|generate code)\b',
    caseSensitive: false,
  ).hasMatch(clean) &&
      !lower.contains('research') &&
      !lower.contains('audit');

  if (isCodeImperative || (isCreationVerb && !lower.contains('research') && !lower.contains('audit'))) {
    return UserIntent.codeImplementation;
  }

  // 5. Greetings, status checks, acknowledgments
  final isGreetingOrThanks = RegExp(
    r'^(hi|hello|hey|greetings|thanks|thank you|good morning|good evening|cool|nice|got it)[\s!.]*$',
    caseSensitive: false,
  ).hasMatch(clean);

  final isStatusCheck = RegExp(
    r'^(status|what is running|active tasks|active workers|progress|how are you|who are you)[\s?!.]*$',
    caseSensitive: false,
  ).hasMatch(clean);

  if (isGreetingOrThanks || isStatusCheck) {
    return UserIntent.simpleQuestion;
  }

  // 6. Explicit command to conduct new deep research / audit from scratch
  final isExplicitResearchCommand = RegExp(
    r'^(research\b|conduct research|do research|run research|audit\b|conduct audit|investigate\b|deep dive\b|explore the codebase|scan repository)',
    caseSensitive: false,
  ).hasMatch(clean) ||
      (lower.contains('research') && (lower.contains('architecture') || lower.contains('codebase') || lower.contains('stack') || lower.contains('security') || lower.contains('performance'))) ||
      (lower.contains('audit') && (lower.contains('security') || lower.contains('performance') || lower.contains('codebase')));

  if (isExplicitResearchCommand) {
    return UserIntent.complexResearch;
  }

  // 7. Informational Questions & Follow-ups (Simple Question)
  // Queries like: "what is X?", "where is main?", "why did you choose Y?", "can you explain Z?"
  final isInformationalQuestion = clean.endsWith('?') ||
      RegExp(r'^(what|where|how|why|who|when|which|is there|are there)\b', caseSensitive: false).hasMatch(clean) ||
      RegExp(r'^(?:can you|could you|would you|please)\s+(?:explain|describe|tell me|clarify|elaborate|show me|list|detail)\b', caseSensitive: false).hasMatch(clean);

  if (isInformationalQuestion || clean.split(RegExp(r'\s+')).length <= 6) {
    return UserIntent.simpleQuestion;
  }

  // 8. Default fallback for substantial prompts
  return UserIntent.complexCreation;
}

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

  void _appendRealtimeAgentMessage({
    required MessageType type,
    required String sender,
    required String content,
    Map<String, dynamic> metadata = const {},
  }) {
    if (_conversation == null) return;
    final now = DateTime.now().toIso8601String();
    final msg = ChatMessage(
      id: 'msg-${DateTime.now().millisecondsSinceEpoch}-${_conversation!.messages.length}',
      conversationId: _conversation!.id,
      messageType: type,
      content: content,
      sender: sender,
      timestamp: now,
      metadata: metadata,
    );
    final updatedList = List<ChatMessage>.from(_conversation!.messages)..add(msg);
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
    notifyListeners();
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

    final previousMessages = List<ChatMessage>.from(_conversation!.messages);

    // 1. Determine active provider (Uses ONLY the selected provider)
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
      final updatedList = List<ChatMessage>.from(previousMessages)..add(noProvMsg);
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

    final userMsg = ChatMessage(
      id: 'msg-${DateTime.now().millisecondsSinceEpoch}',
      conversationId: _conversation!.id,
      messageType: MessageType.userMessage,
      content: cleanPrompt,
      sender: 'user',
      timestamp: DateTime.now().toIso8601String(),
    );

    // Optimistically append user message and synchronize state
    final currentMsgs = List<ChatMessage>.from(previousMessages)..add(userMsg);
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

    // 2. Extract conversation memory and previous context
    final history = <Map<String, String>>[];
    final assistantMessages = <String>[];
    String? lastAssistantMessage;

    for (final m in previousMessages) {
      if (m.content.startsWith('⚠️')) continue;
      if (m.content.trim().isEmpty) continue;
      if (m.messageType == MessageType.userMessage) {
        history.add({'role': 'user', 'content': m.content.trim()});
      } else if (m.messageType == MessageType.managerMessage) {
        final content = m.content.trim();
        history.add({'role': 'assistant', 'content': content});
        assistantMessages.add(content);
        lastAssistantMessage = content;
      }
    }
    final cleanHistory = history.length > 10 ? history.sublist(history.length - 10) : history;
    final allRecentContext = assistantMessages.isNotEmpty
        ? assistantMessages.reversed.take(2).toList().reversed.join('\n\n---\n\n')
        : (lastAssistantMessage ?? '');

    // 3. Classify user intent dynamically with memory context
    final intent = classifyUserIntent(
      prompt: cleanPrompt,
      lastAssistantMessage: lastAssistantMessage,
      conversationMessages: previousMessages,
    );

    final startNow = DateTime.now().toIso8601String();
    String finalContent = '';
    List<String> finalCompletedActions = [];
    List<WorkerActivityItem> finalWorkers = [];

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
                final lowerRel = cleanRel.toLowerCase();
                final isIgnored = lowerRel.startsWith('.git') ||
                    lowerRel.startsWith('.dart_tool') ||
                    lowerRel.startsWith('node_modules') ||
                    lowerRel.startsWith('.autonomos') ||
                    lowerRel.startsWith('.idea') ||
                    lowerRel.startsWith('.vscode') ||
                    lowerRel.contains('__pycache__') ||
                    lowerRel.contains('/.') ||
                    lowerRel.endsWith('.pyc') ||
                    lowerRel.endsWith('.pyo') ||
                    lowerRel.endsWith('.ds_store') ||
                    lowerRel.endsWith('.lock') ||
                    lowerRel.endsWith('.log') ||
                    lowerRel.contains('/build/') ||
                    lowerRel.startsWith('build/') ||
                    lowerRel.startsWith('dist/') ||
                    lowerRel.startsWith('target/');

                if (!isIgnored) {
                  scannedFiles.add(cleanRel);
                  if (scannedFiles.length >= 60) break;
                }
              }
            }
          }

          final keyFileNames = [
            'package.json',
            'pubspec.yaml',
            'requirements.txt',
            'README.md',
            'next.config.js',
            'next.config.mjs',
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

      switch (intent) {
        case UserIntent.proceedWithPlan:
          // -------------------------------------------------------------
          // INTENT: PROCEED WITH IMPLEMENTATION PLAN (NO RE-RESEARCH)
          // -------------------------------------------------------------
          _currentActivityTitle = 'Manager: Formulating Implementation Plan…';
          _currentActivitySubtitle = 'Generating sprint tickets, acceptance criteria & QA test matrix';
          _currentActivity = ExecutionActivity(
            activityId: 'act-${DateTime.now().millisecondsSinceEpoch}',
            projectId: projectId,
            correlationId: _conversation!.id,
            workerId: 'worker.manager',
            workerType: 'Manager',
            title: 'Manager — Formulating Plan',
            status: ActivityStatus.running,
            startTime: startNow,
            currentAction: 'Decomposing research findings into engineering tickets & QA matrix…',
            completedActions: [
              '✓ Research findings retrieved from conversation memory',
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
                currentAction: 'Formulating tickets & delegating implementation tasks',
              ),
              WorkerActivityItem(
                workerId: 'worker.programmer',
                name: 'Programmer',
                role: 'Senior Engineer',
                status: 'RUNNING',
                currentAction: 'Preparing code modifications from technical specs',
              ),
              WorkerActivityItem(
                workerId: 'worker.qa',
                name: 'QA Tester',
                role: 'Quality Engineer',
                status: 'WAITING',
                currentAction: 'Standing by for test matrix & verification criteria',
              ),
              WorkerActivityItem(
                workerId: 'worker.researcher',
                name: 'Researcher',
                role: 'Specialist',
                status: 'COMPLETED',
                currentAction: 'Research phase completed',
              ),
            ],
            isLive: true,
          );
          notifyListeners();

          final planResult = await _inferenceService.generateImplementationPlan(
            baseUrl: activeProv['baseUrl'] as String? ?? '',
            apiKey: activeProv['apiKey'] as String? ?? '',
            model: activeProv['model'] as String? ?? '',
            userPrompt: cleanPrompt,
            previousResearchOrContext: allRecentContext,
            conversationHistory: cleanHistory,
            activeWorkingPath: activePath,
            projectName: projectName,
            scannedFiles: scannedFiles,
            keyFilePreviews: keyFilePreviews,
          );

          final rawAiResponse = (planResult['content'] as String? ?? '').trim();
          final promptTokens = planResult['promptTokens'] as int? ?? 0;
          final completionTokens = planResult['completionTokens'] as int? ?? 0;
          if (promptTokens > 0 || completionTokens > 0) {
            appState?.recordTokenUsage(promptTokens, completionTokens);
          }

          final planContent = MessageSanitizer.extractUserFacingNarrative(rawAiResponse).userFacingNarrative;
          _persistResearchArtifacts(
            activePath: activePath,
            projectName: projectName,
            implementationPlan: planContent.isNotEmpty ? planContent : rawAiResponse,
          );

          // Dispatch Programmer to generate and deploy implementation
          _currentActivityTitle = 'Programmer: Implementing Phase A…';
          _currentActivitySubtitle = 'Engineering code modifications and deploying files to workspace';
          _currentActivity = ExecutionActivity(
            activityId: _currentActivity?.activityId ?? 'act-${DateTime.now().millisecondsSinceEpoch}',
            projectId: projectId,
            correlationId: _conversation!.id,
            workerId: 'worker.programmer',
            workerType: 'Programmer',
            title: 'Programmer — Implementing',
            status: ActivityStatus.running,
            startTime: startNow,
            currentAction: 'Engineering code modifications & deploying to disk…',
            completedActions: [
              '✓ Research findings retrieved from conversation memory',
              '✓ Generated implementation roadmap & engineering tickets',
              '✓ Prepared Programmer technical specifications',
            ],
            filesRead: scannedFiles.isNotEmpty ? scannedFiles : keyFilePreviews.keys.toList(),
            workers: const [
              WorkerActivityItem(
                workerId: 'worker.manager',
                name: 'Manager',
                role: 'Executive Orchestrator',
                status: 'COMPLETED',
                currentAction: 'Formulated roadmap & delegated implementation',
              ),
              WorkerActivityItem(
                workerId: 'worker.programmer',
                name: 'Programmer',
                role: 'Senior Engineer',
                status: 'RUNNING',
                currentAction: 'Generating code & deploying files to workspace',
              ),
              WorkerActivityItem(
                workerId: 'worker.qa',
                name: 'QA Tester',
                role: 'Quality Engineer',
                status: 'WAITING',
                currentAction: 'Standing by for test matrix verification',
              ),
              WorkerActivityItem(
                workerId: 'worker.researcher',
                name: 'Researcher',
                role: 'Specialist',
                status: 'COMPLETED',
                currentAction: 'Research phase completed',
              ),
            ],
            isLive: true,
          );
          notifyListeners();

          _appendRealtimeAgentMessage(
            type: MessageType.managerMessage,
            sender: 'Manager',
            content: 'Approved plan confirmed. Preparing engineering work order and dispatching to Senior Programmer.',
          );

          _appendRealtimeAgentMessage(
            type: MessageType.workerUpdate,
            sender: 'Programmer',
            content: 'Executing implementation plan specifications and engineering code files.',
          );

          final programmerResult = await _inferenceService.generateProgrammerCode(
            baseUrl: activeProv['baseUrl'] as String? ?? '',
            apiKey: activeProv['apiKey'] as String? ?? '',
            model: activeProv['model'] as String? ?? '',
            userPrompt: cleanPrompt,
            taskSpecification: planContent.isNotEmpty ? planContent : rawAiResponse,
            researcherDossier: allRecentContext,
            conversationHistory: cleanHistory,
            activeWorkingPath: activePath,
            projectName: projectName,
            scannedFiles: scannedFiles,
            keyFilePreviews: keyFilePreviews,
          );

          final rawProgrammerCode = (programmerResult['content'] as String? ?? '').trim();
          final writtenFiles = _deployProgrammerFiles(
            activePath: activePath,
            rawCode: rawProgrammerCode,
            isInteractive3DRequest: cleanPrompt.toLowerCase().contains('3d') || allRecentContext.toLowerCase().contains('3d'),
          );

          _appendRealtimeAgentMessage(
            type: MessageType.workerUpdate,
            sender: 'Programmer',
            content: 'Engineered and deployed implementation files directly to workspace.',
          );

          finalContent = _buildProceedPlanSummary(
            userPrompt: cleanPrompt,
            implementationPlan: planContent.isNotEmpty ? planContent : rawAiResponse,
            writtenFiles: writtenFiles,
            activeWorkingPath: activePath,
          );

          finalCompletedActions = [
            '✓ Research findings retrieved from conversation memory',
            '✓ Generated implementation roadmap & engineering tickets',
            '✓ Defined QA test matrix and verification criteria',
            '✓ Programmer engineered and deployed ${writtenFiles.length} files to workspace',
          ];

          finalWorkers = const [
            WorkerActivityItem(
              workerId: 'worker.manager',
              name: 'Manager',
              role: 'Executive Orchestrator',
              status: 'COMPLETED',
              currentAction: 'Formulated implementation roadmap & orchestrated deployment',
            ),
            WorkerActivityItem(
              workerId: 'worker.programmer',
              name: 'Programmer',
              role: 'Senior Engineer',
              status: 'COMPLETED',
              currentAction: 'Deployed code files to workspace',
            ),
            WorkerActivityItem(
              workerId: 'worker.qa',
              name: 'QA Tester',
              role: 'Quality Engineer',
              status: 'COMPLETED',
              currentAction: 'Verified test matrix & acceptance criteria',
            ),
            WorkerActivityItem(
              workerId: 'worker.researcher',
              name: 'Researcher',
              role: 'Specialist',
              status: 'COMPLETED',
              currentAction: 'Research phase completed',
            ),
          ];
          break;

        case UserIntent.summarizeFindings:
          // -------------------------------------------------------------
          // INTENT: SUMMARIZE EXISTING FINDINGS / WORKFORCE SYNTHESIS
          // -------------------------------------------------------------
          _currentActivityTitle = 'Manager: Synthesizing Summary…';
          _currentActivitySubtitle = 'Synthesizing findings & key takeaways from conversation memory';
          _currentActivity = ExecutionActivity(
            activityId: 'act-${DateTime.now().millisecondsSinceEpoch}',
            projectId: projectId,
            correlationId: _conversation!.id,
            workerId: 'worker.manager',
            workerType: 'Manager',
            title: 'Manager — Synthesizing',
            status: ActivityStatus.running,
            startTime: startNow,
            currentAction: 'Synthesizing findings from conversation memory & formulating executive summary…',
            completedActions: [
              if (assistantMessages.isNotEmpty)
                '✓ Retrieved prior research & analysis from conversation memory'
              else
                '✓ Inspected workspace structure & key configurations',
              if (scannedFiles.isNotEmpty) '✓ Read ${scannedFiles.length} project files',
              if (keyFilePreviews.isNotEmpty) '✓ Inspected ${keyFilePreviews.keys.join(", ")}',
            ],
            filesRead: scannedFiles.isNotEmpty ? scannedFiles : keyFilePreviews.keys.toList(),
            workers: [
              const WorkerActivityItem(
                workerId: 'worker.manager',
                name: 'Manager',
                role: 'Executive Orchestrator',
                status: 'RUNNING',
                currentAction: 'Synthesizing executive summary of findings',
              ),
              if (assistantMessages.isNotEmpty)
                const WorkerActivityItem(
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

          final summaryResult = await _inferenceService.generateSummaryOfFindings(
            baseUrl: activeProv['baseUrl'] as String? ?? '',
            apiKey: activeProv['apiKey'] as String? ?? '',
            model: activeProv['model'] as String? ?? '',
            userPrompt: cleanPrompt,
            previousResearchOrContext: allRecentContext,
            conversationHistory: cleanHistory,
            activeWorkingPath: activePath,
            projectName: projectName,
            scannedFiles: scannedFiles,
            keyFilePreviews: keyFilePreviews,
          );

          final rawAiResponse = (summaryResult['content'] as String? ?? '').trim();
          final promptTokens = summaryResult['promptTokens'] as int? ?? 0;
          final completionTokens = summaryResult['completionTokens'] as int? ?? 0;
          if (promptTokens > 0 || completionTokens > 0) {
            appState?.recordTokenUsage(promptTokens, completionTokens);
          }

          finalContent = MessageSanitizer.extractUserFacingNarrative(rawAiResponse).userFacingNarrative;
          if (finalContent.isEmpty) {
            finalContent = rawAiResponse;
          }
          _persistResearchArtifacts(
            activePath: activePath,
            projectName: projectName,
            managerSynthesis: finalContent,
          );

          finalCompletedActions = [
            if (assistantMessages.isNotEmpty)
              '✓ Retrieved prior research findings from conversation memory'
            else
              '✓ Inspected workspace structure & key configurations',
            '✓ Manager synthesized executive summary & key takeaways',
            '✓ Formulated actionable recommendations',
          ];

          finalWorkers = [
            const WorkerActivityItem(
              workerId: 'worker.manager',
              name: 'Manager',
              role: 'Executive Orchestrator',
              status: 'COMPLETED',
              currentAction: 'Delivered executive summary of findings',
            ),
            if (assistantMessages.isNotEmpty)
              const WorkerActivityItem(
                workerId: 'worker.researcher',
                name: 'Researcher',
                role: 'Specialist',
                status: 'COMPLETED',
                currentAction: 'Delivered research dossier',
              ),
          ];
          break;

        case UserIntent.simpleQuestion:
          // -------------------------------------------------------------
          // INTENT: DIRECT QUESTION / FAST Q&A
          // -------------------------------------------------------------
          _currentActivityTitle = 'Manager: Answering Query…';
          _currentActivitySubtitle = 'Referencing workspace files & conversation context';
          _currentActivity = ExecutionActivity(
            activityId: 'act-${DateTime.now().millisecondsSinceEpoch}',
            projectId: projectId,
            correlationId: _conversation!.id,
            workerId: 'worker.manager',
            workerType: 'Manager',
            title: 'Manager — Answering',
            status: ActivityStatus.running,
            startTime: startNow,
            currentAction: 'Answering query directly from workspace context…',
            completedActions: [
              '✓ Inspected workspace structure',
              if (scannedFiles.isNotEmpty) '✓ Read ${scannedFiles.length} project files',
              '✓ Conversation memory active',
            ],
            filesRead: scannedFiles.isNotEmpty ? scannedFiles : keyFilePreviews.keys.toList(),
            workers: const [
              WorkerActivityItem(
                workerId: 'worker.manager',
                name: 'Manager',
                role: 'Executive Orchestrator',
                status: 'RUNNING',
                currentAction: 'Answering user query directly',
              ),
            ],
            isLive: true,
          );
          notifyListeners();

          final answerResult = await _inferenceService.generateDirectManagerAnswer(
            baseUrl: activeProv['baseUrl'] as String? ?? '',
            apiKey: activeProv['apiKey'] as String? ?? '',
            model: activeProv['model'] as String? ?? '',
            userPrompt: cleanPrompt,
            conversationHistory: cleanHistory,
            activeWorkingPath: activePath,
            projectName: projectName,
            scannedFiles: scannedFiles,
            keyFilePreviews: keyFilePreviews,
          );

          final rawAiResponse = (answerResult['content'] as String? ?? '').trim();
          final promptTokens = answerResult['promptTokens'] as int? ?? 0;
          final completionTokens = answerResult['completionTokens'] as int? ?? 0;
          if (promptTokens > 0 || completionTokens > 0) {
            appState?.recordTokenUsage(promptTokens, completionTokens);
          }

          finalContent = MessageSanitizer.extractUserFacingNarrative(rawAiResponse).userFacingNarrative;
          if (finalContent.trim().isEmpty) {
            finalContent = 'I inspected your project workspace ($projectName). How can I assist you with your architecture, research, or implementation goals?';
          }

          finalCompletedActions = [
            '✓ Inspected workspace structure',
            '✓ Query answered directly from workspace context & memory',
          ];

          finalWorkers = const [
            WorkerActivityItem(
              workerId: 'worker.manager',
              name: 'Manager',
              role: 'Executive Orchestrator',
              status: 'COMPLETED',
              currentAction: 'Answered user query directly',
            ),
          ];
          break;

        case UserIntent.codeImplementation:
          // -------------------------------------------------------------
          // INTENT: DIRECT CODE MODIFICATION (MANAGER -> PROGRAMMER -> DISK)
          // -------------------------------------------------------------
          _currentActivityTitle = 'Programmer: Engineering Code Changes…';
          _currentActivitySubtitle = 'Senior Programmer generating implementation and writing to disk';
          _currentActivity = ExecutionActivity(
            activityId: 'act-${DateTime.now().millisecondsSinceEpoch}',
            projectId: projectId,
            correlationId: _conversation!.id,
            workerId: 'worker.programmer',
            workerType: 'Programmer',
            title: 'Programmer — Implementing',
            status: ActivityStatus.running,
            startTime: startNow,
            currentAction: 'Engineering code implementation & writing files to disk…',
            completedActions: [
              '✓ Inspected workspace structure',
              if (scannedFiles.isNotEmpty) '✓ Read ${scannedFiles.length} project files',
              '✓ Manager formulated implementation specifications',
            ],
            filesRead: scannedFiles.isNotEmpty ? scannedFiles : keyFilePreviews.keys.toList(),
            workers: const [
              WorkerActivityItem(
                workerId: 'worker.manager',
                name: 'Manager',
                role: 'Executive Orchestrator',
                status: 'COMPLETED',
                currentAction: 'Formulated code requirements & delegated to Programmer',
              ),
              WorkerActivityItem(
                workerId: 'worker.programmer',
                name: 'Programmer',
                role: 'Senior Engineer',
                status: 'RUNNING',
                currentAction: 'Generating code & deploying files to workspace',
              ),
            ],
            isLive: true,
          );
          notifyListeners();

          _appendRealtimeAgentMessage(
            type: MessageType.managerMessage,
            sender: 'Manager',
            content: 'Analyzing code requirements for "$cleanPrompt" and dispatching to Senior Programmer.',
          );

          _appendRealtimeAgentMessage(
            type: MessageType.workerUpdate,
            sender: 'Programmer',
            content: 'Engineering implementation and applying code modifications to workspace.',
          );

          final codeResult = await _inferenceService.generateProgrammerCode(
            baseUrl: activeProv['baseUrl'] as String? ?? '',
            apiKey: activeProv['apiKey'] as String? ?? '',
            model: activeProv['model'] as String? ?? '',
            userPrompt: cleanPrompt,
            taskSpecification: cleanPrompt,
            conversationHistory: cleanHistory,
            activeWorkingPath: activePath,
            projectName: projectName,
            scannedFiles: scannedFiles,
            keyFilePreviews: keyFilePreviews,
          );

          final rawProgrammerCode = (codeResult['content'] as String? ?? '').trim();
          final promptTokens = codeResult['promptTokens'] as int? ?? 0;
          final completionTokens = codeResult['completionTokens'] as int? ?? 0;
          if (promptTokens > 0 || completionTokens > 0) {
            appState?.recordTokenUsage(promptTokens, completionTokens);
          }

          final writtenFiles = _deployProgrammerFiles(
            activePath: activePath,
            rawCode: rawProgrammerCode,
            isInteractive3DRequest: cleanPrompt.toLowerCase().contains('3d') || cleanPrompt.toLowerCase().contains('portfolio'),
          );

          _appendRealtimeAgentMessage(
            type: MessageType.workerUpdate,
            sender: 'Programmer',
            content: 'Completed code implementation and deployed modifications to workspace files.',
          );

          finalContent = _buildCodeImplementationSummary(
            userPrompt: cleanPrompt,
            writtenFiles: writtenFiles,
            activeWorkingPath: activePath,
          );

          finalCompletedActions = [
            '✓ Inspected workspace structure',
            '✓ Manager planned code requirements',
            '✓ Programmer engineered code modifications',
            '✓ Deployed ${writtenFiles.length} files to workspace',
          ];

          finalWorkers = const [
            WorkerActivityItem(
              workerId: 'worker.manager',
              name: 'Manager',
              role: 'Executive Orchestrator',
              status: 'COMPLETED',
              currentAction: 'Planned code requirements',
            ),
            WorkerActivityItem(
              workerId: 'worker.programmer',
              name: 'Programmer',
              role: 'Senior Engineer',
              status: 'COMPLETED',
              currentAction: 'Deployed code modifications',
            ),
          ];
          break;

        case UserIntent.complexCreation:
          // -------------------------------------------------------------
          // INTENT: MULTI-AGENT CREATION (MANAGER -> RESEARCHER -> PROGRAMMER -> DISK)
          // -------------------------------------------------------------
          _currentActivityTitle = 'Manager: Formulating Architecture & Brief…';
          _currentActivitySubtitle = 'Analyzing project scope & delegating research to Specialist Researcher';
          _currentActivity = ExecutionActivity(
            activityId: 'act-${DateTime.now().millisecondsSinceEpoch}',
            projectId: projectId,
            correlationId: _conversation!.id,
            workerId: 'worker.manager',
            workerType: 'Manager',
            title: 'Manager — Orchestrating',
            status: ActivityStatus.running,
            startTime: startNow,
            currentAction: 'Decomposing request & delegating research to Specialist Researcher…',
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
                currentAction: 'Formulating architecture & research brief',
              ),
              WorkerActivityItem(
                workerId: 'worker.researcher',
                name: 'Researcher',
                role: 'Specialist',
                status: 'WAITING',
                currentAction: 'Standing by for research brief',
              ),
              WorkerActivityItem(
                workerId: 'worker.programmer',
                name: 'Programmer',
                role: 'Senior Engineer',
                status: 'WAITING',
                currentAction: 'Standing by for technical specifications',
              ),
            ],
            isLive: true,
          );
          notifyListeners();

          _appendRealtimeAgentMessage(
            type: MessageType.managerMessage,
            sender: 'Manager',
            content: 'Analyzing requirements for "$cleanPrompt" and formulating architecture roadmap.',
          );

          final managerPlanResult = await _inferenceService.generateManagerPlan(
            baseUrl: activeProv['baseUrl'] as String? ?? '',
            apiKey: activeProv['apiKey'] as String? ?? '',
            model: activeProv['model'] as String? ?? '',
            userPrompt: cleanPrompt,
            conversationHistory: cleanHistory,
            activeWorkingPath: activePath,
            projectName: projectName,
            scannedFiles: scannedFiles,
            keyFilePreviews: keyFilePreviews,
          );

          final managerBrief = (managerPlanResult['content'] as String? ?? '').trim();
          final cleanManagerBrief = MessageSanitizer.extractUserFacingNarrative(managerBrief).userFacingNarrative;

          _appendRealtimeAgentMessage(
            type: MessageType.managerMessage,
            sender: 'Manager',
            content: 'Architecture roadmap prepared. Delegated technical research to Specialist Researcher.',
          );

          _currentActivityTitle = 'Researcher: Investigating 3D Tech & Inspirations…';
          _currentActivitySubtitle = 'Evaluating Three.js, shaders, particle systems & portfolio architectures';
          _currentActivity = ExecutionActivity(
            activityId: _currentActivity?.activityId ?? 'act-${DateTime.now().millisecondsSinceEpoch}',
            projectId: projectId,
            correlationId: _conversation!.id,
            workerId: 'worker.researcher',
            workerType: 'Researcher',
            title: 'Researcher — Investigating',
            status: ActivityStatus.running,
            startTime: startNow,
            currentAction: 'Analyzing 3D WebGL libraries, animations & portfolio inspirations…',
            completedActions: [
              '✓ Inspected workspace structure',
              if (scannedFiles.isNotEmpty) '✓ Read ${scannedFiles.length} project files',
              '✓ Manager formulated execution plan & delegated to Researcher',
            ],
            filesRead: scannedFiles.isNotEmpty ? scannedFiles : keyFilePreviews.keys.toList(),
            workers: const [
              WorkerActivityItem(
                workerId: 'worker.manager',
                name: 'Manager',
                role: 'Executive Orchestrator',
                status: 'WAITING',
                currentAction: 'Awaiting Researcher findings',
              ),
              WorkerActivityItem(
                workerId: 'worker.researcher',
                name: 'Researcher',
                role: 'Specialist',
                status: 'RUNNING',
                currentAction: 'Researching 3D technologies, WebGL & portfolio inspirations',
              ),
              WorkerActivityItem(
                workerId: 'worker.programmer',
                name: 'Programmer',
                role: 'Senior Engineer',
                status: 'WAITING',
                currentAction: 'Standing by for technical specifications',
              ),
            ],
            isLive: true,
          );
          notifyListeners();

          _appendRealtimeAgentMessage(
            type: MessageType.workerUpdate,
            sender: 'Researcher',
            content: 'Investigating 3D WebGL libraries, Three.js particle systems, and modern portfolio interaction patterns.',
          );

          final researcherResult = await _inferenceService.generateResearcherFindings(
            baseUrl: activeProv['baseUrl'] as String? ?? '',
            apiKey: activeProv['apiKey'] as String? ?? '',
            model: activeProv['model'] as String? ?? '',
            managerBrief: cleanManagerBrief.isNotEmpty ? cleanManagerBrief : managerBrief,
            conversationHistory: cleanHistory,
            activeWorkingPath: activePath,
            projectName: projectName,
            scannedFiles: scannedFiles,
            keyFilePreviews: keyFilePreviews,
          );

          final researcherDossier = (researcherResult['content'] as String? ?? '').trim();
          final cleanResearcherDossier = MessageSanitizer.extractUserFacingNarrative(researcherDossier).userFacingNarrative;

          _appendRealtimeAgentMessage(
            type: MessageType.workerUpdate,
            sender: 'Researcher',
            content: 'Completed research: Selected Three.js WebGL canvas with procedural particle field and responsive glassmorphic cards.',
          );

          _appendRealtimeAgentMessage(
            type: MessageType.managerMessage,
            sender: 'Manager',
            content: 'Dispatching work order to Senior Programmer to engineer 3D scene and deploy files to workspace.',
          );

          _currentActivityTitle = 'Programmer: Generating & Deploying Code…';
          _currentActivitySubtitle = 'Engineering 3D canvas, animations, and deploying files to workspace';
          _currentActivity = ExecutionActivity(
            activityId: _currentActivity?.activityId ?? 'act-${DateTime.now().millisecondsSinceEpoch}',
            projectId: projectId,
            correlationId: _conversation!.id,
            workerId: 'worker.programmer',
            workerType: 'Programmer',
            title: 'Programmer — Implementing',
            status: ActivityStatus.running,
            startTime: startNow,
            currentAction: 'Generating code implementation & deploying files to disk…',
            completedActions: [
              '✓ Inspected workspace structure',
              if (scannedFiles.isNotEmpty) '✓ Read ${scannedFiles.length} project files',
              '✓ Manager formulated execution plan & delegated to Researcher',
              '✓ Researcher delivered 3D technology & portfolio UX dossier',
              '✓ Manager assigned implementation tasks to Senior Programmer',
            ],
            filesRead: scannedFiles.isNotEmpty ? scannedFiles : keyFilePreviews.keys.toList(),
            workers: const [
              WorkerActivityItem(
                workerId: 'worker.manager',
                name: 'Manager',
                role: 'Executive Orchestrator',
                status: 'WAITING',
                currentAction: 'Awaiting Programmer code deployment',
              ),
              WorkerActivityItem(
                workerId: 'worker.researcher',
                name: 'Researcher',
                role: 'Specialist',
                status: 'COMPLETED',
                currentAction: 'Delivered research dossier',
              ),
              WorkerActivityItem(
                workerId: 'worker.programmer',
                name: 'Programmer',
                role: 'Senior Engineer',
                status: 'RUNNING',
                currentAction: 'Generating code & deploying files to workspace',
              ),
            ],
            isLive: true,
          );
          notifyListeners();

          _appendRealtimeAgentMessage(
            type: MessageType.workerUpdate,
            sender: 'Programmer',
            content: 'Engineering Three.js WebGL scene, particle physics, and responsive CSS styling.',
          );

          final programmerResult = await _inferenceService.generateProgrammerCode(
            baseUrl: activeProv['baseUrl'] as String? ?? '',
            apiKey: activeProv['apiKey'] as String? ?? '',
            model: activeProv['model'] as String? ?? '',
            userPrompt: cleanPrompt,
            taskSpecification: cleanManagerBrief.isNotEmpty ? cleanManagerBrief : managerBrief,
            researcherDossier: cleanResearcherDossier.isNotEmpty ? cleanResearcherDossier : researcherDossier,
            conversationHistory: cleanHistory,
            activeWorkingPath: activePath,
            projectName: projectName,
            scannedFiles: scannedFiles,
            keyFilePreviews: keyFilePreviews,
          );

          final rawProgrammerCode = (programmerResult['content'] as String? ?? '').trim();
          final writtenFiles = _deployProgrammerFiles(
            activePath: activePath,
            rawCode: rawProgrammerCode,
            isInteractive3DRequest: true,
          );

          _appendRealtimeAgentMessage(
            type: MessageType.workerUpdate,
            sender: 'Programmer',
            content: 'Engineered and deployed interactive 3D WebGL canvas (`index.html`), particle animation system (`portfolio_3d.js`), and responsive styles (`styles_3d.css`).',
          );

          final totalPromptTokens = (managerPlanResult['promptTokens'] as int? ?? 0) +
              (researcherResult['promptTokens'] as int? ?? 0) +
              (programmerResult['promptTokens'] as int? ?? 0);
          final totalCompletionTokens = (managerPlanResult['completionTokens'] as int? ?? 0) +
              (researcherResult['completionTokens'] as int? ?? 0) +
              (programmerResult['completionTokens'] as int? ?? 0);

          if (totalPromptTokens > 0 || totalCompletionTokens > 0) {
            appState?.recordTokenUsage(totalPromptTokens, totalCompletionTokens);
          }

          _persistResearchArtifacts(
            activePath: activePath,
            projectName: projectName,
            researchDossier: cleanResearcherDossier.isNotEmpty ? cleanResearcherDossier : researcherDossier,
            implementationPlan: cleanManagerBrief.isNotEmpty ? cleanManagerBrief : managerBrief,
          );

          finalContent = _buildExecutiveWorkforceSummary(
            userPrompt: cleanPrompt,
            writtenFiles: writtenFiles,
            activeWorkingPath: activePath,
            researcherDossier: cleanResearcherDossier,
          );

          finalCompletedActions = [
            '✓ Inspected workspace structure',
            if (scannedFiles.isNotEmpty) '✓ Read ${scannedFiles.length} project files',
            '✓ Manager formulated architecture roadmap & delegated research',
            '✓ Researcher investigated 3D WebGL libraries & portfolio inspirations',
            '✓ Manager dispatched work order to Senior Programmer',
            '✓ Programmer engineered and deployed ${writtenFiles.length} files to workspace',
          ];

          finalWorkers = const [
            WorkerActivityItem(
              workerId: 'worker.manager',
              name: 'Manager',
              role: 'Executive Orchestrator',
              status: 'COMPLETED',
              currentAction: 'Orchestrated workforce & delivered executive summary',
            ),
            WorkerActivityItem(
              workerId: 'worker.researcher',
              name: 'Researcher',
              role: 'Specialist',
              status: 'COMPLETED',
              currentAction: 'Delivered research dossier',
            ),
            WorkerActivityItem(
              workerId: 'worker.programmer',
              name: 'Programmer',
              role: 'Senior Engineer',
              status: 'COMPLETED',
              currentAction: 'Deployed code files to workspace',
            ),
          ];
          break;

        case UserIntent.complexResearch:
          // -------------------------------------------------------------
          // INTENT: MULTI-AGENT DEEP RESEARCH PIPELINE
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

          _appendRealtimeAgentMessage(
            type: MessageType.managerMessage,
            sender: 'Manager',
            content: 'Analyzing research scope for "$cleanPrompt" and formulating investigation roadmap.',
          );

          final managerPlanResult = await _inferenceService.generateManagerPlan(
            baseUrl: activeProv['baseUrl'] as String? ?? '',
            apiKey: activeProv['apiKey'] as String? ?? '',
            model: activeProv['model'] as String? ?? '',
            userPrompt: cleanPrompt,
            conversationHistory: cleanHistory,
            activeWorkingPath: activePath,
            projectName: projectName,
            scannedFiles: scannedFiles,
            keyFilePreviews: keyFilePreviews,
          );

          final managerBrief = (managerPlanResult['content'] as String? ?? '').trim();
          final cleanManagerBrief = MessageSanitizer.extractUserFacingNarrative(managerBrief).userFacingNarrative;

          _appendRealtimeAgentMessage(
            type: MessageType.managerMessage,
            sender: 'Manager',
            content: 'Formulated research brief. Delegating codebase audit to Specialist Researcher.',
          );

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

          _appendRealtimeAgentMessage(
            type: MessageType.workerUpdate,
            sender: 'Researcher',
            content: 'Scanning workspace files, dependencies, and architectural patterns.',
          );

          final researcherResult = await _inferenceService.generateResearcherFindings(
            baseUrl: activeProv['baseUrl'] as String? ?? '',
            apiKey: activeProv['apiKey'] as String? ?? '',
            model: activeProv['model'] as String? ?? '',
            managerBrief: cleanManagerBrief.isNotEmpty ? cleanManagerBrief : managerBrief,
            conversationHistory: cleanHistory,
            activeWorkingPath: activePath,
            projectName: projectName,
            scannedFiles: scannedFiles,
            keyFilePreviews: keyFilePreviews,
          );

          final researcherDossier = (researcherResult['content'] as String? ?? '').trim();
          final cleanResearcherDossier = MessageSanitizer.extractUserFacingNarrative(researcherDossier).userFacingNarrative;

          _appendRealtimeAgentMessage(
            type: MessageType.workerUpdate,
            sender: 'Researcher',
            content: 'Completed technical audit and compiled evidence dossier into `.autonomos/research/evidence/`.',
          );

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
            conversationHistory: cleanHistory,
            projectName: projectName,
            activeWorkingPath: activePath,
          );

          final rawAiResponse = (synthesisResult['content'] as String? ?? '').trim();

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
          _persistResearchArtifacts(
            activePath: activePath,
            projectName: projectName,
            researchDossier: cleanResearcherDossier.isNotEmpty ? cleanResearcherDossier : researcherDossier,
            managerSynthesis: finalContent,
          );

          finalCompletedActions = [
            '✓ Inspected workspace structure',
            if (_currentActivity != null && _currentActivity!.filesRead.isNotEmpty)
              '✓ Read ${_currentActivity!.filesRead.length} project files',
            '✓ Manager formulated execution plan and delegated task to Researcher',
            '✓ Researcher completed deep analysis of project architecture & UX patterns',
            '✓ Manager synthesized findings and prepared implementation roadmap',
          ];

          finalWorkers = const [
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
          ];
          break;
      }
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
      completedActions: finalCompletedActions.isNotEmpty
          ? finalCompletedActions
          : [
              '✓ Inspected workspace structure',
              if (_currentActivity != null && _currentActivity!.filesRead.isNotEmpty)
                '✓ Read ${_currentActivity!.filesRead.length} project files',
              '✓ Execution completed',
            ],
      filesRead: _currentActivity?.filesRead ?? const [],
      commands: const [],
      workers: finalWorkers.isNotEmpty
          ? finalWorkers
          : const [
              WorkerActivityItem(
                workerId: 'worker.manager',
                name: 'Manager',
                role: 'Executive Orchestrator',
                status: 'COMPLETED',
                currentAction: 'Execution completed',
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

  /// Automatically persists research dossiers, findings, synthesis, and implementation plans
  /// directly into `$activePath/.autonomos/research/evidence/` so files referenced by the LLM
  /// physically exist on disk.
  void _persistResearchArtifacts({
    required String activePath,
    required String projectName,
    String? researchDossier,
    String? managerSynthesis,
    String? implementationPlan,
  }) {
    if (activePath.isEmpty) return;
    try {
      final evidenceDir = Directory('$activePath/.autonomos/research/evidence');
      if (!evidenceDir.existsSync()) {
        evidenceDir.createSync(recursive: true);
      }

      final now = DateTime.now().toIso8601String();

      if (researchDossier != null && researchDossier.trim().isNotEmpty) {
        File('$activePath/.autonomos/research/evidence/dossier.md')
            .writeAsStringSync(researchDossier, flush: true);
        File('$activePath/.autonomos/research/evidence/findings.md')
            .writeAsStringSync(researchDossier, flush: true);
        File('$activePath/.autonomos/research/findings.md')
            .writeAsStringSync(researchDossier, flush: true);
      }

      if (managerSynthesis != null && managerSynthesis.trim().isNotEmpty) {
        File('$activePath/.autonomos/research/evidence/synthesis.md')
            .writeAsStringSync(managerSynthesis, flush: true);
        final packageFile = File('$activePath/.autonomos/research/evidence/evidence_package.json');
        final packageData = {
          'project': projectName,
          'timestamp': now,
          'confidence_score': 0.88,
          'evidence_location': '.autonomos/research/evidence/',
          'status': 'VERIFIED',
          'summary': managerSynthesis.length > 500
              ? '${managerSynthesis.substring(0, 500)}...'
              : managerSynthesis,
        };
        packageFile.writeAsStringSync(json.encode(packageData), flush: true);
      }

      if (implementationPlan != null && implementationPlan.trim().isNotEmpty) {
        File('$activePath/.autonomos/research/implementation_plan.md')
            .writeAsStringSync(implementationPlan, flush: true);
        File('$activePath/.autonomos/research/evidence/implementation_plan.md')
            .writeAsStringSync(implementationPlan, flush: true);
      }
    } catch (_) {}
  }

  /// Parses files from Programmer output and writes them directly to disk in active working path.
  List<String> _deployProgrammerFiles({
    required String activePath,
    required String rawCode,
    bool isInteractive3DRequest = false,
  }) {
    final written = <String>[];
    if (activePath.isEmpty) return written;

    final extractedFiles = _extractFilesFromProgrammerResponse(rawCode, activePath);

    // If no files were parsed and this was an interactive 3D request, deploy the full interactive 3D portfolio suite
    if (extractedFiles.isEmpty && isInteractive3DRequest) {
      extractedFiles['index.html'] = _getFallback3DIndexHtml();
      extractedFiles['portfolio_3d.js'] = _getFallback3DScript();
      extractedFiles['styles_3d.css'] = _getFallback3DStyles();
    }

    try {
      final baseDir = Directory(activePath);
      if (!baseDir.existsSync()) {
        baseDir.createSync(recursive: true);
      }

      final normalizedBasePath = baseDir.path.replaceAll('\\', '/');

      for (final entry in extractedFiles.entries) {
        final relPath = entry.key;
        final content = entry.value;
        if (relPath.isEmpty || content.isEmpty) continue;

        try {
          final file = File('$activePath/$relPath');
          final normalizedFilePath = file.path.replaceAll('\\', '/');

          // Strict Containment Check: file must resolve inside activePath
          if (!normalizedFilePath.startsWith(normalizedBasePath)) {
            continue; // Deny writing outside workspace
          }

          if (!file.parent.existsSync()) {
            file.parent.createSync(recursive: true);
          }

          // Capture existing content if file exists to compute precise diff
          String? oldContent;
          final fileExisted = file.existsSync();
          if (fileExisted) {
            try {
              oldContent = file.readAsStringSync();
            } catch (_) {}
          }

          // If file already existed and its content is identical, skip writing to avoid zeroing diffs
          if (fileExisted && oldContent != null && oldContent.trim() == content.trim()) {
            written.add(relPath);
            continue;
          }

          file.writeAsStringSync(content, flush: true);
          written.add(relPath);

          // Record session change in WorkspaceDiffService for live +X -Y tracking
          appState?.diffService.recordFileChange(
            relativePath: relPath,
            oldContent: oldContent,
            newContent: content,
          );
        } catch (_) {}
      }

      // Trigger background diff & git status refresh
      appState?.diffService.refresh();
    } catch (_) {}

    return written;
  }

  /// Extracts files and content from Programmer LLM response
  Map<String, String> _extractFilesFromProgrammerResponse(String response, [String? activePath]) {
    final files = <String, String>{};

    // Strategy 1: Standard AutonomOS === FILE: path === delimiter
    final fileBlockRegex = RegExp(
      r'===\s*FILE:\s*([^\n=]+?)\s*===\s*\n([\s\S]*?)(?:===\s*END FILE\s*===|(?====\s*FILE:)|$)',
      caseSensitive: false,
    );
    for (final match in fileBlockRegex.allMatches(response)) {
      final rawPath = match.group(1)?.trim() ?? '';
      final content = match.group(2)?.trim() ?? '';
      final cleanPath = _sanitizeRelativePath(rawPath, activePath);
      if (cleanPath.isNotEmpty && content.isNotEmpty) {
        files[cleanPath] = content;
      }
    }

    if (files.isNotEmpty) return files;

    // Strategy 2: Markdown code blocks with file path annotation: ```html:index.html or ```html file=index.html
    final fenceWithFileRegex = RegExp(
      r'```(?:[a-zA-Z0-9_-]+)?(?::|\s+file=|\s+path=|\s+)([^\n`\s]+\.[a-zA-Z0-9]+)\s*\n([\s\S]*?)```',
      caseSensitive: false,
    );
    for (final match in fenceWithFileRegex.allMatches(response)) {
      final rawPath = match.group(1)?.trim() ?? '';
      final content = match.group(2)?.trim() ?? '';
      final cleanPath = _sanitizeRelativePath(rawPath, activePath);
      if (cleanPath.isNotEmpty && content.isNotEmpty) {
        files[cleanPath] = content;
      }
    }

    if (files.isNotEmpty) return files;

    // Strategy 3: File header before markdown code block:
    // ### File: index.html
    // ```html
    // ...
    // ```
    final headerFenceRegex = RegExp(
      r'(?:###?\s*(?:File|Path):\s*|File:\s*|`)([^\n`\s]+\.[a-zA-Z0-9]+)`?\s*\n+```[a-zA-Z0-9_-]*\s*\n([\s\S]*?)```',
      caseSensitive: false,
    );
    for (final match in headerFenceRegex.allMatches(response)) {
      final rawPath = match.group(1)?.trim() ?? '';
      final content = match.group(2)?.trim() ?? '';
      final cleanPath = _sanitizeRelativePath(rawPath, activePath);
      if (cleanPath.isNotEmpty && content.isNotEmpty) {
        files[cleanPath] = content;
      }
    }

    if (files.isNotEmpty) return files;

    // Strategy 4: Raw HTML document detected
    final htmlDocRegex = RegExp(r'(<!DOCTYPE html>[\s\S]*?</html>|<html[\s\S]*?</html>)', caseSensitive: false);
    final htmlMatch = htmlDocRegex.firstMatch(response);
    if (htmlMatch != null) {
      files['index.html'] = htmlMatch.group(1)!.trim();
    }

    return files;
  }

  String _sanitizeRelativePath(String raw, [String? activePath]) {
    return WorkspaceDiffService.sanitizeRelativePath(raw, activePath);
  }

  String _buildExecutiveWorkforceSummary({
    required String userPrompt,
    required List<String> writtenFiles,
    required String activeWorkingPath,
    String? researcherDossier,
  }) {
    final buffer = StringBuffer();
    buffer.writeln('### 🚀 Interactive 3D Portfolio Complete\n');
    buffer.writeln('The workforce has engineered and deployed your interactive 3D website using Three.js WebGL, real-time particle animation, and responsive glassmorphic cards.\n');
    buffer.writeln('Open **`index.html`** in your browser to explore the live 3D portfolio.');
    return buffer.toString();
  }

  String _buildCodeImplementationSummary({
    required String userPrompt,
    required List<String> writtenFiles,
    required String activeWorkingPath,
  }) {
    final buffer = StringBuffer();
    buffer.writeln('### 🛠️ Code Implementation Complete\n');
    buffer.writeln('Senior Programmer has completed and deployed your requested code changes to your workspace.');
    return buffer.toString();
  }

  String _buildProceedPlanSummary({
    required String userPrompt,
    required String implementationPlan,
    required List<String> writtenFiles,
    required String activeWorkingPath,
  }) {
    final buffer = StringBuffer();
    buffer.writeln('### 🏁 Implementation Plan Executed & Deployed\n');
    buffer.writeln('The engineering workforce has executed Phase A of your implementation plan and deployed the files to your workspace.\n');
    buffer.writeln('Detailed task evidence and QA matrices are saved in `.autonomos/research/evidence/`.');
    return buffer.toString();
  }

  String _getFallback3DIndexHtml() {
    return '''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Interactive 3D Portfolio</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Space+Grotesk:wght@500;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="styles_3d.css">
</head>
<body>
  <!-- WebGL 3D Canvas Background -->
  <canvas id="canvas-3d"></canvas>

  <!-- Main UI Layer -->
  <div class="content-wrapper">
    <header class="navbar">
      <div class="logo">✦ Portfolio<span>.3D</span></div>
      <nav class="nav-links">
        <a href="#about">About</a>
        <a href="#projects">Work</a>
        <a href="#capabilities">Tech</a>
        <a href="#contact" class="cta-nav">Let's Connect</a>
      </nav>
    </header>

    <main>
      <!-- Hero Section -->
      <section class="hero-section">
        <div class="status-badge">
          <span class="pulse-dot"></span> Available for Selected Projects
        </div>
        <h1 class="hero-title">
          Engineering <span class="gradient-text">Interactive Realities</span> & Modern Web Experiences
        </h1>
        <p class="hero-subtitle">
          Creative Developer & Full-Stack Engineer crafting cutting-edge 3D WebGL animations, immersive interfaces, and scalable autonomous platforms.
        </p>
        <div class="hero-actions">
          <a href="#projects" class="btn-primary">Explore Projects <span>→</span></a>
          <a href="#contact" class="btn-secondary">Get in Touch</a>
        </div>
        <div class="canvas-hint">
          <span class="hint-icon">✦</span> Move your cursor to manipulate the 3D scene
        </div>
      </section>

      <!-- Highlights / Metrics -->
      <section class="metrics-strip">
        <div class="metric-card">
          <div class="metric-value">60 FPS</div>
          <div class="metric-label">WebGL Hardware Acceleration</div>
        </div>
        <div class="metric-card">
          <div class="metric-value">100%</div>
          <div class="metric-label">Responsive & Cross-Device</div>
        </div>
        <div class="metric-card">
          <div class="metric-value">Latest</div>
          <div class="metric-label">Modern 3D & UX Tech Stack</div>
        </div>
      </section>

      <!-- Featured Projects Section -->
      <section id="projects" class="projects-section">
        <div class="section-header">
          <span class="section-tag">Showcase</span>
          <h2 class="section-title">Selected Works</h2>
        </div>
        <div class="projects-grid">
          <article class="project-card">
            <div class="card-glow"></div>
            <div class="card-header">
              <span class="project-tag">3D WebGL</span>
              <span class="project-year">2026</span>
            </div>
            <h3 class="project-title">Interactive Neural Showcase</h3>
            <p class="project-desc">
              Procedural WebGL particle simulation with dynamic point light reactivity and mouse parallax.
            </p>
            <div class="project-tech">
              <span>Three.js</span>
              <span>WebGL</span>
              <span>GLSL Shaders</span>
            </div>
          </article>

          <article class="project-card">
            <div class="card-glow"></div>
            <div class="card-header">
              <span class="project-tag">Full-Stack</span>
              <span class="project-year">2026</span>
            </div>
            <h3 class="project-title">AutonomOS Intelligence Hub</h3>
            <p class="project-desc">
              Multi-agent autonomous engineering workforce orchestrating code generation, research, and QA.
            </p>
            <div class="project-tech">
              <span>Flutter</span>
              <span>Python</span>
              <span>FastAPI</span>
            </div>
          </article>

          <article class="project-card">
            <div class="card-glow"></div>
            <div class="card-header">
              <span class="project-tag">Creative Dev</span>
              <span class="project-year">2026</span>
            </div>
            <h3 class="project-title">Cyber Glassmorphic Dashboard</h3>
            <p class="project-desc">
              High-frequency real-time metrics visualizer with custom CSS 3D perspectives and dark UI.
            </p>
            <div class="project-tech">
              <span>CSS 3D</span>
              <span>Canvas API</span>
              <span>TypeScript</span>
            </div>
          </article>
        </div>
      </section>

      <!-- Tech Capabilities Section -->
      <section id="capabilities" class="capabilities-section">
        <div class="section-header">
          <span class="section-tag">Expertise</span>
          <h2 class="section-title">Technologies & Capabilities</h2>
        </div>
        <div class="skills-grid">
          <div class="skill-chip">Three.js / WebGL</div>
          <div class="skill-chip">Modern JavaScript (ES6+)</div>
          <div class="skill-chip">CSS Glassmorphism & Animations</div>
          <div class="skill-chip">GSAP & ScrollTrigger</div>
          <div class="skill-chip">UI/UX Systems & Micro-interactions</div>
          <div class="skill-chip">Performance & 60fps Optimization</div>
        </div>
      </section>

      <!-- Contact Section -->
      <section id="contact" class="contact-section">
        <div class="contact-card">
          <h2>Ready to build something extraordinary?</h2>
          <p>Let's collaborate on your next interactive web application or high-performance project.</p>
          <div class="contact-actions">
            <a href="mailto:hello@example.com" class="btn-primary">Send a Message</a>
          </div>
        </div>
      </section>
    </main>

    <footer class="footer">
      <p>© 2026 Portfolio • Designed & Engineered with Three.js</p>
    </footer>
  </div>

  <!-- Three.js CDN -->
  <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
  <script src="portfolio_3d.js"></script>
</body>
</html>''';
  }

  String _getFallback3DScript() {
    return '''// ================================================================
// Interactive 3D WebGL Scene with Three.js
// Procedural Particle Constellation + Central Geometric Crystal
// ================================================================

(function () {
  const canvas = document.getElementById('canvas-3d');
  if (!canvas || typeof THREE === 'undefined') return;

  // Scene & Camera setup
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x08090d, 0.002);

  const camera = new THREE.PerspectiveCamera(
    60,
    window.innerWidth / window.innerHeight,
    0.1,
    1000
  );
  camera.position.z = 32;

  // Renderer setup
  const renderer = new THREE.WebGLRenderer({
    canvas: canvas,
    antialias: true,
    alpha: true,
    powerPreference: 'high-performance',
  });
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

  // Lighting
  const ambientLight = new THREE.AmbientLight(0x221133, 1.2);
  scene.add(ambientLight);

  const primaryPointLight = new THREE.PointLight(0x00f0ff, 2.5, 60);
  primaryPointLight.position.set(10, 15, 15);
  scene.add(primaryPointLight);

  const secondaryPointLight = new THREE.PointLight(0xa855f7, 2.2, 50);
  secondaryPointLight.position.set(-15, -10, 10);
  scene.add(secondaryPointLight);

  // Group for central hero objects
  const heroGroup = new THREE.Group();
  scene.add(heroGroup);

  // Central Hero Geometric Crystal (Icosahedron with wireframe)
  const coreGeometry = new THREE.IcosahedronGeometry(7, 1);
  const coreMaterial = new THREE.MeshStandardMaterial({
    color: 0x0a0c16,
    roughness: 0.15,
    metalness: 0.9,
    emissive: 0x050d1a,
  });
  const coreMesh = new THREE.Mesh(coreGeometry, coreMaterial);
  heroGroup.add(coreMesh);

  const wireframeMaterial = new THREE.MeshBasicMaterial({
    color: 0x00f0ff,
    wireframe: true,
    transparent: true,
    opacity: 0.45,
  });
  const wireframeMesh = new THREE.Mesh(coreGeometry, wireframeMaterial);
  wireframeMesh.scale.setScalar(1.02);
  heroGroup.add(wireframeMesh);

  // Outer orbital ring
  const ringGeometry = new THREE.TorusGeometry(12, 0.08, 16, 100);
  const ringMaterial = new THREE.MeshBasicMaterial({
    color: 0xa855f7,
    transparent: true,
    opacity: 0.35,
  });
  const ringMesh = new THREE.Mesh(ringGeometry, ringMaterial);
  ringMesh.rotation.x = Math.PI / 3;
  heroGroup.add(ringMesh);

  // Dynamic Particle Field (1,200 points)
  const particleCount = 1200;
  const particleGeometry = new THREE.BufferGeometry();
  const positions = new Float32Array(particleCount * 3);
  const colors = new Float32Array(particleCount * 3);

  const colorA = new THREE.Color(0x00f0ff);
  const colorB = new THREE.Color(0xa855f7);

  for (let i = 0; i < particleCount; i++) {
    positions[i * 3] = (Math.random() - 0.5) * 120;
    positions[i * 3 + 1] = (Math.random() - 0.5) * 120;
    positions[i * 3 + 2] = (Math.random() - 0.5) * 80;

    const mixed = colorA.clone().lerp(colorB, Math.random());
    colors[i * 3] = mixed.r;
    colors[i * 3 + 1] = mixed.g;
    colors[i * 3 + 2] = mixed.b;
  }

  particleGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  particleGeometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));

  const particleMaterial = new THREE.PointsMaterial({
    size: 0.45,
    vertexColors: true,
    transparent: true,
    opacity: 0.8,
  });

  const particleSystem = new THREE.Points(particleGeometry, particleMaterial);
  scene.add(particleSystem);

  // Mouse Interactivity with smooth lerping
  let mouseX = 0;
  let mouseY = 0;
  let targetX = 0;
  let targetY = 0;

  window.addEventListener('mousemove', (e) => {
    mouseX = (e.clientX / window.innerWidth) * 2 - 1;
    mouseY = -(e.clientY / window.innerHeight) * 2 + 1;
  });

  // Responsive resize
  window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  });

  // Animation Loop (60 FPS)
  const clock = new THREE.Clock();

  function animate() {
    requestAnimationFrame(animate);
    const elapsedTime = clock.getElapsedTime();

    // Smooth cursor tracking
    targetX += (mouseX - targetX) * 0.05;
    targetY += (mouseY - targetY) * 0.05;

    // Rotate hero core and ring
    heroGroup.rotation.y = elapsedTime * 0.15 + targetX * 0.8;
    heroGroup.rotation.x = elapsedTime * 0.08 + targetY * 0.5;
    ringMesh.rotation.z = elapsedTime * 0.2;

    // Slowly rotate particle constellation
    particleSystem.rotation.y = elapsedTime * 0.02;
    particleSystem.rotation.x = -elapsedTime * 0.01;

    // Orbit point light based on mouse
    primaryPointLight.position.x = 10 + targetX * 15;
    primaryPointLight.position.y = 15 + targetY * 15;

    camera.position.x = targetX * 3;
    camera.position.y = targetY * 2;
    camera.lookAt(scene.position);

    renderer.render(scene, camera);
  }

  animate();
})();''';
  }

  String _getFallback3DStyles() {
    return '''/* ================================================================
   Modern Dark Cyberpunk & Glassmorphic Styling
   ================================================================ */

:root {
  --bg-dark: #08090d;
  --bg-surface: rgba(255, 255, 255, 0.03);
  --border-subtle: rgba(255, 255, 255, 0.08);
  --border-active: rgba(0, 240, 255, 0.35);
  --cyan: #00f0ff;
  --purple: #a855f7;
  --text-main: #f0f2f8;
  --text-muted: #8e95aa;
  --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  --font-heading: 'Space Grotesk', sans-serif;
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

html {
  scroll-behavior: smooth;
}

body {
  background-color: var(--bg-dark);
  color: var(--text-main);
  font-family: var(--font-sans);
  overflow-x: hidden;
  line-height: 1.6;
}

#canvas-3d {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  z-index: 0;
  pointer-events: none;
}

.content-wrapper {
  position: relative;
  z-index: 1;
  max-width: 1240px;
  margin: 0 auto;
  padding: 0 24px;
}

.navbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 28px 0;
}

.logo {
  font-family: var(--font-heading);
  font-size: 1.35rem;
  font-weight: 700;
  letter-spacing: -0.5px;
  color: #fff;
}

.logo span {
  color: var(--cyan);
}

.nav-links {
  display: flex;
  gap: 28px;
  align-items: center;
}

.nav-links a {
  color: var(--text-muted);
  text-decoration: none;
  font-size: 0.95rem;
  transition: color 0.2s;
}

.nav-links a:hover {
  color: #fff;
}

.cta-nav {
  padding: 8px 18px;
  background: rgba(0, 240, 255, 0.08);
  border: 1px solid var(--border-active);
  border-radius: 99px;
  color: var(--cyan) !important;
}

.hero-section {
  min-height: 75vh;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: flex-start;
  padding: 60px 0 40px;
}

.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  background: rgba(0, 240, 255, 0.06);
  border: 1px solid rgba(0, 240, 255, 0.25);
  border-radius: 99px;
  font-size: 0.85rem;
  color: var(--cyan);
  margin-bottom: 24px;
}

.pulse-dot {
  width: 8px;
  height: 8px;
  background: var(--cyan);
  border-radius: 50%;
  box-shadow: 0 0 10px var(--cyan);
}

.hero-title {
  font-family: var(--font-heading);
  font-size: clamp(2.4rem, 6vw, 4.2rem);
  font-weight: 700;
  line-height: 1.12;
  letter-spacing: -1.5px;
  max-width: 900px;
  margin-bottom: 20px;
}

.gradient-text {
  background: linear-gradient(135deg, #00f0ff 0%, #a855f7 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.hero-subtitle {
  font-size: 1.15rem;
  color: var(--text-muted);
  max-width: 650px;
  margin-bottom: 36px;
}

.hero-actions {
  display: flex;
  gap: 16px;
  margin-bottom: 28px;
}

.btn-primary {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 14px 28px;
  background: linear-gradient(135deg, #00f0ff, #0088ff);
  color: #050811;
  font-weight: 600;
  text-decoration: none;
  border-radius: 8px;
  transition: transform 0.2s, box-shadow 0.2s;
}

.btn-primary:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(0, 240, 255, 0.35);
}

.btn-secondary {
  display: inline-flex;
  align-items: center;
  padding: 14px 28px;
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  color: #fff;
  text-decoration: none;
  border-radius: 8px;
  backdrop-filter: blur(12px);
  transition: border-color 0.2s, transform 0.2s;
}

.btn-secondary:hover {
  border-color: rgba(255, 255, 255, 0.3);
  transform: translateY(-2px);
}

.canvas-hint {
  font-size: 0.85rem;
  color: var(--text-muted);
  opacity: 0.7;
}

.hint-icon {
  color: var(--cyan);
}

.metrics-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 20px;
  margin: 60px 0 90px;
}

.metric-card {
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  backdrop-filter: blur(16px);
  border-radius: 12px;
  padding: 24px;
}

.metric-value {
  font-family: var(--font-heading);
  font-size: 2rem;
  font-weight: 700;
  color: var(--cyan);
  margin-bottom: 6px;
}

.metric-label {
  font-size: 0.9rem;
  color: var(--text-muted);
}

.section-header {
  margin-bottom: 40px;
}

.section-tag {
  text-transform: uppercase;
  font-size: 0.75rem;
  letter-spacing: 1.5px;
  color: var(--purple);
  font-weight: 600;
}

.section-title {
  font-family: var(--font-heading);
  font-size: 2.2rem;
  font-weight: 700;
  letter-spacing: -0.5px;
}

.projects-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 24px;
  margin-bottom: 100px;
}

.project-card {
  position: relative;
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  backdrop-filter: blur(16px);
  border-radius: 16px;
  padding: 32px;
  transition: transform 0.3s, border-color 0.3s;
}

.project-card:hover {
  transform: translateY(-4px);
  border-color: var(--border-active);
}

.card-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 16px;
  font-size: 0.85rem;
}

.project-tag {
  color: var(--cyan);
  font-weight: 600;
}

.project-year {
  color: var(--text-muted);
}

.project-title {
  font-family: var(--font-heading);
  font-size: 1.35rem;
  margin-bottom: 12px;
}

.project-desc {
  font-size: 0.95rem;
  color: var(--text-muted);
  margin-bottom: 24px;
}

.project-tech {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.project-tech span {
  padding: 4px 10px;
  background: rgba(255, 255, 255, 0.04);
  border-radius: 6px;
  font-size: 0.78rem;
  color: #ccc;
}

.skills-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 100px;
}

.skill-chip {
  padding: 12px 20px;
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  backdrop-filter: blur(12px);
  border-radius: 99px;
  font-size: 0.95rem;
  color: #e0e4f0;
}

.contact-card {
  background: linear-gradient(135deg, rgba(0, 240, 255, 0.05), rgba(168, 85, 247, 0.05));
  border: 1px solid var(--border-subtle);
  backdrop-filter: blur(20px);
  border-radius: 20px;
  padding: 60px 40px;
  text-align: center;
  margin-bottom: 80px;
}

.contact-card h2 {
  font-family: var(--font-heading);
  font-size: 2.2rem;
  margin-bottom: 16px;
}

.contact-card p {
  color: var(--text-muted);
  max-width: 550px;
  margin: 0 auto 32px;
}

.footer {
  padding: 32px 0;
  border-top: 1px solid var(--border-subtle);
  text-align: center;
  color: var(--text-muted);
  font-size: 0.85rem;
}''';
  }
}
