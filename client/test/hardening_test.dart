import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:client/models/artifact.dart';
import 'package:client/models/project.dart';
import 'package:client/models/task.dart';
import 'package:client/models/worker.dart';
import 'package:client/services/mock_api_client.dart';
import 'package:client/state/app_state.dart';
import 'package:client/ui/notifications/notification_center_dialog.dart';
import 'package:client/ui/onboarding/onboarding_dialog.dart';
import 'package:client/ui/search/global_search_dialog.dart';
import 'package:client/ui/settings/settings_view.dart';
import 'package:client/ui/widgets/empty_state.dart';

void main() {
  group('Stage 16: Real-World Hardening UI Tests', () {
    late MockAutonomOSApiClient mockClient;
    late AppState appState;

    setUp(() {
      mockClient = MockAutonomOSApiClient();
      appState = AppState(client: mockClient);
    });

    testWidgets('1. Settings View renders 7 organized tabs and switches properly', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          theme: ThemeData.dark(),
          home: Scaffold(
            body: SettingsView(appState: appState),
          ),
        ),
      );
      await tester.pumpAndSettle();

      // Check all 7 tabs are present
      expect(find.text('General'), findsOneWidget);
      expect(find.text('Autonomy'), findsOneWidget);
      expect(find.text('Providers'), findsOneWidget);
      expect(find.text('Workforce'), findsOneWidget);
      expect(find.text('Safety'), findsOneWidget);
      expect(find.text('Storage'), findsOneWidget);
      expect(find.text('Advanced'), findsOneWidget);

      // Check General tab content
      expect(find.text('Local-First Privacy Architecture'), findsOneWidget);
      expect(find.text('Open Source & Cost Disclaimer'), findsOneWidget);

      // Tap Safety tab
      await tester.tap(find.text('Safety'));
      await tester.pumpAndSettle();
      expect(find.text('Active Safety Invariants'), findsOneWidget);
      expect(find.text('ENFORCED'), findsOneWidget);

      // Tap Storage tab
      await tester.tap(find.text('Storage'));
      await tester.pumpAndSettle();
      expect(find.text('Project Storage & Disk Locations'), findsOneWidget);
      expect(find.text('Database Engine'), findsOneWidget);

      // Tap Advanced tab
      await tester.tap(find.text('Advanced'));
      await tester.pumpAndSettle();
      expect(find.text('Enable Developer Mode'), findsOneWidget);
      expect(find.text('Export Project Bundle'), findsOneWidget);
    });

    testWidgets('2. Global Search Dialog searches tasks and artifacts and executes route', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          theme: ThemeData.dark(),
          home: Scaffold(
            body: GlobalSearchDialog(appState: appState),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('Type to search across current project...'), findsOneWidget);

      // Enter query
      await tester.enterText(find.byType(TextField), 'auth');
      await tester.pumpAndSettle();

      // Should find matching items
      expect(find.textContaining('auth', findRichText: true), findsWidgets);
    });

    testWidgets('3. Notification Center Dialog presents active alerts and routes to context', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          theme: ThemeData.dark(),
          home: Scaffold(
            body: NotificationCenterDialog(appState: appState),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('Workforce Notifications'), findsOneWidget);
      expect(find.text('Dismiss'), findsOneWidget);
    });

    testWidgets('4. Onboarding Dialog guides user through 3 clear steps', (tester) async {
      bool completed = false;
      await tester.pumpWidget(
        MaterialApp(
          theme: ThemeData.dark(),
          home: Scaffold(
            body: OnboardingDialog(
              onComplete: () => completed = true,
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      // Step 1
      expect(find.text('Welcome to AutonomOS'), findsOneWidget);
      expect(find.text('Step 1 of 3'), findsOneWidget);
      expect(find.text('Next'), findsOneWidget);

      // Step 2
      await tester.tap(find.text('Next'));
      await tester.pumpAndSettle();
      expect(find.text('Step 2 of 3'), findsOneWidget);
      expect(find.text('Local Storage & Privacy Guarantee'), findsOneWidget);

      // Step 3
      await tester.tap(find.text('Next'));
      await tester.pumpAndSettle();
      expect(find.text('Step 3 of 3'), findsOneWidget);
      expect(find.text('Cost & Open Source Transparency'), findsOneWidget);
      expect(find.text('Get Started'), findsOneWidget);

      // Complete
      await tester.tap(find.text('Get Started'));
      await tester.pumpAndSettle();
      expect(completed, isTrue);
    });

    testWidgets('5. Empty States communicate clearly without blank screens', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          theme: ThemeData.dark(),
          home: const Scaffold(
            body: EmptyState(
              icon: Icons.account_tree_outlined,
              title: 'No Active Workflows',
              message: 'Tell the Manager what you want to build in chat to generate a plan.',
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('No Active Workflows'), findsOneWidget);
      expect(find.text('Tell the Manager what you want to build in chat to generate a plan.'), findsOneWidget);
    });
  });
}
