enum MessageType {
  userMessage('USER_MESSAGE'),
  managerMessage('MANAGER_MESSAGE'),
  workflowUpdate('WORKFLOW_UPDATE'),
  workerUpdate('WORKER_UPDATE'),
  approvalRequest('APPROVAL_REQUEST'),
  userInputRequest('USER_INPUT_REQUEST'),
  decisionRequest('DECISION_REQUEST'),
  error('ERROR'),
  completion('COMPLETION');

  final String value;
  const MessageType(this.value);

  static MessageType fromString(String val) {
    return MessageType.values.firstWhere(
      (e) => e.value == val,
      orElse: () => MessageType.managerMessage,
    );
  }
}

class ChatMessage {
  final String id;
  final String conversationId;
  final MessageType messageType;
  final String content;
  final String sender;
  final String timestamp;
  final Map<String, dynamic> metadata;
  final String? relatedEventId;

  const ChatMessage({
    required this.id,
    required this.conversationId,
    required this.messageType,
    required this.content,
    required this.sender,
    required this.timestamp,
    this.metadata = const {},
    this.relatedEventId,
  });

  factory ChatMessage.fromJson(Map<String, dynamic> json) {
    return ChatMessage(
      id: json['id'] as String? ?? '',
      conversationId: json['conversation_id'] as String? ?? '',
      messageType: MessageType.fromString(json['message_type'] as String? ?? 'MANAGER_MESSAGE'),
      content: json['content'] as String? ?? '',
      sender: json['sender'] as String? ?? 'Manager',
      timestamp: json['timestamp'] as String? ?? '',
      metadata: (json['metadata'] as Map<String, dynamic>?) ?? const {},
      relatedEventId: json['related_event_id'] as String?,
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'conversation_id': conversationId,
    'message_type': messageType.value,
    'content': content,
    'sender': sender,
    'timestamp': timestamp,
    'metadata': metadata,
    'related_event_id': relatedEventId,
  };
}

class ChatConversation {
  final String id;
  final String projectId;
  final String title;
  final List<ChatMessage> messages;
  final String createdAt;
  final String updatedAt;
  final bool isActive;

  const ChatConversation({
    required this.id,
    required this.projectId,
    required this.title,
    this.messages = const [],
    required this.createdAt,
    required this.updatedAt,
    this.isActive = true,
  });

  factory ChatConversation.fromJson(Map<String, dynamic> json) {
    final rawMsgs = json['messages'] as List<dynamic>? ?? [];
    return ChatConversation(
      id: json['id'] as String? ?? '',
      projectId: json['project_id'] as String? ?? '',
      title: json['title'] as String? ?? '',
      messages: rawMsgs.map((m) => ChatMessage.fromJson(m as Map<String, dynamic>)).toList(),
      createdAt: json['created_at'] as String? ?? '',
      updatedAt: json['updated_at'] as String? ?? '',
      isActive: json['is_active'] as bool? ?? true,
    );
  }

  ChatConversation copyWith({
    String? id,
    String? projectId,
    String? title,
    List<ChatMessage>? messages,
    String? createdAt,
    String? updatedAt,
    bool? isActive,
  }) {
    return ChatConversation(
      id: id ?? this.id,
      projectId: projectId ?? this.projectId,
      title: title ?? this.title,
      messages: messages ?? this.messages,
      createdAt: createdAt ?? this.createdAt,
      updatedAt: updatedAt ?? this.updatedAt,
      isActive: isActive ?? this.isActive,
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'project_id': projectId,
    'title': title,
    'messages': messages.map((m) => m.toJson()).toList(),
    'created_at': createdAt,
    'updated_at': updatedAt,
    'is_active': isActive,
  };
}
