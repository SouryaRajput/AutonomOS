import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:autonomos/models/conversation.dart';
import 'package:autonomos/models/manager_activity.dart';
import 'package:autonomos/ui/chat/document_stream_view.dart';
import 'package:autonomos/ui/chat/manager_activity_card.dart';

void main() {
  group('Manager Activity & Observability UI Tests', () {
    testWidgets('ManagerActivityCard renders structured activity events and inactive status badge',
        (WidgetTester tester) async {
      final steps = [
        const ManagerActivityStep(
          id: 'step-1',
          title: 'Checking workspace...',
          status: ActivityEventStatus.completed,
        ),
        const ManagerActivityStep(
          id: 'step-2',
          title: 'Project Map loaded',
          status: ActivityEventStatus.completed,
        ),
        const ManagerActivityStep(
          id: 'step-3',
          title: 'Planning work...',
          status: ActivityEventStatus.completed,
          decision: 'SongPitchGraph.tsx is relevant.',
          reason: 'Project Map identifies it as the pitch visualization component.',
          selectedContext: ['SongPitchGraph.tsx', 'SongModePage.tsx'],
        ),
        const ManagerActivityStep(
          id: 'step-4',
          title: 'Workers are disabled',
          status: ActivityEventStatus.disabled,
        ),
      ];

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: ManagerActivityCard(steps: steps),
          ),
        ),
      );

      // Verify Header & Inactive Badge
      expect(find.text('Manager Orchestration'), findsOneWidget);
      expect(find.text('Workers Inactive'), findsOneWidget);

      // Verify Timeline Steps
      expect(find.text('Checking workspace...'), findsOneWidget);
      expect(find.text('Project Map loaded'), findsOneWidget);
      expect(find.text('Planning work...'), findsOneWidget);
      expect(find.text('Workers are disabled'), findsOneWidget);
    });

    testWidgets('Expandable events reveal Decision, Reason, and Selected Context on tap',
        (WidgetTester tester) async {
      final steps = [
        const ManagerActivityStep(
          id: 'step-1',
          title: 'Planning work...',
          status: ActivityEventStatus.completed,
          decision: 'SongPitchGraph.tsx is relevant.',
          reason: 'Project Map identifies it as the pitch visualization component.',
          selectedContext: ['SongPitchGraph.tsx', 'SongModePage.tsx'],
        ),
      ];

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: ManagerActivityCard(steps: steps),
          ),
        ),
      );

      // Details not visible before tapping
      expect(find.text('SongPitchGraph.tsx is relevant.'), findsNothing);

      // Tap to expand step
      await tester.tap(find.text('Planning work...'));
      await tester.pumpAndSettle();

      // Verify Observability Details are revealed
      expect(find.text('Decision: '), findsOneWidget);
      expect(find.text('SongPitchGraph.tsx is relevant.'), findsOneWidget);
      expect(find.text('Reason: '), findsOneWidget);
      expect(find.text('Project Map identifies it as the pitch visualization component.'), findsOneWidget);
      expect(find.text('SongPitchGraph.tsx'), findsOneWidget);
      expect(find.text('SongModePage.tsx'), findsOneWidget);
    });

    testWidgets('Task cards and worker prompts render within inspectable details',
        (WidgetTester tester) async {
      final steps = [
        const ManagerActivityStep(
          id: 'step-1',
          title: 'Created TASK-24 & TASK-25',
          status: ActivityEventStatus.completed,
          tasks: [
            {
              'task_id': 'TASK-24',
              'title': 'Improve pitch graph spacing',
              'worker_type': 'Programmer',
              'worker_prompt': 'Apply CSS spacing adjustments to SongPitchGraph.',
            },
            {
              'task_id': 'TASK-25',
              'title': 'Verify graph spacing and interactions',
              'worker_type': 'Tester',
              'worker_prompt': 'Execute component test suite for pitch graph.',
            },
          ],
        ),
      ];

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: ManagerActivityCard(steps: steps),
          ),
        ),
      );

      // Tap to expand
      await tester.tap(find.text('Created TASK-24 & TASK-25'));
      await tester.pumpAndSettle();

      // Verify Task Cards and Worker Badges
      expect(find.text('Programmer'), findsOneWidget);
      expect(find.text('Improve pitch graph spacing'), findsOneWidget);
      expect(find.text('Tester'), findsOneWidget);
      expect(find.text('Verify graph spacing and interactions'), findsOneWidget);
      expect(find.text('ID: TASK-24 • Workers Disabled'), findsOneWidget);

      // Tap prompt icon
      final promptButtons = find.byIcon(Icons.description_outlined);
      expect(promptButtons, findsNWidgets(2));

      await tester.tap(promptButtons.first);
      await tester.pumpAndSettle();

      // Verify Dialog renders prompt content
      expect(find.text('Apply CSS spacing adjustments to SongPitchGraph.'), findsOneWidget);
      expect(find.text('Close'), findsOneWidget);

      await tester.tap(find.text('Close'));
      await tester.pumpAndSettle();
    });

    testWidgets('DocumentStreamView parses structured event stream and concise Manager response',
        (WidgetTester tester) async {
      final messages = [
        ChatMessage(
          id: 'msg-1',
          conversationId: 'c1',
          sender: 'user',
          content: 'Improve the pitch graph spacing.',
          timestamp: '2026-08-29T10:00:00Z',
          messageType: MessageType.userMessage,
        ),
        ChatMessage(
          id: 'msg-2',
          conversationId: 'c1',
          sender: 'manager',
          content: '''
● Checking workspace...
✓ Project Map loaded
● Planning work...
Decision: "SongPitchGraph.tsx is relevant."
Reason: "Project Map identifies it as the pitch visualization component."
- `SongPitchGraph.tsx`
✓ Created TASK-24 (Programmer)
✓ Created TASK-25 (Tester)
⏸ Workers are disabled

Mapped the project and prepared 2 tasks. Workers are currently disabled.
''',
          timestamp: '2026-08-29T10:00:05Z',
          messageType: MessageType.managerMessage,
        ),
      ];

      final scrollController = ScrollController();

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: DocumentStreamView(
              messages: messages,
              isSending: false,
              scrollController: scrollController,
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      // Verify concise main conversation text
      expect(find.text('Improve the pitch graph spacing.'), findsOneWidget);
      expect(find.text('Mapped the project and prepared 2 tasks. Workers are currently disabled.'), findsOneWidget);

      // Verify Manager Activity Card is embedded
      expect(find.text('Manager Orchestration'), findsOneWidget);
      expect(find.text('Workers Inactive'), findsOneWidget);
      expect(find.text('Checking workspace...'), findsOneWidget);
      expect(find.text('Project Map loaded'), findsOneWidget);
      expect(find.text('Planning work...'), findsOneWidget);
      expect(find.text('Workers are disabled'), findsOneWidget);
    });
  });
}
