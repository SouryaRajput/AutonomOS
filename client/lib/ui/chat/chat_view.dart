import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../../core/tokens/tokens.dart';
import '../../models/conversation.dart';
import '../../state/app_state.dart';
import '../../state/chat_controller.dart';
import 'chat_input_bar.dart';
import 'document_stream_view.dart';

class ChatView extends StatefulWidget {
  final AppState appState;
  final ChatController controller;

  const ChatView({
    super.key,
    required this.appState,
    required this.controller,
  });

  @override
  State<ChatView> createState() => _ChatViewState();
}

class _ChatViewState extends State<ChatView> {
  final ScrollController _scrollController = ScrollController();

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_onControllerUpdate);
  }

  @override
  void didUpdateWidget(covariant ChatView oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.controller != widget.controller) {
      oldWidget.controller.removeListener(_onControllerUpdate);
      widget.controller.addListener(_onControllerUpdate);
    }
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onControllerUpdate);
    _scrollController.dispose();
    super.dispose();
  }

  void _onControllerUpdate() {
    _scrollToBottom();
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 250),
          curve: Curves.easeOut,
        );
      }
    });
  }

  void _copySessionTranscript() {
    final messages = widget.controller.messages;
    if (messages.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Session transcript is empty.'),
          duration: Duration(seconds: 2),
        ),
      );
      return;
    }

    final buffer = StringBuffer();
    final activeConv = widget.appState.activeConversation;
    final projName = widget.appState.selectedProject?.name ?? 'AutonomOS';

    buffer.writeln('# AutonomOS Session Transcript: ${activeConv?.title ?? "Orchestration Session"}');
    buffer.writeln('Project: $projName');
    buffer.writeln('Working Path: ${widget.appState.activeWorkingPath}');
    buffer.writeln('Exported At: ${DateTime.now().toIso8601String()}');
    buffer.writeln('==================================================');
    buffer.writeln();

    for (final msg in messages) {
      final sender = msg.messageType == MessageType.userMessage ? 'USER' : 'MANAGER';
      final ts = msg.timestamp.isNotEmpty ? ' [${msg.timestamp}]' : '';
      buffer.writeln('## $sender$ts');
      buffer.writeln(msg.content);
      buffer.writeln();
    }

    Clipboard.setData(ClipboardData(text: buffer.toString()));
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('Session transcript copied to clipboard'),
        duration: Duration(seconds: 2),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.controller,
      builder: (context, _) {
        final isDark = Theme.of(context).brightness == Brightness.dark;
        final activeConv = widget.appState.activeConversation;
        final projectName = widget.appState.selectedProject?.name ?? 'AutonomOS';

        if (widget.controller.isLoading && widget.controller.messages.isEmpty) {
          return const Center(child: CircularProgressIndicator());
        }

        return Container(
          color: isDark ? AppTokens.darkBg : AppTokens.lightBg,
          child: Column(
            children: [
              // Top Header (Title + Project Badge + Action Icons)
              Container(
                padding: const EdgeInsets.symmetric(horizontal: AppTokens.space20, vertical: AppTokens.space12),
                decoration: BoxDecoration(
                  color: isDark ? AppTokens.darkBg : AppTokens.lightSurface,
                  border: Border(
                    bottom: BorderSide(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
                  ),
                ),
                child: Row(
                  children: [
                    if (!widget.appState.isSidebarOpen) ...[
                      IconButton(
                        icon: const Icon(Icons.menu, size: 16),
                        tooltip: 'Open sidebar',
                        onPressed: () => widget.appState.toggleSidebar(),
                      ),
                      const SizedBox(width: AppTokens.space8),
                    ],
                    Text(
                      activeConv?.title ?? 'Autonomous Engineering Session',
                      style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
                    ),
                    const SizedBox(width: AppTokens.space10),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: AppTokens.space8, vertical: 2),
                      decoration: BoxDecoration(
                        color: isDark ? AppTokens.darkElevated : AppTokens.lightBorder,
                        borderRadius: AppTokens.borderRadiusSm,
                        border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
                      ),
                      child: Text(
                        projectName,
                        style: TextStyle(
                          fontSize: 11,
                          fontFamily: 'monospace',
                          color: isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary,
                        ),
                      ),
                    ),
                    const Spacer(),
                    IconButton(
                      icon: const Icon(Icons.copy_outlined, size: 16),
                      tooltip: 'Copy session transcript',
                      onPressed: _copySessionTranscript,
                    ),
                    IconButton(
                      icon: const Icon(Icons.play_arrow_outlined, size: 17),
                      tooltip: 'Run workforce orchestration',
                      onPressed: () => widget.appState.runManagerStep(),
                    ),
                  ],
                ),
              ),

              // Main Continuous Document Stream
              Expanded(
                child: DocumentStreamView(
                  messages: widget.controller.messages,
                  isSending: widget.controller.isSending,
                  activeStage: widget.controller.activeStage,
                  scrollController: _scrollController,
                ),
              ),

              // Bottom Input & Context Bar
              ChatInputBar(
                appState: widget.appState,
                controller: widget.controller,
              ),
            ],
          ),
        );
      },
    );
  }
}
