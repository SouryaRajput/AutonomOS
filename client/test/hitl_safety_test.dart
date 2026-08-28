import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:client/core/tokens/tokens.dart';
import 'package:client/models/approval.dart';
import 'package:client/models/worker.dart';
import 'package:client/models/policy.dart';
import 'package:client/repositories/approval_repository.dart';
import 'package:client/services/mock_api_client.dart';
import 'package:client/state/app_state.dart';
import 'package:client/state/approval_controller.dart';
import 'package:client/ui/approvals/approvals_view.dart';
import 'package:client/ui/settings/autonomy_settings_view.dart';
import 'package:client/ui/settings/provider_settings_view.dart';
import 'package:client/ui/widgets/emergency_stop_banner.dart';
import 'package:client/ui/widgets/emergency_stop_dialog.dart';
import 'package:client/ui/workforce/worker_manifest_dialog.dart';

void main() {
  group('Stage 16: Human-in-the-Loop & Safety UI Tests', () {
    late MockAutonomOSApiClient mockClient;
    late AppState appState;

    setUp(() {
      mockClient = MockAutonomOSApiClient();
      appState = AppState(client: mockClient);
    });

    testWidgets('1. Approval Center renders descriptive action buttons and details', (tester) async {
      final repo = ApprovalRepository(mockClient);
      final controller = ApprovalController(repository: repo, projectId: 'proj-1');
      await controller.load();

      await tester.pumpWidget(
        MaterialApp(
          theme: ThemeData.dark(),
          home: Scaffold(
            body: ApprovalsView(controller: controller),
          ),
        ),
      );
      await tester.pumpAndSettle();

      // Check header
      expect(find.text('Human-in-the-Loop Approval Center'), findsOneWidget);

      // Check details WHAT, WHY, RISK LEVEL
      expect(find.text('WHAT'), findsWidgets);
      expect(find.text('WHY'), findsWidgets);
      expect(find.text('RISK LEVEL'), findsWidgets);

      // Check non-ambiguous action button
      expect(find.text('Approve Deletion'), findsOneWidget);
      expect(find.text('Reject Request'), findsWidgets);
    });

    testWidgets('2. User Input Requests provide structured option chips and free text', (tester) async {
      final repo = ApprovalRepository(mockClient);
      final controller = ApprovalController(repository: repo, projectId: 'proj-1');
      await controller.load();

      await tester.pumpWidget(
        MaterialApp(
          theme: ThemeData.dark(),
          home: Scaffold(
            body: ApprovalsView(controller: controller),
          ),
        ),
      );
      await tester.pumpAndSettle();

      // Check user input question
      expect(find.text('Workforce Input Requests (1)'), findsOneWidget);
      expect(find.text('Which database engine should be used for user session storage?'), findsOneWidget);

      // Structured database option chips
      expect(find.text('PostgreSQL'), findsOneWidget);
      expect(find.text('SQLite'), findsOneWidget);

      // Tap SQLite chip and verify input populates
      await tester.tap(find.text('SQLite'));
      await tester.pumpAndSettle();

      expect(find.text('SQLite'), findsWidgets);
      expect(find.text('Submit Answer to Manager'), findsOneWidget);
    });

    testWidgets('3. Decision Requests render structured choice buttons', (tester) async {
      final repo = ApprovalRepository(mockClient);
      final controller = ApprovalController(repository: repo, projectId: 'proj-1');
      await controller.load();

      await tester.pumpWidget(
        MaterialApp(
          theme: ThemeData.dark(),
          home: Scaffold(
            body: ApprovalsView(controller: controller),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('Architectural & Product Decisions (1)'), findsOneWidget);
      expect(find.text('Choose OAuth 2.0 PKCE'), findsOneWidget);
      expect(find.text('Choose Session Cookies'), findsOneWidget);
    });

    testWidgets('4. Emergency Stop dialog requires explicit confirmation and reason', (tester) async {
      String? confirmedReason;
      await tester.pumpWidget(
        MaterialApp(
          theme: ThemeData.dark(),
          home: Scaffold(
            body: EmergencyStopConfirmDialog(
              onConfirm: (reason) async {
                confirmedReason = reason;
              },
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('Stop AutonomOS?'), findsOneWidget);
      expect(find.text('Halt Reason'), findsOneWidget);

      // Tap Emergency Stop confirm
      await tester.tap(find.text('Emergency Stop'));
      await tester.pumpAndSettle();

      expect(confirmedReason, isNotNull);
    });

    testWidgets('5. Emergency Stop Banner clearly displays halt state and resume action', (tester) async {
      bool resumed = false;
      bool reviewed = false;

      await tester.pumpWidget(
        MaterialApp(
          theme: ThemeData.dark(),
          home: Scaffold(
            body: EmergencyStopBanner(
              isStopped: true,
              stoppedReason: 'Policy violation on disk space',
              onResume: () => resumed = true,
              onReviewWorkflows: () => reviewed = true,
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('AUTONOMOS STOPPED'), findsOneWidget);
      expect(find.textContaining('Policy violation on disk space'), findsOneWidget);

      await tester.tap(find.text('Resume Workforce'));
      expect(resumed, isTrue);

      await tester.tap(find.text('Review'));
      expect(reviewed, isTrue);
    });

    testWidgets('6. Autonomy Settings displays 3 plain language levels and advanced sliders', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          theme: ThemeData.dark(),
          home: Scaffold(
            body: AutonomySettingsView(appState: appState),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('Supervised Autonomy'), findsOneWidget);
      expect(find.text('Balanced Autonomy (Recommended)'), findsOneWidget);
      expect(find.text('High Autonomy'), findsOneWidget);

      // Expand advanced thresholds
      await tester.tap(find.text('Show Advanced Autonomy Thresholds'));
      await tester.pumpAndSettle();

      expect(find.text('Resource & Safety Limits'), findsOneWidget);
      expect(find.text('Maximum Project Budget Limit'), findsOneWidget);
    });

    testWidgets('7. Provider & OmniRoute settings masks secrets and configures endpoints', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          theme: ThemeData.dark(),
          home: Scaffold(
            body: ProviderSettingsView(appState: appState),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('OmniRoute Dynamic Gateway'), findsOneWidget);
      expect(find.text('OpenRouter Gateway'), findsOneWidget);
      expect(find.text('Local Ollama Runtime'), findsOneWidget);

      // Check masked key
      expect(find.text('sk-or-••••••••9a2f'), findsOneWidget);
      expect(find.text('Test Connection'), findsOneWidget);
    });

    testWidgets('8. Worker Manifest Dialog displays capabilities and authorized permissions', (tester) async {
      final worker = WorkerInfo(
        id: 'worker.programmer',
        name: 'Programmer',
        role: 'Code Synthesis & Patch Specialist',
        description: 'Implements targeted code edits, refactors, and test suites.',
        status: 'IDLE',
        capabilities: ['code_generation', 'patch_application', 'static_analysis'],
      );

      await tester.pumpWidget(
        MaterialApp(
          theme: ThemeData.dark(),
          home: Scaffold(
            body: WorkerManifestDialog(worker: worker),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('Programmer'), findsOneWidget);
      expect(find.text('Advertised Capabilities'), findsOneWidget);
      expect(find.text('code_generation'), findsOneWidget);
      expect(find.text('Authorized Tool Categories'), findsOneWidget);
      expect(find.textContaining('Sandboxed Execution'), findsOneWidget);
    });
  });
}
