import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../../core/tokens/tokens.dart';
import '../../services/folder_picker_service.dart';
import '../../services/workspace_service.dart';
import '../../state/app_state.dart';
import '../../state/chat_controller.dart';
import 'manager_activity_popup.dart';
import 'token_usage_dialog.dart';

/// Context-aware prompt input bar inspired by Cursor & Claude Code.
/// Supports Enter to send, Shift+Enter for multiline, and native folder selection.
class ChatInputBar extends StatefulWidget {
  final AppState appState;
  final ChatController controller;

  const ChatInputBar({
    super.key,
    required this.appState,
    required this.controller,
  });

  @override
  State<ChatInputBar> createState() => _ChatInputBarState();
}

class _ChatInputBarState extends State<ChatInputBar> {
  final TextEditingController _textController = TextEditingController();
  final FocusNode _focusNode = FocusNode();

  void _handleSend() {
    final text = _textController.text.trim();
    if (text.isNotEmpty && !widget.controller.isSending) {
      widget.controller.sendMessage(text);
      _textController.clear();
      _focusNode.requestFocus();
    }
  }

  Future<void> _pickWorkspaceFolder() async {
    final current = widget.appState.activeWorkingPath;
    final selected = await FolderPickerService.pickFolder(initialDirectory: current);
    if (selected != null && selected.isNotEmpty) {
      widget.appState.setWorkingPath(selected);
      try {
        await WorkspaceClientService().setFolder(selected);
        await WorkspaceClientService().runAudit(full: false, trigger: 'WORKSPACE_FOLDER_CHANGED');
      } catch (_) {}
    }
  }

  @override
  void dispose() {
    _textController.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final activeProv = widget.appState.activeProvider;
    final modelName = activeProv?['model'] ?? 'No model';
    final provName = activeProv?['name'] ?? 'Inference';
    final workingPath = widget.appState.activeWorkingPath;
    final shortPath = workingPath.split('/').where((s) => s.isNotEmpty).lastOrNull ?? 'Workspace';

    return Container(
      padding: const EdgeInsets.fromLTRB(AppTokens.space24, AppTokens.space4, AppTokens.space24, AppTokens.space16),
      color: isDark ? AppTokens.darkBg : AppTokens.lightBg,
      child: SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            // 1. Context Status Bar above prompt input
            Container(
              margin: const EdgeInsets.only(bottom: AppTokens.space8),
              padding: const EdgeInsets.symmetric(horizontal: AppTokens.space12, vertical: AppTokens.space6),
              decoration: BoxDecoration(
                color: isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
                borderRadius: AppTokens.borderRadiusMd,
                border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
              ),
              child: Row(
                children: [
                  InkWell(
                    onTap: _pickWorkspaceFolder,
                    borderRadius: AppTokens.borderRadiusXs,
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const Icon(Icons.folder_outlined, size: 14, color: AppTokens.brandPrimary),
                        const SizedBox(width: AppTokens.space6),
                        Text(
                          '$shortPath main',
                          style: TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                            color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
                          ),
                        ),
                        const SizedBox(width: 4),
                        Icon(Icons.unfold_more, size: 12, color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted),
                      ],
                    ),
                  ),
                  const SizedBox(width: AppTokens.space10),
                  const Text('+0 -0', style: TextStyle(fontSize: 11.5, fontFamily: 'monospace', color: AppTokens.diffAdded, fontWeight: FontWeight.bold)),
                  const Spacer(),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: AppTokens.space8, vertical: 2),
                    decoration: BoxDecoration(
                      color: isDark ? AppTokens.darkElevated : AppTokens.lightBorder,
                      borderRadius: AppTokens.borderRadiusXs,
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(
                          'Review Changes',
                          style: TextStyle(fontSize: 11, color: isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary),
                        ),
                        const SizedBox(width: 4),
                        Icon(Icons.keyboard_arrow_down, size: 12, color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted),
                      ],
                    ),
                  ),
                ],
              ),
            ),

            // 2. Chat Input Box with Enter to Send & Shift+Enter for newline
            Container(
              decoration: BoxDecoration(
                color: isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
                borderRadius: AppTokens.borderRadiusMd,
                border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
              ),
              child: Column(
                children: [
                  Focus(
                    onKeyEvent: (node, event) {
                      if (event is KeyDownEvent) {
                        if (event.logicalKey == LogicalKeyboardKey.enter ||
                            event.logicalKey == LogicalKeyboardKey.numpadEnter) {
                          final isShift = HardwareKeyboard.instance.logicalKeysPressed.contains(LogicalKeyboardKey.shiftLeft) ||
                                          HardwareKeyboard.instance.logicalKeysPressed.contains(LogicalKeyboardKey.shiftRight);
                          if (!isShift) {
                            _handleSend();
                            return KeyEventResult.handled;
                          }
                        }
                      }
                      return KeyEventResult.ignored;
                    },
                    child: TextField(
                      controller: _textController,
                      focusNode: _focusNode,
                      minLines: 1,
                      maxLines: 6,
                      style: TextStyle(
                        fontSize: 13.5,
                        color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
                      ),
                      textInputAction: TextInputAction.send,
                      onSubmitted: (_) => _handleSend(),
                      decoration: InputDecoration(
                        hintText: 'Type / for commands  (↵ Enter to send)',
                        hintStyle: TextStyle(
                          fontSize: 13,
                          color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
                        ),
                        isDense: true,
                        filled: false,
                        border: InputBorder.none,
                        enabledBorder: InputBorder.none,
                        focusedBorder: InputBorder.none,
                        contentPadding: const EdgeInsets.fromLTRB(AppTokens.space12, AppTokens.space12, AppTokens.space12, AppTokens.space6),
                      ),
                    ),
                  ),

                  // Quick prompt actions & Submit button
                  Padding(
                    padding: const EdgeInsets.fromLTRB(AppTokens.space10, 0, AppTokens.space10, AppTokens.space8),
                    child: Row(
                      children: [
                        _buildQuickActionChip('/plan', 'Formulate workflow architecture plan', isDark),
                        const SizedBox(width: AppTokens.space6),
                        _buildQuickActionChip('/research', 'Explore codebase & dependencies', isDark),
                        const SizedBox(width: AppTokens.space6),
                        _buildQuickActionChip('/test', 'Run verification test harness', isDark),
                        const Spacer(),
                        Material(
                          color: Colors.transparent,
                          child: InkWell(
                            onTap: _handleSend,
                            borderRadius: AppTokens.borderRadiusSm,
                            child: Container(
                              padding: const EdgeInsets.all(AppTokens.space6),
                              decoration: BoxDecoration(
                                color: AppTokens.brandPrimary,
                                borderRadius: AppTokens.borderRadiusSm,
                              ),
                              child: const Icon(Icons.keyboard_return, size: 14, color: Colors.white),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),

            // 3. Bottom Status Footer
            const SizedBox(height: AppTokens.space8),
            Row(
              children: [
                Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Icon(Icons.add, size: 12, color: AppTokens.darkTextMuted),
                    const SizedBox(width: 2),
                    Text(
                      'Accept edits',
                      style: TextStyle(fontSize: 11.5, color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted),
                    ),
                  ],
                ),
                const Spacer(),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: AppTokens.space8, vertical: 2),
                  decoration: BoxDecoration(
                    color: isDark ? AppTokens.darkSurface : AppTokens.lightBorder,
                    borderRadius: AppTokens.borderRadiusXs,
                    border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Container(
                        width: 6,
                        height: 6,
                        decoration: BoxDecoration(
                          color: activeProv != null ? AppTokens.success : AppTokens.warning,
                          shape: BoxShape.circle,
                        ),
                      ),
                      const SizedBox(width: AppTokens.space6),
                      Text(
                        activeProv != null ? '$provName ($modelName)' : 'No Provider Configured',
                        style: TextStyle(
                          fontSize: 11,
                          fontFamily: 'monospace',
                          color: isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary,
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: AppTokens.space8),
                Material(
                  color: Colors.transparent,
                  child: InkWell(
                    onTap: () => TokenUsageDialog.show(context, widget.appState),
                    borderRadius: AppTokens.borderRadiusXs,
                    hoverColor: isDark ? const Color(0xFF1E293B) : const Color(0xFFF1F5F9),
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: AppTokens.space8, vertical: 2),
                      decoration: BoxDecoration(
                        color: isDark ? AppTokens.darkSurface : AppTokens.lightBorder,
                        borderRadius: AppTokens.borderRadiusXs,
                        border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
                      ),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(
                            Icons.token_outlined,
                            size: 11,
                            color: isDark ? const Color(0xFF38BDF8) : const Color(0xFF0284C7),
                          ),
                          const SizedBox(width: 4),
                          Text(
                            'Tokens today: ${TokenUsageDialog.formatNumber(widget.appState.tokensToday)}',
                            style: TextStyle(
                              fontSize: 11,
                              fontFamily: 'monospace',
                              color: isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildQuickActionChip(String label, String tooltip, bool isDark) {
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: () {
          _textController.text = '$label ';
          _textController.selection = TextSelection.fromPosition(TextPosition(offset: _textController.text.length));
          _focusNode.requestFocus();
        },
        borderRadius: AppTokens.borderRadiusXs,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: AppTokens.space6, vertical: 2),
          decoration: BoxDecoration(
            color: isDark ? AppTokens.darkElevated : AppTokens.lightBorder,
            borderRadius: AppTokens.borderRadiusXs,
          ),
          child: Text(
            label,
            style: TextStyle(
              fontSize: 11,
              fontFamily: 'monospace',
              color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
            ),
          ),
        ),
      ),
    );
  }
}
