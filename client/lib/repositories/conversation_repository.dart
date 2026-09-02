import '../models/conversation.dart';
import '../services/api_client.dart';

class ConversationRepository {
  final AutonomOSApiClient _api;

  ConversationRepository(this._api);

  Future<ChatConversation> getActiveConversation(String projectId) =>
      _api.getOrCreateActiveConversation(projectId);

  Future<ChatConversation> createConversation(String projectId, {String title = 'New Conversation'}) =>
      _api.createConversation(projectId, title: title);

  Future<ChatConversation> getConversation(String conversationId) =>
      _api.getConversation(conversationId);

  Future<List<ChatConversation>> listConversations(String projectId) =>
      _api.listConversations(projectId);

  Future<List<ChatConversation>> getConversations(String projectId) =>
      _api.listConversations(projectId);

  Future<ChatConversation> renameConversation(String conversationId, String newTitle) =>
      _api.renameConversation(conversationId, newTitle);

  Future<void> deleteConversation(String conversationId) =>
      _api.deleteConversation(conversationId);

  Future<List<ChatMessage>> sendMessage({
    required String conversationId,
    required String content,
    bool dispatchManager = true,
  }) => _api.postUserMessage(conversationId, content, dispatchManager: dispatchManager);
}
