import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../services/folder_picker_service.dart';
import '../../services/workspace_service.dart';
import '../../state/app_state.dart';

/// Sleek floating path pill positioned directly above the chat box.
class WorkspacePill extends StatelessWidget {
  final AppState appState;

  const WorkspacePill({super.key, required this.appState});

  String _formatPath(String rawPath) {
    if (rawPath.isEmpty) return 'No directory selected';
    if (rawPath.startsWith('/Users/')) {
      final parts = rawPath.split('/');
      if (parts.length > 3) {
        return '~/${parts.sublist(3).join('/')}';
      }
    }
    return rawPath;
  }

  Future<void> _pickFolder(BuildContext context) async {
    final current = appState.activeWorkingPath;
    final selected = await FolderPickerService.pickFolder(initialDirectory: current);
    if (selected != null && selected.isNotEmpty) {
      appState.setWorkingPath(selected);
      try {
        await WorkspaceClientService().setFolder(selected);
        await WorkspaceClientService().runAudit(full: false, trigger: 'WORKSPACE_FOLDER_CHANGED');
      } catch (_) {}
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final pathDisplay = _formatPath(appState.activeWorkingPath);

    return Container(
      margin: const EdgeInsets.only(bottom: AppTokens.space8, left: AppTokens.space4),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Material(
            color: Colors.transparent,
            child: InkWell(
              onTap: () => _pickFolder(context),
              borderRadius: AppTokens.borderRadiusFull,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: AppTokens.space12, vertical: AppTokens.space6),
                decoration: BoxDecoration(
                  color: isDark ? AppTokens.darkElevated : AppTokens.lightBorderMuted,
                  borderRadius: AppTokens.borderRadiusFull,
                  border: Border.all(
                    color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder,
                    width: 1,
                  ),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Icon(
                      Icons.folder_outlined,
                      size: 14,
                      color: AppTokens.brandPrimary,
                    ),
                    const SizedBox(width: AppTokens.space6),
                    Text(
                      'Working in: ',
                      style: TextStyle(
                        fontSize: 11,
                        color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                    ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 320),
                      child: Text(
                        pathDisplay,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          fontSize: 11,
                          fontFamily: 'monospace',
                          color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ),
                    const SizedBox(width: AppTokens.space6),
                    Icon(
                      Icons.edit_outlined,
                      size: 12,
                      color: isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary,
                    ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
