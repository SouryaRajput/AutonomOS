import 'package:flutter_test/flutter_test.dart';
import 'package:autonomos/state/app_state.dart';
import 'package:autonomos/state/chat_controller.dart';
import 'package:autonomos/state/workflow_controller.dart';
import 'package:autonomos/services/mock_api_client.dart';

void main() {
  group('AppState & Controller Integration Tests', () {
    late AppState appState;
    late MockAutonomOSApiClient mockApi;

    setUp(() async {
      mockApi = MockAutonomOSApiClient();
      appState = AppState(client: mockApi);
      await appState.init();
    });

    test('Initializes with default project and manager status', () {
      expect(appState.projects.isNotEmpty, true);
      expect(appState.selectedProject, isNotNull);
      expect(appState.selectedProject!.name, 'AutonomOS Core');
    });

    test('Tab switching and theme toggling', () {
      expect(appState.currentTab, AppTab.home);
      appState.setTab(AppTab.chat);
      expect(appState.currentTab, AppTab.chat);

      final initialTheme = appState.themeMode;
      appState.toggleTheme();
      expect(appState.themeMode != initialTheme, true);
    });

    test('Project creation flow', () async {
      final initialCount = appState.projects.length;
      await appState.createProject(
        name: 'New Microservice',
        rootPath: '/tmp/new_service',
        description: 'Payments engine',
      );
      expect(appState.projects.length, initialCount + 1);
      expect(appState.selectedProject?.name, 'New Microservice');
    });

    test('ChatController message sending flow', () async {
      final chatController = ChatController(
        repository: appState.conversationRepo,
        projectId: appState.selectedProject!.id,
      );

      await chatController.loadActiveConversation();
      expect(chatController.conversation, isNotNull);

      final initialMsgCount = chatController.messages.length;
      await chatController.sendMessage('Implement JWT authentication');

      expect(chatController.messages.length, greaterThan(initialMsgCount));
      expect(chatController.messages.any((m) => m.content.contains('JWT authentication')), true);
    });

    test('WorkflowController loads active plan and tasks', () async {
      final workflowController = WorkflowController(
        repository: appState.workflowRepo,
        projectId: appState.selectedProject!.id,
      );

      await workflowController.loadWorkflowData();
      expect(workflowController.tasks.isNotEmpty, true);
      expect(workflowController.activePlan, isNotNull);
    });
  });
}
