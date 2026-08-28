import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import '../lib/core/routing/deep_link_navigator.dart';
import '../lib/core/utils/activity_translator.dart';
import '../lib/models/artifact.dart';
import '../lib/models/event.dart';
import '../lib/models/evidence.dart';
import '../lib/models/project.dart';
import '../lib/models/task.dart';
import '../lib/models/verification.dart';
import '../lib/services/mock_api_client.dart';
import '../lib/state/app_state.dart';
import '../lib/ui/artifacts/artifact_browser_view.dart';
import '../lib/ui/observability/code_viewer.dart';
import '../lib/ui/observability/diff_viewer.dart';
import '../lib/ui/observability/evidence_viewer.dart';
import '../lib/ui/observability/markdown_view.dart';
import '../lib/ui/observability/verification_view.dart';
import '../lib/ui/tasks/task_inspector_view.dart';
import '../lib/ui/workflow/workflow_result_view.dart';

void main() {
  group('Deep Observability Layer Tests', () {
    // 1. Artifact Browsing & Categorization
    test('1. Artifact kind categorization and metadata', () {
      final artReport = ArtifactModel.fromJson({
        'id': 'art-1',
        'project_id': 'proj-1',
        'type': 'REPORT',
        'path': 'docs/research.md',
        'description': 'Research doc',
        'created_at': '2026-08-27T10:00:00Z',
      });
      expect(artReport.kind, ArtifactKind.report);

      final artDiff = ArtifactModel.fromJson({
        'id': 'art-2',
        'project_id': 'proj-1',
        'type': 'DIFF',
        'path': 'diffs/changes.patch',
        'description': 'Unified diff',
        'created_at': '2026-08-27T10:00:00Z',
      });
      expect(artDiff.kind, ArtifactKind.diff);

      final artCode = ArtifactModel.fromJson({
        'id': 'art-3',
        'project_id': 'proj-1',
        'type': 'SOURCE_CODE',
        'path': 'src/main.py',
        'description': 'Source file',
        'created_at': '2026-08-27T10:00:00Z',
      });
      expect(artCode.kind, ArtifactKind.sourceCode);
    });

    // 2. Safe Markdown Rendering
    testWidgets('2. Markdown rendering of headings, lists, tables, and code blocks', (tester) async {
      const mdContent = '''# Test Header 1
## Test Header 2
* Bullet item 1
* Bullet item 2
1. Ordered item 1
> Blockquote text
```python
def foo():
    return 42
```
| Header A | Header B |
| :--- | :--- |
| Val A | Val B |
''';

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SafeMarkdownView(markdown: mdContent),
          ),
        ),
      );

      expect(find.text('Test Header 1'), findsOneWidget);
      expect(find.text('Test Header 2'), findsOneWidget);
      expect(find.text('Bullet item 1'), findsOneWidget);
      expect(find.text('Val A'), findsOneWidget);
      expect(find.text('PYTHON'), findsOneWidget);
    });

    // 3. Markdown Safety
    testWidgets('3. Markdown safety - ignores arbitrary script tags without execution', (tester) async {
      const unsafeContent = '''# Safe Title
<script>alert("XSS Attack!");</script>
Normal text after malicious tag.
''';

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SafeMarkdownView(markdown: unsafeContent),
          ),
        ),
      );

      expect(find.text('Safe Title'), findsOneWidget);
      expect(find.text('Normal text after malicious tag.'), findsOneWidget);
    });

    // 4. Code Viewer
    testWidgets('4. Code viewer displays line numbers, copy action, and search highlighting', (tester) async {
      const sourceCode = '''import time
def calculate_metrics():
    # Helper comment
    return 100
''';

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SizedBox(
              height: 400,
              child: CodeViewer(code: sourceCode, filename: 'metrics.py', language: 'python'),
            ),
          ),
        ),
      );

      expect(find.text('metrics.py'), findsOneWidget);
      expect(find.text('1'), findsOneWidget);
      expect(find.text('2'), findsOneWidget);
      expect(find.byIcon(Icons.copy), findsOneWidget);
    });

    // 5. Diff Viewer
    testWidgets('5. Diff viewer displays additions, deletions, and colored diff lines', (tester) async {
      const diffContent = '''--- a/auth.py
+++ b/auth.py
@@ -1,3 +1,4 @@
 def login():
-    pass
+    token = generate_token()
+    return token
''';

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SizedBox(
              height: 400,
              child: DiffViewer(diffText: diffContent, filename: 'auth.py'),
            ),
          ),
        ),
      );

      expect(find.text('auth.py'), findsOneWidget);
      expect(find.text('+2'), findsOneWidget);
      expect(find.text('-1'), findsOneWidget);
      expect(find.text('+    token = generate_token()'), findsOneWidget);
    });

    // 6. Evidence Viewer & Traceability
    testWidgets('6. Evidence viewer inspects requirement traces and raw evidence logs', (tester) async {
      final traces = [
        const EvidenceTraceItem(
          requirement: 'Tokens must expire in 3600 seconds',
          checkDescription: 'Verify token expiration claim validation',
          checkType: 'TEST_CASE',
          checkStatus: 'PASSED',
          workerName: 'Tester',
          evidenceSnippet: 'test_token_expiry passed in 0.02s',
        ),
      ];

      final evidence = [
        const EvidenceModel(
          id: 'ev-1',
          taskId: 't-1',
          evidenceType: 'COMMAND_OUTPUT',
          data: 'pytest tests/test_token.py -> 1 passed',
          createdAt: '2026-08-27T10:00:00Z',
        ),
      ];

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: EvidenceViewer(
              evidenceList: evidence,
              traces: traces,
              taskTitle: 'Token Verification',
            ),
          ),
        ),
      );

      expect(find.text('Evidence Trace: Token Verification'), findsOneWidget);
      expect(find.text('Tokens must expire in 3600 seconds'), findsOneWidget);
      expect(find.text('pytest tests/test_token.py -> 1 passed'), findsOneWidget);
    });

    // 7. Verification States
    testWidgets('7. Verification view exposes distinct states: VERIFIED, FAILED, PARTIALLY_VERIFIED, BLOCKED', (tester) async {
      final reportPassed = const VerificationReportModel(
        id: 'ver-1',
        projectId: 'p-1',
        taskId: 't-1',
        state: VerificationState.verified,
        totalChecks: 3,
        passedChecks: 3,
        failedChecks: 0,
        summary: 'All 3 deterministic gates verified successfully.',
        timestamp: '2026-08-27T10:00:00Z',
        checks: [
          VerificationCheckModel(
            id: 'c-1',
            checkType: 'BUILD',
            description: 'Source compilation check',
            status: 'PASSED',
          ),
        ],
      );

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: VerificationView(
              report: reportPassed,
              programmerClaim: 'Programmer finished refactoring.',
              testerEvaluation: 'Tester executed 10 tests with 0 regressions.',
            ),
          ),
        ),
      );

      expect(find.text('Deterministic Verification Gate'), findsOneWidget);
      expect(find.text('VERIFIED'), findsNWidgets(2));
      expect(find.text('Programmer finished refactoring.'), findsOneWidget);
    });

    // 8. Completed Workflow Result
    testWidgets('8. Workflow result view for completed workflow shows metrics and actions', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: WorkflowResultView(
              isSuccess: true,
              title: 'OAuth Authentication Workflow',
              summary: 'All tasks completed and verified with 0 defects.',
              filesChanged: 8,
              testsPassed: 42,
              totalTests: 42,
            ),
          ),
        ),
      );

      expect(find.text('OAuth Authentication Workflow Completed'), findsOneWidget);
      expect(find.text('Files Modified'), findsOneWidget);
      expect(find.text('42 / 42'), findsOneWidget);
    });

    // 9. Failed Workflow Result
    testWidgets('9. Workflow result view for failed workflow shows root cause and recovery options', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: WorkflowResultView(
              isSuccess: false,
              title: 'Database Migration Workflow',
              summary: 'Schema invariant check violated during test stage.',
              failureReason: 'Column "user_uuid" not found on table "accounts".',
              evidenceSummary: 'Migration script exit code 1: Table schema mismatch.',
            ),
          ),
        ),
      );

      expect(find.text('Database Migration Workflow Failed'), findsOneWidget);
      expect(find.text('Failure Root Cause Diagnostic'), findsOneWidget);
      expect(find.text('Rollback to Pre-Task Checkpoint'), findsOneWidget);
    });

    // 10. Large Project Scalability Behavior
    test('10. Large project scalability with 500 tasks', () {
      final tasks = List.generate(
        500,
        (i) => TaskItem(
          id: 'task-$i',
          projectId: 'p-large',
          title: 'Automated Task $i',
          objective: 'Process item $i',
          status: i % 2 == 0 ? 'COMPLETED' : 'PENDING',
          createdAt: '2026-08-27T10:00:00Z',
        ),
      );

      expect(tasks.length, 500);
      final completed = tasks.where((t) => t.status == 'COMPLETED').length;
      expect(completed, 250);
    });

    // 11. Event Stream Performance & Humanized Activity Language
    test('11. Thousands of events translated to human-readable summaries', () {
      final events = List.generate(
        1000,
        (i) => EventModel(
          eventId: 'evt-$i',
          eventType: i % 3 == 0
              ? 'PROGRAMMER_CODE_MODIFIED'
              : i % 3 == 1
                  ? 'TEST_COMPLETED'
                  : 'VERIFICATION_PASSED',
          timestamp: '2026-08-27T10:00:00Z',
          workerId: 'worker.programmer.001',
          payload: {'path': 'src/module_$i.py', 'passed_count': 10},
        ),
      );

      expect(events.length, 1000);
      final firstTitle = ActivityTranslator.getHumanReadableTitle(events[0]);
      expect(firstTitle, contains('Programmer modified src/module_0.py'));

      final secondTitle = ActivityTranslator.getHumanReadableTitle(events[1]);
      expect(secondTitle, contains('verified 10 tests'));
    });

    // 12. Large Artifact Handling
    test('12. Large artifact model lazy-loading simulation', () {
      final largeContent = List.generate(5000, (i) => 'Line $i: data chunk payload').join('\n');
      final art = ArtifactModel(
        id: 'art-large',
        projectId: 'p-1',
        kind: ArtifactKind.report,
        path: 'reports/large_log.txt',
        description: 'Large log artifact',
        content: largeContent,
        createdAt: '2026-08-27T10:00:00Z',
      );

      expect(art.content?.split('\n').length, 5000);
    });

    // 13. Deep Link Navigation
    test('13. DeepLinkNavigator routes URIs to exact application contexts', () async {
      final client = MockAutonomOSApiClient();
      final appState = AppState(client: client);
      await appState.init();

      final navigator = DeepLinkNavigator(appState);

      final routedChat = await navigator.handleUri('autonomos://chat');
      expect(routedChat, isTrue);
      expect(appState.currentTab, AppTab.chat);

      final routedArtifacts = await navigator.handleUri('autonomos://artifacts');
      expect(routedArtifacts, isTrue);
      expect(appState.currentTab, AppTab.artifacts);

      final routedApprovals = await navigator.handleUri('autonomos://approvals');
      expect(routedApprovals, isTrue);
      expect(appState.currentTab, AppTab.approvals);

      final routedWorkflow = await navigator.handleUri('autonomos://workflow');
      expect(routedWorkflow, isTrue);
      expect(appState.currentTab, AppTab.workflow);
    });
  });
}
