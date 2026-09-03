import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../state/app_state.dart';
import '../../state/chat_controller.dart';
import '../chat/chat_view.dart';
import '../sidebar/conversation_sidebar.dart';
import '../workspace/workspace_explorer_view.dart';

/// Main Claude Code responsive shell hosting collapsible sidebar, central chat canvas, and workspace explorer.
class ClaudeShell extends StatefulWidget {
  final AppState appState;

  const ClaudeShell({super.key, required this.appState});

  @override
  State<ClaudeShell> createState() => _ClaudeShellState();
}

class _ClaudeShellState extends State<ClaudeShell> {
  ChatController? _chatController;
  String? _lastProjectId;
  String? _lastConversationId;

  void _updateChatController() {
    final pid = widget.appState.selectedProject?.id ?? 'default-project';
    final activeConv = widget.appState.activeConversation;
    final cid = activeConv?.id;

    if (_chatController == null || _lastProjectId != pid) {
      _lastProjectId = pid;
      _lastConversationId = cid;
      _chatController = ChatController(
        repository: widget.appState.conversationRepo,
        projectId: pid,
        appState: widget.appState,
        initialConversation: activeConv,
      );
    } else if ((_lastConversationId != cid || _chatController!.conversation?.id != cid) && activeConv != null) {
      _lastConversationId = cid;
      _chatController!.setConversation(activeConv);
    }
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.appState,
      builder: (context, _) {
        _updateChatController();

        if (widget.appState.isLoading && widget.appState.projects.isEmpty) {
          return const Scaffold(
            body: Center(child: CircularProgressIndicator()),
          );
        }

        return Scaffold(
          body: Row(
            children: [
              // 1. Collapsible Left Conversation & Project Sidebar
              if (widget.appState.isSidebarOpen)
                ConversationSidebar(appState: widget.appState),

              // 2. Main Center Canvas (Workforce Chat vs Code Workspace Explorer)
              Expanded(
                child: widget.appState.activeSidebarMode == 1
                    ? WorkspaceExplorerView(appState: widget.appState)
                    : ChatView(
                        appState: widget.appState,
                        controller: _chatController!,
                      ),
              ),
            ],
          ),
        );
      },
    );
  }
}
