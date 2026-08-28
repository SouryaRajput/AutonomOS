import 'package:flutter_test/flutter_test.dart';
import 'package:autonomos/models/project.dart';
import 'package:autonomos/models/task.dart';
import 'package:autonomos/models/worker.dart';
import 'package:autonomos/models/conversation.dart';
import 'package:autonomos/models/workflow.dart';
import 'package:autonomos/models/approval.dart';

void main() {
  group('Flutter Client Models Serialization Tests', () {
    test('Project JSON roundtrip', () {
      final p = Project(
        id: 'p-1',
        name: 'AutonomOS Core',
        rootPath: '/tmp/core',
        createdAt: '2026-08-27T00:00:00Z',
        updatedAt: '2026-08-27T00:00:00Z',
      );
      final json = p.toJson();
      final p2 = Project.fromJson(json);
      expect(p2.id, 'p-1');
      expect(p2.name, 'AutonomOS Core');
    });

    test('TaskItem JSON roundtrip', () {
      final t = TaskItem(
        id: 't-1',
        projectId: 'p-1',
        title: 'Run Tests',
        createdAt: '2026-08-27T00:00:00Z',
        status: 'READY',
      );
      final json = t.toJson();
      final t2 = TaskItem.fromJson(json);
      expect(t2.id, 't-1');
      expect(t2.status, 'READY');
    });

    test('ChatMessage JSON roundtrip', () {
      final m = ChatMessage(
        id: 'm-1',
        conversationId: 'c-1',
        messageType: MessageType.userMessage,
        content: 'Build feature',
        sender: 'user',
        timestamp: '2026-08-27T00:00:00Z',
      );
      final json = m.toJson();
      final m2 = ChatMessage.fromJson(json);
      expect(m2.id, 'm-1');
      expect(m2.messageType, MessageType.userMessage);
    });

    test('ApprovalItem JSON roundtrip', () {
      final a = ApprovalItem(
        id: 'app-1',
        projectId: 'p-1',
        taskId: 't-1',
        workerId: 'worker.programmer',
        action: 'filesystem.delete',
        riskLevel: 'HIGH',
        createdAt: '2026-08-27T00:00:00Z',
      );
      final json = a.toJson();
      final a2 = ApprovalItem.fromJson(json);
      expect(a2.id, 'app-1');
      expect(a2.riskLevel, 'HIGH');
    });
  });
}
