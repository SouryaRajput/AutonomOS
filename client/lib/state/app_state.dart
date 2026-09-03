import 'dart:io';
import 'package:flutter/material.dart';
import '../models/project.dart';
import '../models/task.dart';
import '../models/worker.dart';
import '../models/manager.dart';
import '../models/artifact.dart';
import '../models/verification.dart';
import '../models/conversation.dart';
import '../repositories/project_repository.dart';
import '../repositories/conversation_repository.dart';
import '../repositories/workflow_repository.dart';
import '../repositories/worker_repository.dart';
import '../repositories/approval_repository.dart';
import '../repositories/activity_repository.dart';
import '../repositories/artifact_repository.dart';
import '../core/routing/deep_link_navigator.dart';
import '../services/api_client.dart';
import '../services/http_api_client.dart';
import '../services/mock_api_client.dart';
import '../services/provider_storage.dart';
import '../services/workspace_storage.dart';
import '../services/conversation_storage.dart';

enum AppTab {
  home,
  chat,
  workflow,
  workforce,
  activity,
  artifacts,
  approvals,
  settings,
}

class AppState extends ChangeNotifier {
  final AutonomOSApiClient apiClient;

  late final ProjectRepository projectRepo;
  late final ConversationRepository conversationRepo;
  late final WorkflowRepository workflowRepo;
  late final WorkerRepository workerRepo;
  late final ApprovalRepository approvalRepo;
  late final ActivityRepository activityRepo;
  late final ArtifactRepository artifactRepo;
  late final DeepLinkNavigator deepLinkNavigator;

  AppTab _currentTab = AppTab.chat; // Default to Claude Code chat
  AppTab get currentTab => _currentTab;

  ThemeMode _themeMode = ThemeMode.dark;
  ThemeMode get themeMode => _themeMode;

  Project? _selectedProject;
  Project? get selectedProject => _selectedProject;

  List<Project> _projects = [];
  List<Project> get projects => _projects;

  List<TaskItem> _tasks = [];
  List<TaskItem> get tasks => _tasks;

  List<WorkerInfo> _workers = [];
  List<WorkerInfo> get workers => _workers;

  List<ArtifactModel> _artifacts = [];
  List<ArtifactModel> get artifacts => _artifacts;

  List<ChatConversation> _conversations = [];
  List<ChatConversation> get conversations => _conversations;

  ChatConversation? _activeConversation;
  ChatConversation? get activeConversation => _activeConversation;

  String _activeWorkingPath = WorkspaceStorage.loadActiveWorkspace() ?? '';
  String get activeWorkingPath => _activeWorkingPath.isNotEmpty
      ? _activeWorkingPath
      : (_selectedProject?.rootPath ?? '/Users/shirsh/Downloads/Programming/AutonomOS');

  void setWorkingPath(String path) {
    final cleanPath = path.trim();
    if (cleanPath.isEmpty) return;
    _activeWorkingPath = cleanPath;
    WorkspaceStorage.saveActiveWorkspace(_activeWorkingPath);
    final dirName = cleanPath.split(Platform.pathSeparator).where((s) => s.isNotEmpty).last;
    _selectedProject = Project(
      id: 'proj-${dirName.toLowerCase().replaceAll(RegExp(r'[^a-z0-9]'), '-')}',
      name: dirName,
      description: 'Active project workspace at $cleanPath',
      rootPath: cleanPath,
      status: 'ACTIVE',
      taskCount: 0,
      workerCount: 0,
      createdAt: DateTime.now().toIso8601String(),
      updatedAt: DateTime.now().toIso8601String(),
    );
    notifyListeners();
  }

  ManagerStatusModel? _managerStatus;
  ManagerStatusModel? get managerStatus => _managerStatus;

  bool _isSidebarOpen = true;
  bool get isSidebarOpen => _isSidebarOpen;

  int _activeSidebarMode = 0; // 0 = Workforce / Chat, 1 = Code / Workspace Explorer
  int get activeSidebarMode => _activeSidebarMode;
  void setActiveSidebarMode(int mode) {
    _activeSidebarMode = mode;
    notifyListeners();
  }

  bool _isLoading = false;
  bool get isLoading => _isLoading;

  bool _isEmergencyStopped = false;
  bool get isEmergencyStopped => _isEmergencyStopped;

  String _emergencyStopReason = '';
  String get emergencyStopReason => _emergencyStopReason;

  String? _errorMessage;
  String? get errorMessage => _errorMessage;

  // --- Token Telemetry Metrics ---
  int _totalTokens = 24850;
  int _promptTokens = 18600;
  int _completionTokens = 6250;
  double _estimatedCostUsd = 0.00;

  int get totalTokens => _totalTokens;
  int get promptTokens => _promptTokens;
  int get completionTokens => _completionTokens;
  double get estimatedCostUsd => _estimatedCostUsd;

  // --- User-Configured Inference Providers (Persisted to disk) ---
  List<Map<String, dynamic>> _customProviders = ProviderStorage.loadProviders();
  List<Map<String, dynamic>> get customProviders => _customProviders;

  Map<String, dynamic>? get activeProvider {
    if (_customProviders.isEmpty) return null;
    return _customProviders.firstWhere(
      (p) => p['isDefault'] == true,
      orElse: () => _customProviders.first,
    );
  }

  AppState({AutonomOSApiClient? client})
      : apiClient = client ?? HttpAutonomOSApiClient() {
    projectRepo = ProjectRepository(apiClient);
    conversationRepo = ConversationRepository(apiClient);
    workflowRepo = WorkflowRepository(apiClient);
    workerRepo = WorkerRepository(apiClient);
    approvalRepo = ApprovalRepository(apiClient);
    activityRepo = ActivityRepository(apiClient);
    artifactRepo = ArtifactRepository(apiClient);
    deepLinkNavigator = DeepLinkNavigator(this);

    init();
  }

  Future<void> init() async {
    _isLoading = true;
    notifyListeners();
    try {
      final savedWorkspace = WorkspaceStorage.loadActiveWorkspace();
      if (savedWorkspace != null && savedWorkspace.isNotEmpty && Directory(savedWorkspace).existsSync()) {
        _activeWorkingPath = savedWorkspace;
        final dirName = savedWorkspace.split(Platform.pathSeparator).where((s) => s.isNotEmpty).last;
        _selectedProject = Project(
          id: 'proj-${dirName.toLowerCase().replaceAll(RegExp(r'[^a-z0-9]'), '-')}',
          name: dirName,
          description: 'Active project workspace at $savedWorkspace',
          rootPath: savedWorkspace,
          status: 'ACTIVE',
          taskCount: 0,
          workerCount: 0,
          createdAt: DateTime.now().toIso8601String(),
          updatedAt: DateTime.now().toIso8601String(),
        );
        await _loadProjectContext();
      } else {
        _projects = await projectRepo.getProjects();
        if (_projects.isNotEmpty) {
          _selectedProject = _projects.first;
          _activeWorkingPath = _selectedProject!.rootPath;
          await _loadProjectContext();
        } else {
          await _loadProjectContext();
        }
      }
      _errorMessage = null;
    } catch (e) {
      _errorMessage = 'Failed to load projects: $e';
    } finally {
      _isLoading = false;
      notifyListeners();
    }
  }

  void toggleSidebar() {
    _isSidebarOpen = !_isSidebarOpen;
    notifyListeners();
  }

  void setTab(AppTab tab) {
    if (_currentTab != tab) {
      _currentTab = tab;
      notifyListeners();
    }
  }

  void toggleTheme() {
    _themeMode = _themeMode == ThemeMode.dark ? ThemeMode.light : ThemeMode.dark;
    notifyListeners();
  }

  Future<void> selectProject(Project project) async {
    _selectedProject = project;
    _activeWorkingPath = project.rootPath;
    await _loadProjectContext();
    notifyListeners();
  }

  void updateConversation(ChatConversation updated) {
    final index = _conversations.indexWhere((c) => c.id == updated.id);
    if (index != -1) {
      _conversations[index] = updated;
    } else {
      _conversations.insert(0, updated);
    }
    if (_activeConversation?.id == updated.id) {
      _activeConversation = updated;
    }
    ConversationStorage.saveConversation(updated);
    notifyListeners();
  }

  Future<void> selectConversation(ChatConversation conv) async {
    final existingIndex = _conversations.indexWhere((c) => c.id == conv.id);
    if (existingIndex != -1) {
      _activeConversation = _conversations[existingIndex];
    } else {
      _conversations.insert(0, conv);
      _activeConversation = conv;
    }
    notifyListeners();
  }

  Future<ChatConversation> createNewConversation({String title = 'New Conversation'}) async {
    if (_selectedProject == null) {
      if (_projects.isNotEmpty) {
        _selectedProject = _projects.first;
      } else {
        await createProject(
          name: 'AutonomOS Project',
          rootPath: _activeWorkingPath.isNotEmpty ? _activeWorkingPath : '/Users/shirsh/Downloads/Programming/AutonomOS',
        );
      }
    }

    final now = DateTime.now().toIso8601String();
    final newConv = ChatConversation(
      id: 'conv-${DateTime.now().millisecondsSinceEpoch}',
      projectId: _selectedProject?.id ?? 'proj-default',
      title: title,
      messages: [],
      createdAt: now,
      updatedAt: now,
      isActive: true,
    );

    _conversations.insert(0, newConv);
    _activeConversation = newConv;
    ConversationStorage.saveConversation(newConv);
    notifyListeners();

    try {
      await conversationRepo.createConversation(_selectedProject!.id, title: title);
    } catch (_) {}

    return newConv;
  }

  Future<void> renameConversation(String conversationId, String newTitle) async {
    final cleanTitle = newTitle.trim();
    if (cleanTitle.isEmpty) return;

    final index = _conversations.indexWhere((c) => c.id == conversationId);
    if (index != -1) {
      final updated = ChatConversation(
        id: _conversations[index].id,
        projectId: _conversations[index].projectId,
        title: cleanTitle,
        messages: _conversations[index].messages,
        createdAt: _conversations[index].createdAt,
        updatedAt: DateTime.now().toIso8601String(),
        isActive: _conversations[index].isActive,
      );
      _conversations[index] = updated;
      if (_activeConversation?.id == conversationId) {
        _activeConversation = updated;
      }
      ConversationStorage.renameConversation(conversationId, cleanTitle);
      notifyListeners();
      try {
        await conversationRepo.renameConversation(conversationId, cleanTitle);
      } catch (_) {}
    }
  }

  Future<void> deleteConversation(String conversationId) async {
    _conversations.removeWhere((c) => c.id == conversationId);
    ConversationStorage.deleteConversation(conversationId);

    if (_activeConversation?.id == conversationId) {
      if (_conversations.isNotEmpty) {
        _activeConversation = _conversations.first;
      } else {
        final freshConv = ChatConversation(
          id: 'conv-${DateTime.now().millisecondsSinceEpoch}',
          projectId: _selectedProject?.id ?? 'proj-default',
          title: 'Workforce Chat',
          messages: [],
          createdAt: DateTime.now().toIso8601String(),
          updatedAt: DateTime.now().toIso8601String(),
          isActive: true,
        );
        _conversations.add(freshConv);
        _activeConversation = freshConv;
        ConversationStorage.saveConversation(freshConv);
      }
    }
    notifyListeners();
    try {
      await conversationRepo.deleteConversation(conversationId);
    } catch (_) {}
  }

  Future<void> addCustomProvider({
    required String name,
    required String baseUrl,
    required String apiKey,
    required String model,
    bool isDefault = true,
  }) async {
    final provId = 'prov-${DateTime.now().millisecondsSinceEpoch}';
    final shouldBeDefault = isDefault || _customProviders.isEmpty;
    final newProv = {
      'id': provId,
      'name': name.trim(),
      'baseUrl': baseUrl.trim(),
      'apiKey': apiKey.trim(),
      'model': model.trim(),
      'isDefault': shouldBeDefault,
      'status': apiKey.trim().isNotEmpty ? 'CONFIGURED' : 'READY',
      'isCustom': true,
    };
    if (shouldBeDefault) {
      for (var p in _customProviders) {
        p['isDefault'] = false;
      }
    }
    _customProviders.add(newProv);
    ProviderStorage.saveProviders(_customProviders);
    if (apiKey.trim().isNotEmpty) {
      try {
        await apiClient.setProviderKey(provId, apiKey.trim());
      } catch (_) {}
    }
    notifyListeners();
  }

  Future<void> updateProvider(String providerId, {
    String? name,
    String? baseUrl,
    String? apiKey,
    String? model,
    bool? isDefault,
  }) async {
    final idx = _customProviders.indexWhere((p) => p['id'] == providerId);
    if (idx != -1) {
      if (name != null) _customProviders[idx]['name'] = name.trim();
      if (baseUrl != null) _customProviders[idx]['baseUrl'] = baseUrl.trim();
      if (model != null) _customProviders[idx]['model'] = model.trim();
      if (apiKey != null) {
        _customProviders[idx]['apiKey'] = apiKey.trim();
        _customProviders[idx]['status'] = apiKey.trim().isNotEmpty ? 'CONFIGURED' : 'READY';
        try {
          await apiClient.setProviderKey(providerId, apiKey.trim());
        } catch (_) {}
      }
      if (isDefault == true) {
        for (var p in _customProviders) {
          p['isDefault'] = (p['id'] == providerId);
        }
      }
      ProviderStorage.saveProviders(_customProviders);
      notifyListeners();
    }
  }

  void useProvider(String providerId) {
    for (var p in _customProviders) {
      p['isDefault'] = (p['id'] == providerId);
    }
    ProviderStorage.saveProviders(_customProviders);
    notifyListeners();
  }

  void setDefaultProvider(String providerId) => useProvider(providerId);

  void deleteProvider(String providerId) {
    _customProviders.removeWhere((p) => p['id'] == providerId);
    if (!_customProviders.any((p) => p['isDefault'] == true) && _customProviders.isNotEmpty) {
      _customProviders.first['isDefault'] = true;
    }
    ProviderStorage.saveProviders(_customProviders);
    notifyListeners();
  }

  void deleteCustomProvider(String providerId) => deleteProvider(providerId);

  Future<void> runManagerStep({String? feedback}) async {
    if (_selectedProject == null) return;
    try {
      await apiClient.stepManager(_selectedProject!.id, feedback: feedback);
      notifyListeners();
    } catch (_) {}
  }

  void recordTokenUsage(int prompt, int completion) {
    _promptTokens += prompt;
    _completionTokens += completion;
    _totalTokens = _promptTokens + _completionTokens;
    notifyListeners();
  }

  Future<void> createProject({required String name, required String rootPath, String description = ''}) async {
    _isLoading = true;
    notifyListeners();
    try {
      final p = await projectRepo.createProject(name: name, rootPath: rootPath, description: description);
      _projects.add(p);
      _selectedProject = p;
      _activeWorkingPath = p.rootPath;
      await _loadProjectContext();
      _errorMessage = null;
    } catch (e) {
      _errorMessage = 'Failed to create project: $e';
    } finally {
      _isLoading = false;
      notifyListeners();
    }
  }

  Future<void> loadArtifacts() async {
    if (_selectedProject == null) return;
    try {
      _artifacts = await artifactRepo.listArtifacts(_selectedProject!.id);
      notifyListeners();
    } catch (_) {}
  }

  Future<String?> loadArtifactContent(String artifactId) async {
    try {
      return await artifactRepo.getArtifactContent(artifactId);
    } catch (_) {
      return null;
    }
  }

  Future<VerificationReportModel?> getVerificationReport(String taskId) async {
    try {
      return await artifactRepo.getVerificationReport(taskId);
    } catch (_) {
      return null;
    }
  }

  Future<void> activateEmergencyStop(String reason) async {
    if (_selectedProject == null) return;
    try {
      await apiClient.emergencyStop(_selectedProject!.id, reason: reason);
      _isEmergencyStopped = true;
      _emergencyStopReason = reason;
      notifyListeners();
    } catch (e) {
      _errorMessage = 'Failed to activate emergency stop: $e';
      notifyListeners();
    }
  }

  Future<void> clearEmergencyHalt() async {
    if (_selectedProject == null) return;
    try {
      await apiClient.clearEmergencyStop(_selectedProject!.id);
      _isEmergencyStopped = false;
      _emergencyStopReason = '';
      await _loadProjectContext();
      notifyListeners();
    } catch (e) {
      _errorMessage = 'Failed to clear emergency stop: $e';
      notifyListeners();
    }
  }

  Future<void> _loadProjectContext() async {
    try {
      _workers = await workerRepo.getWorkers();
    } catch (_) {}

    final pid = _selectedProject?.id ?? '';
    // 1. Immediately load local persisted conversations
    final localConvs = ConversationStorage.loadConversations(pid);
    if (localConvs.isNotEmpty) {
      _conversations = localConvs;
      if (_activeConversation == null || !_conversations.any((c) => c.id == _activeConversation?.id)) {
        _activeConversation = _conversations.first;
      }
      notifyListeners();
    }

    if (_selectedProject != null) {
      try {
        _managerStatus = await workflowRepo.getManagerStatus(_selectedProject!.id);
      } catch (_) {}
      try {
        _artifacts = await artifactRepo.listArtifacts(_selectedProject!.id);
      } catch (_) {}
      try {
        _tasks = await workflowRepo.getTasks(_selectedProject!.id);
      } catch (_) {}

      try {
        final remoteConvs = await conversationRepo.getConversations(_selectedProject!.id);
        if (remoteConvs.isNotEmpty) {
          final mergedMap = <String, ChatConversation>{};
          for (final c in _conversations) {
            mergedMap[c.id] = c;
          }
          for (final rc in remoteConvs) {
            final local = mergedMap[rc.id];
            if (local == null) {
              mergedMap[rc.id] = rc;
            } else {
              final msgs = local.messages.length >= rc.messages.length ? local.messages : rc.messages;
              mergedMap[rc.id] = ChatConversation(
                id: rc.id,
                projectId: rc.projectId,
                title: rc.title.isNotEmpty ? rc.title : local.title,
                messages: msgs,
                createdAt: rc.createdAt.isNotEmpty ? rc.createdAt : local.createdAt,
                updatedAt: rc.updatedAt.isNotEmpty ? rc.updatedAt : local.updatedAt,
                isActive: rc.isActive,
              );
            }
          }
          _conversations = mergedMap.values.toList();
          ConversationStorage.saveAllConversations(_conversations);
        }
      } catch (_) {}
    }

    if (_conversations.isEmpty) {
      final initialConv = ChatConversation(
        id: 'conv-${DateTime.now().millisecondsSinceEpoch}',
        projectId: _selectedProject?.id ?? 'proj-default',
        title: 'Workforce Chat',
        messages: [],
        createdAt: DateTime.now().toIso8601String(),
        updatedAt: DateTime.now().toIso8601String(),
        isActive: true,
      );
      _conversations = [initialConv];
      _activeConversation = initialConv;
      ConversationStorage.saveConversation(initialConv);
    } else if (_activeConversation == null || !_conversations.any((c) => c.id == _activeConversation?.id)) {
      _activeConversation = _conversations.first;
    }
    notifyListeners();
  }
}
