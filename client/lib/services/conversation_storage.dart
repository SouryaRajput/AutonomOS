import 'dart:convert';
import 'dart:io';
import '../models/conversation.dart';

/// Local JSON persistence for all conversation sessions and message history.
/// Ensures conversations survive app restarts, reloads, and window closing until explicitly deleted by the user.
class ConversationStorage {
  ConversationStorage._();

  static File _getStorageFile() {
    final home = Platform.environment['HOME'] ?? '.';
    final dir = Directory('$home/.autonomos');
    if (!dir.existsSync()) {
      try {
        dir.createSync(recursive: true);
      } catch (_) {}
    }
    return File('$home/.autonomos/conversations.json');
  }

  /// Load all saved conversations from disk.
  static List<ChatConversation> loadAllConversations() {
    try {
      final file = _getStorageFile();
      if (file.existsSync()) {
        final content = file.readAsStringSync();
        if (content.trim().isNotEmpty) {
          final data = json.decode(content);
          if (data is List) {
            return data
                .whereType<Map<String, dynamic>>()
                .map((m) => ChatConversation.fromJson(m))
                .toList();
          }
        }
      }
    } catch (_) {}
    return [];
  }

  /// Load conversations for a specific project ID, or all if projectId is empty.
  static List<ChatConversation> loadConversations(String projectId) {
    final all = loadAllConversations();
    if (projectId.isEmpty) return all;
    return all.where((c) => c.projectId == projectId).toList();
  }

  /// Save the full list of conversations to disk.
  static void saveAllConversations(List<ChatConversation> conversations) {
    try {
      final file = _getStorageFile();
      final jsonList = conversations.map((c) => c.toJson()).toList();
      file.writeAsStringSync(json.encode(jsonList), flush: true);
    } catch (_) {}
  }

  /// Upsert a single conversation and its full message history to persistent disk.
  static void saveConversation(ChatConversation conversation) {
    final all = loadAllConversations();
    final index = all.indexWhere((c) => c.id == conversation.id);
    if (index != -1) {
      all[index] = conversation;
    } else {
      all.insert(0, conversation);
    }
    saveAllConversations(all);
  }

  /// Delete a conversation from disk.
  static void deleteConversation(String conversationId) {
    final all = loadAllConversations();
    all.removeWhere((c) => c.id == conversationId);
    saveAllConversations(all);
  }

  /// Rename a conversation on disk.
  static void renameConversation(String conversationId, String newTitle) {
    final all = loadAllConversations();
    final index = all.indexWhere((c) => c.id == conversationId);
    if (index != -1) {
      final existing = all[index];
      all[index] = ChatConversation(
        id: existing.id,
        projectId: existing.projectId,
        title: newTitle.trim(),
        messages: existing.messages,
        createdAt: existing.createdAt,
        updatedAt: DateTime.now().toIso8601String(),
        isActive: existing.isActive,
      );
      saveAllConversations(all);
    }
  }
}
