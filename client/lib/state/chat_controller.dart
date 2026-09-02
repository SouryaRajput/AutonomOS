import 'dart:io';
import 'package:flutter/material.dart';
import '../core/utils/message_sanitizer.dart';
import '../models/conversation.dart';
import '../repositories/conversation_repository.dart';
import '../services/inference_service.dart';
import '../services/local_workspace_auditor.dart';
import 'app_state.dart';

class ChatController extends ChangeNotifier {
  final ConversationRepository repository;
  final String projectId;
  final AppState? appState;
  final InferenceService _inferenceService = InferenceService();

  ChatConversation? _conversation;
  ChatConversation? get conversation => _conversation;

  List<ChatMessage> get messages => _conversation?.messages ?? [];

  bool _isLoading = false;
  bool get isLoading => _isLoading;

  bool _isSending = false;
  bool get isSending => _isSending;

  String _currentActivityTitle = '';
  String get currentActivityTitle => _currentActivityTitle;
  String get activeStage => _currentActivityTitle;

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
    if (initialConversation != null) {
      _conversation = initialConversation;
    } else {
      loadActiveConversation();
    }
  }

  Future<void> loadActiveConversation() async {
    _isLoading = true;
    notifyListeners();
    try {
      _conversation = await repository.getActiveConversation(projectId);
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

    // 1. Optimistically append user message in compact bubble
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

    _isSending = true;
    _currentActivityTitle = 'Exploring workspace…';
    _currentActivitySubtitle = 'Reading project structure and relevant files';
    _errorMessage = null;
    notifyListeners();

    // 2. Prepare clean message history for real LLM inference
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
      _isSending = false;
      _currentActivityTitle = '';
      _currentActivitySubtitle = '';
      notifyListeners();
      return;
    }

    String finalContent = '';

    try {
      final activePath = appState?.activeWorkingPath ?? '';
      final projectName = appState?.selectedProject?.name;

      // Stage 1: Inspect workspace and read repository state
      _currentActivityTitle = 'Inspecting workspace…';
      _currentActivitySubtitle = 'Reading project structure and relevant files';
      notifyListeners();

      Map<String, dynamic> auditResult = {};
      if (activePath.isNotEmpty) {
        try {
          auditResult = await LocalWorkspaceAuditor.auditWorkspace(activePath, requestedObjective: cleanPrompt);
        } catch (e) {
          auditResult = {'status': 'SKIPPED', 'error': e.toString()};
        }
      }

      String? pmapMarkdown;
      final scannedFiles = <String>[];

      if (activePath.isNotEmpty) {
        try {
          final pmapFile = File('$activePath/.autonomos/project-map.md');
          if (pmapFile.existsSync()) {
            pmapMarkdown = pmapFile.readAsStringSync();
          }

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
        } catch (_) {}
      }

      // Stage 2: Preparing execution plan
      _currentActivityTitle = 'Preparing execution plan…';
      _currentActivitySubtitle = 'Breaking the goal into dependent tasks';
      notifyListeners();

      final result = await _inferenceService.generateCompletion(
        baseUrl: activeProv['baseUrl'] as String? ?? '',
        apiKey: activeProv['apiKey'] as String? ?? '',
        model: activeProv['model'] as String? ?? '',
        messages: cleanHistory,
        activeWorkingPath: activePath,
        projectName: projectName,
        projectMapMarkdown: pmapMarkdown,
        scannedFiles: scannedFiles,
      );

      final rawAiResponse = (result['content'] as String? ?? '').trim();
      final promptTokens = result['promptTokens'] as int? ?? 0;
      final completionTokens = result['completionTokens'] as int? ?? 0;

      // Update token telemetry
      if (promptTokens > 0 || completionTokens > 0) {
        appState?.recordTokenUsage(promptTokens, completionTokens);
      }

      // 3. Assemble natural, conversational Manager response
      final activityBuffer = StringBuffer();
      final inspectionStatement = (auditResult['status'] == 'INCREMENTAL_UPDATE')
          ? 'I inspected the workspace and identified ${auditResult['total_changed']} modified files.'
          : 'I inspected the workspace and verified the active repository structure.';

      // Clean LLM response (strip internal tool calls, robotic checklists, context file dumps, and debug telemetry)
      final extracted = MessageSanitizer.extractUserFacingNarrative(rawAiResponse);
      var cleanAi = extracted.userFacingNarrative;

      final alreadyMentionsInspection = cleanAi.toLowerCase().contains('inspected the workspace') ||
          cleanAi.toLowerCase().contains('inspected your workspace') ||
          cleanAi.toLowerCase().contains('analyzed the project');

      if (!alreadyMentionsInspection && auditResult['status'] != 'SKIPPED') {
        activityBuffer.writeln(inspectionStatement);
      }

      if (cleanAi.isNotEmpty) {
        if (activityBuffer.isNotEmpty) activityBuffer.writeln();
        activityBuffer.writeln(cleanAi);
      } else {
        if (activityBuffer.isNotEmpty) activityBuffer.writeln();
        activityBuffer.writeln('I’ve prepared an execution plan and activated the required specialists to begin the investigation.');
      }

      finalContent = activityBuffer.toString().trim();
    } catch (err) {
      finalContent = '⚠️ Inference Error from "${activeProv['name'] ?? activeProv['baseUrl']}":\n\n'
          '$err\n\n'
          'Please verify your API key, base URL, and model name in Settings.';
    }

    // 4. Append natural conversational Manager message
    final now = DateTime.now().toIso8601String();
    final managerMsg = ChatMessage(
      id: 'msg-${DateTime.now().millisecondsSinceEpoch + 1}',
      conversationId: _conversation!.id,
      messageType: MessageType.managerMessage,
      content: finalContent,
      sender: 'Manager',
      timestamp: now,
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
