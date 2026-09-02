import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../models/conversation.dart';
import '../../services/folder_picker_service.dart';
import '../../services/workspace_service.dart';
import '../../state/app_state.dart';
import '../settings/claude_settings_dialog.dart';

/// Modern agentic sidebar featuring Dual-Mode Switcher (Workforce / Workspace), project tree, and gateway pill.
class ConversationSidebar extends StatefulWidget {
  final AppState appState;

  const ConversationSidebar({super.key, required this.appState});

  @override
  State<ConversationSidebar> createState() => _ConversationSidebarState();
}

class _ConversationSidebarState extends State<ConversationSidebar> {
  final TextEditingController _searchController = TextEditingController();
  String _filter = '';
  int _activeMode = 0; // 0 = Workforce / Cowork, 1 = Code / Workspace

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  Future<void> _openFolder() async {
    final current = widget.appState.activeWorkingPath;
    final selected = await FolderPickerService.pickFolder(initialDirectory: current);
    if (selected != null && selected.isNotEmpty) {
      widget.appState.setWorkingPath(selected);
      try {
        await WorkspaceClientService().setFolder(selected);
      } catch (_) {}
    }
  }

  void _openSettings() {
    showDialog(
      context: context,
      builder: (ctx) => ClaudeSettingsDialog(appState: widget.appState),
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final activeProv = widget.appState.activeProvider;
    final provName = activeProv?['name'] ?? 'Custom Gateway';
    final projectName = widget.appState.selectedProject?.name ?? 'AutonomOS';

    final conversations = widget.appState.conversations.where((c) {
      if (_filter.isEmpty) return true;
      return c.title.toLowerCase().contains(_filter.toLowerCase());
    }).toList();

    return Container(
      width: 250,
      decoration: BoxDecoration(
        color: isDark ? AppTokens.darkSidebar : AppTokens.lightSidebar,
        border: Border(
          right: BorderSide(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
        ),
      ),
      child: Column(
        children: [
          // 1. Top Header Icons (Toggle Sidebar + Search)
          Padding(
            padding: const EdgeInsets.fromLTRB(AppTokens.space12, AppTokens.space12, AppTokens.space12, AppTokens.space6),
            child: Row(
              children: [
                IconButton(
                  icon: const Icon(Icons.dock_outlined, size: 16),
                  tooltip: 'Toggle sidebar',
                  onPressed: () => widget.appState.toggleSidebar(),
                  visualDensity: VisualDensity.compact,
                ),
                IconButton(
                  icon: const Icon(Icons.search, size: 16),
                  tooltip: 'Search sessions',
                  onPressed: () {},
                  visualDensity: VisualDensity.compact,
                ),
                const Spacer(),
                const Icon(Icons.auto_awesome, size: 14, color: AppTokens.brandPrimary),
              ],
            ),
          ),

          // 2. Dual-Mode Switcher Tabs (Workforce vs Code)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: AppTokens.space12, vertical: AppTokens.space6),
            child: Container(
              padding: const EdgeInsets.all(2),
              decoration: BoxDecoration(
                color: isDark ? AppTokens.darkElevated : AppTokens.lightBorder,
                borderRadius: AppTokens.borderRadiusMd,
              ),
              child: Row(
                children: [
                  Expanded(
                    child: InkWell(
                      onTap: () => widget.appState.setActiveSidebarMode(0),
                      borderRadius: AppTokens.borderRadiusSm,
                      child: Container(
                        padding: const EdgeInsets.symmetric(vertical: AppTokens.space6),
                        decoration: BoxDecoration(
                          color: widget.appState.activeSidebarMode == 0 ? (isDark ? AppTokens.darkSurface : Colors.white) : Colors.transparent,
                          borderRadius: AppTokens.borderRadiusSm,
                        ),
                        child: Row(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            Icon(Icons.hub_outlined, size: 13, color: widget.appState.activeSidebarMode == 0 ? AppTokens.brandPrimary : AppTokens.darkTextMuted),
                            const SizedBox(width: 4),
                            Text(
                              'Workforce',
                              style: TextStyle(
                                fontSize: 12,
                                fontWeight: widget.appState.activeSidebarMode == 0 ? FontWeight.w600 : FontWeight.normal,
                                color: widget.appState.activeSidebarMode == 0 ? (isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary) : AppTokens.darkTextMuted,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                  Expanded(
                    child: InkWell(
                      onTap: () => widget.appState.setActiveSidebarMode(1),
                      borderRadius: AppTokens.borderRadiusSm,
                      child: Container(
                        padding: const EdgeInsets.symmetric(vertical: AppTokens.space6),
                        decoration: BoxDecoration(
                          color: widget.appState.activeSidebarMode == 1 ? (isDark ? AppTokens.darkSurface : Colors.white) : Colors.transparent,
                          borderRadius: AppTokens.borderRadiusSm,
                        ),
                        child: Row(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            Icon(Icons.code_outlined, size: 13, color: widget.appState.activeSidebarMode == 1 ? AppTokens.brandPrimary : AppTokens.darkTextMuted),
                            const SizedBox(width: 4),
                            Text(
                              'Code',
                              style: TextStyle(
                                fontSize: 12,
                                fontWeight: widget.appState.activeSidebarMode == 1 ? FontWeight.w600 : FontWeight.normal,
                                color: widget.appState.activeSidebarMode == 1 ? (isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary) : AppTokens.darkTextMuted,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),

          // 3. Action Buttons (+ New & Customize/Settings)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: AppTokens.space12, vertical: AppTokens.space4),
            child: Column(
              children: [
                _buildSidebarActionItem(
                  icon: Icons.add,
                  label: 'New',
                  onTap: () => widget.appState.createNewConversation(),
                  isDark: isDark,
                ),
                _buildSidebarActionItem(
                  icon: Icons.folder_open_outlined,
                  label: 'Open Folder',
                  onTap: _openFolder,
                  isDark: isDark,
                ),
                _buildSidebarActionItem(
                  icon: Icons.tune_outlined,
                  label: 'Customize',
                  onTap: _openSettings,
                  isDark: isDark,
                ),
              ],
            ),
          ),

          const SizedBox(height: AppTokens.space8),

          // 4. Project Section Header
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: AppTokens.space14, vertical: AppTokens.space4),
            child: Row(
              children: [
                Text(
                  projectName,
                  style: TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                    color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
                  ),
                ),
                const Spacer(),
                Material(
                  color: Colors.transparent,
                  child: InkWell(
                    onTap: () => widget.appState.createNewConversation(),
                    borderRadius: AppTokens.borderRadiusXs,
                    child: const Padding(
                      padding: EdgeInsets.all(2.0),
                      child: Icon(Icons.add, size: 14, color: AppTokens.darkTextMuted),
                    ),
                  ),
                ),
                const SizedBox(width: AppTokens.space8),
                const Icon(Icons.swap_vert, size: 14, color: AppTokens.darkTextMuted),
              ],
            ),
          ),

          // 5. Conversations / Threads List
          Expanded(
            child: conversations.isEmpty
                ? Center(
                    child: Text(
                      'No sessions yet.\nClick "+ New" to begin.',
                      textAlign: TextAlign.center,
                      style: TextStyle(fontSize: 11, color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted),
                    ),
                  )
                : ListView.builder(
                    padding: const EdgeInsets.symmetric(horizontal: AppTokens.space8, vertical: AppTokens.space4),
                    itemCount: conversations.length,
                    itemBuilder: (context, index) {
                      final conv = conversations[index];
                      final isSelected = widget.appState.activeConversation?.id == conv.id;

                      return Padding(
                        padding: const EdgeInsets.only(bottom: 2),
                        child: GestureDetector(
                          onSecondaryTapDown: (details) => _showConversationContextMenu(context, conv, details.globalPosition),
                          child: InkWell(
                            onTap: () => widget.appState.selectConversation(conv),
                            onLongPress: () => _showConversationContextMenu(context, conv, Offset.zero),
                            borderRadius: AppTokens.borderRadiusSm,
                            child: Container(
                              padding: const EdgeInsets.symmetric(horizontal: AppTokens.space8, vertical: AppTokens.space6),
                              decoration: BoxDecoration(
                                color: isSelected ? (isDark ? AppTokens.darkElevated : AppTokens.lightBorder) : Colors.transparent,
                                borderRadius: AppTokens.borderRadiusSm,
                              ),
                              child: Row(
                                children: [
                                  Container(
                                    width: 6,
                                    height: 6,
                                    decoration: BoxDecoration(
                                      color: isSelected ? AppTokens.brandPrimary : Colors.transparent,
                                      shape: BoxShape.circle,
                                      border: isSelected ? null : Border.all(color: AppTokens.darkTextMuted, width: 1),
                                    ),
                                  ),
                                  const SizedBox(width: AppTokens.space8),
                                  Expanded(
                                    child: Text(
                                      conv.title,
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                      style: TextStyle(
                                        fontSize: 12.5,
                                        fontWeight: isSelected ? FontWeight.w600 : FontWeight.normal,
                                        color: isSelected
                                            ? (isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary)
                                            : (isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary),
                                      ),
                                    ),
                                  ),
                                  InkWell(
                                    onTap: () => _showConversationContextMenu(context, conv, Offset.zero),
                                    borderRadius: BorderRadius.circular(4),
                                    child: Padding(
                                      padding: const EdgeInsets.all(2),
                                      child: Icon(
                                        Icons.more_horiz,
                                        size: 14,
                                        color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
                                      ),
                                    ),
                                  ),
                                ],
                              ),
                            ),
                          ),
                        ),
                      );
                    },
                  ),
          ),

          // 6. Bottom User / Gateway Status Pill
          Container(
            padding: const EdgeInsets.all(AppTokens.space12),
            decoration: BoxDecoration(
              border: Border(
                top: BorderSide(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
              ),
            ),
            child: InkWell(
              onTap: _openSettings,
              borderRadius: AppTokens.borderRadiusSm,
              child: Row(
                children: [
                  const Icon(Icons.hub_outlined, size: 14, color: AppTokens.brandPrimary),
                  const SizedBox(width: AppTokens.space8),
                  Expanded(
                    child: Text(
                      'shirsh · $provName',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 11.5,
                        fontWeight: FontWeight.w500,
                        color: isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary,
                      ),
                    ),
                  ),
                  const Icon(Icons.settings_outlined, size: 14, color: AppTokens.darkTextMuted),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildSidebarActionItem({
    required IconData icon,
    required String label,
    required VoidCallback onTap,
    required bool isDark,
  }) {
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: AppTokens.borderRadiusSm,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: AppTokens.space8, vertical: AppTokens.space8),
          child: Row(
            children: [
              Icon(icon, size: 15, color: isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary),
              const SizedBox(width: AppTokens.space8),
              Text(
                label,
                style: TextStyle(
                  fontSize: 12.5,
                  fontWeight: FontWeight.w500,
                  color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  void _showConversationContextMenu(BuildContext context, ChatConversation conv, Offset position) async {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final overlay = Overlay.of(context).context.findRenderObject() as RenderBox?;
    if (overlay == null) return;

    final actualPosition = position == Offset.zero
        ? const Offset(120, 200)
        : position;

    final selected = await showMenu<String>(
      context: context,
      position: RelativeRect.fromRect(
        actualPosition & const Size(40, 40),
        Offset.zero & overlay.size,
      ),
      color: isDark ? AppTokens.darkElevated : AppTokens.lightSurface,
      elevation: 8,
      shape: RoundedRectangleBorder(
        borderRadius: AppTokens.borderRadiusMd,
        side: BorderSide(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
      ),
      items: [
        PopupMenuItem<String>(
          value: 'rename',
          height: 36,
          child: Row(
            children: [
              Icon(Icons.edit_outlined, size: 14, color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary),
              const SizedBox(width: 8),
              Text('Rename', style: TextStyle(fontSize: 12, color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary)),
            ],
          ),
        ),
        const PopupMenuDivider(height: 1),
        PopupMenuItem<String>(
          value: 'delete',
          height: 36,
          child: Row(
            children: const [
              Icon(Icons.delete_outline, size: 14, color: AppTokens.danger),
              SizedBox(width: 8),
              Text('Delete', style: TextStyle(fontSize: 12, color: AppTokens.danger)),
            ],
          ),
        ),
      ],
    );

    if (selected == 'rename') {
      _promptRenameConversation(conv);
    } else if (selected == 'delete') {
      _confirmDeleteConversation(conv);
    }
  }

  void _promptRenameConversation(ChatConversation conv) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final controller = TextEditingController(text: conv.title);

    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
        title: Text(
          'Rename Conversation',
          style: TextStyle(
            fontSize: 14,
            fontWeight: FontWeight.w600,
            color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
          ),
        ),
        content: TextField(
          controller: controller,
          autofocus: true,
          style: TextStyle(fontSize: 13, color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary),
          decoration: InputDecoration(
            hintText: 'Enter new conversation name',
            filled: true,
            fillColor: isDark ? AppTokens.darkElevated : AppTokens.lightElevated,
            border: OutlineInputBorder(
              borderRadius: AppTokens.borderRadiusSm,
              borderSide: BorderSide(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
            ),
          ),
          onSubmitted: (val) {
            if (val.trim().isNotEmpty) {
              widget.appState.renameConversation(conv.id, val.trim());
              Navigator.of(ctx).pop();
            }
          },
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Cancel'),
          ),
          ElevatedButton(
            style: ElevatedButton.styleFrom(
              backgroundColor: AppTokens.brandPrimary,
              foregroundColor: Colors.black,
            ),
            onPressed: () {
              final val = controller.text.trim();
              if (val.isNotEmpty) {
                widget.appState.renameConversation(conv.id, val);
              }
              Navigator.of(ctx).pop();
            },
            child: const Text('Save'),
          ),
        ],
      ),
    );
  }

  void _confirmDeleteConversation(ChatConversation conv) {
    final isDark = Theme.of(context).brightness == Brightness.dark;

    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
        title: Text(
          'Delete Conversation?',
          style: TextStyle(
            fontSize: 14,
            fontWeight: FontWeight.w600,
            color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
          ),
        ),
        content: Text(
          'Are you sure you want to delete "${conv.title}"? This action cannot be undone.',
          style: TextStyle(
            fontSize: 12.5,
            color: isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary,
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Cancel'),
          ),
          ElevatedButton(
            style: ElevatedButton.styleFrom(
              backgroundColor: AppTokens.danger,
              foregroundColor: Colors.white,
            ),
            onPressed: () {
              widget.appState.deleteConversation(conv.id);
              Navigator.of(ctx).pop();
            },
            child: const Text('Delete'),
          ),
        ],
      ),
    );
  }
}
