import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../../core/tokens/tokens.dart';
import '../../services/workspace_diff_service.dart';
import '../observability/diff_viewer.dart';

/// Modal dialog providing full diff inspection for files changed under the active workspace.
class ReviewChangesDialog extends StatefulWidget {
  final WorkspaceDiffService diffService;

  const ReviewChangesDialog({
    super.key,
    required this.diffService,
  });

  static Future<void> show(BuildContext context, WorkspaceDiffService diffService) {
    return showDialog<void>(
      context: context,
      barrierDismissible: true,
      barrierColor: Colors.black54,
      builder: (context) => ReviewChangesDialog(diffService: diffService),
    );
  }

  @override
  State<ReviewChangesDialog> createState() => _ReviewChangesDialogState();
}

class _ReviewChangesDialogState extends State<ReviewChangesDialog> {
  int _selectedFileIndex = 0;
  bool _copiedAll = false;
  bool _copiedCurrent = false;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final diffService = widget.diffService;
    final files = diffService.fileDiffs;

    final screenWidth = MediaQuery.of(context).size.width;
    final screenHeight = MediaQuery.of(context).size.height;
    final dialogWidth = (screenWidth * 0.85).clamp(650.0, 1000.0);
    final dialogHeight = (screenHeight * 0.82).clamp(480.0, 750.0);

    if (_selectedFileIndex >= files.length) {
      _selectedFileIndex = 0;
    }

    final selectedDiff = files.isNotEmpty ? files[_selectedFileIndex] : null;

    return Dialog(
      backgroundColor: isDark ? const Color(0xFF0F121A) : AppTokens.lightSurface,
      insetPadding: const EdgeInsets.symmetric(horizontal: 24, vertical: 24),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(
          color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder,
          width: 1,
        ),
      ),
      child: SizedBox(
        width: dialogWidth,
        height: dialogHeight,
        child: Column(
          children: [
            // 1. Dialog Header
            _buildHeader(context, isDark, diffService),

            const Divider(height: 1, thickness: 1, color: Colors.white12),

            // 2. Dialog Body
            Expanded(
              child: files.isEmpty
                  ? _buildEmptyState(isDark, diffService)
                  : _buildDiffContent(isDark, files, selectedDiff!),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildHeader(BuildContext context, bool isDark, WorkspaceDiffService diffService) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: AppTokens.space20, vertical: AppTokens.space12),
      decoration: BoxDecoration(
        color: isDark ? const Color(0xFF141824) : AppTokens.lightElevated,
        borderRadius: const BorderRadius.vertical(top: Radius.circular(12)),
      ),
      child: Row(
        children: [
          Container(
            padding: const EdgeInsets.all(AppTokens.space8),
            decoration: BoxDecoration(
              color: AppTokens.brandPrimary.withOpacity(0.12),
              borderRadius: BorderRadius.circular(8),
            ),
            child: const Icon(Icons.difference_outlined, size: 20, color: AppTokens.brandPrimary),
          ),
          const SizedBox(width: AppTokens.space12),
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    'Review Changes',
                    style: TextStyle(
                      fontSize: 15,
                      fontWeight: FontWeight.bold,
                      color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
                    ),
                  ),
                  const SizedBox(width: 8),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                    decoration: BoxDecoration(
                      color: isDark ? AppTokens.darkElevated : AppTokens.lightBorder,
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const Icon(Icons.fork_right, size: 12, color: AppTokens.brandPrimary),
                        const SizedBox(width: 2),
                        Text(
                          diffService.branchName,
                          style: TextStyle(
                            fontSize: 11,
                            fontFamily: 'monospace',
                            color: isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 2),
              Text(
                diffService.activePath.isEmpty ? 'No directory selected' : diffService.activePath,
                style: TextStyle(
                  fontSize: 11.5,
                  color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
                  fontFamily: 'monospace',
                ),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ],
          ),
          const Spacer(),

          // Additions / Deletions Summary Pill
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
            decoration: BoxDecoration(
              color: isDark ? const Color(0xFF1B2030) : AppTokens.lightSurface,
              borderRadius: BorderRadius.circular(6),
              border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  '+${diffService.totalAdditions}',
                  style: const TextStyle(
                    fontSize: 12,
                    fontFamily: 'monospace',
                    fontWeight: FontWeight.bold,
                    color: AppTokens.diffAdded,
                  ),
                ),
                const SizedBox(width: 6),
                Text(
                  '-${diffService.totalDeletions}',
                  style: const TextStyle(
                    fontSize: 12,
                    fontFamily: 'monospace',
                    fontWeight: FontWeight.bold,
                    color: AppTokens.diffRemoved,
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  '(${diffService.fileDiffs.length} ${diffService.fileDiffs.length == 1 ? "file" : "files"})',
                  style: TextStyle(
                    fontSize: 11.5,
                    color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
                  ),
                ),
              ],
            ),
          ),

          const SizedBox(width: 12),

          // Copy All Diffs Button
          if (diffService.fileDiffs.isNotEmpty)
            Tooltip(
              message: _copiedAll ? 'Copied all diffs!' : 'Copy all unified diffs to clipboard',
              child: OutlinedButton.icon(
                onPressed: _copyAllDiffs,
                icon: Icon(
                  _copiedAll ? Icons.check : Icons.copy,
                  size: 13,
                  color: _copiedAll ? AppTokens.diffAdded : (isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary),
                ),
                label: Text(
                  _copiedAll ? 'Copied!' : 'Copy All',
                  style: TextStyle(
                    fontSize: 11.5,
                    color: _copiedAll ? AppTokens.diffAdded : (isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary),
                  ),
                ),
                style: OutlinedButton.styleFrom(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                  side: BorderSide(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(6)),
                ),
              ),
            ),

          const SizedBox(width: 8),

          // Refresh Button
          Tooltip(
            message: 'Refresh diffs from workspace',
            child: IconButton(
              icon: Icon(
                Icons.refresh,
                size: 18,
                color: isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary,
              ),
              onPressed: () => diffService.refresh(),
            ),
          ),

          // Close Button
          IconButton(
            icon: Icon(Icons.close, size: 18, color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted),
            onPressed: () => Navigator.of(context).pop(),
          ),
        ],
      ),
    );
  }

  Widget _buildEmptyState(bool isDark, WorkspaceDiffService diffService) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: AppTokens.diffAdded.withOpacity(0.1),
              shape: BoxShape.circle,
            ),
            child: const Icon(Icons.check_circle_outline, size: 48, color: AppTokens.diffAdded),
          ),
          const SizedBox(height: 16),
          Text(
            'Working Directory is Clean',
            style: TextStyle(
              fontSize: 16,
              fontWeight: FontWeight.bold,
              color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
            ),
          ),
          const SizedBox(height: 6),
          Text(
            'No uncommitted code modifications or additions in ${diffService.shortPath}.',
            style: TextStyle(
              fontSize: 13,
              color: isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            'When agents create or edit files under this workspace, file diffs will appear here.',
            style: TextStyle(
              fontSize: 12,
              color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
            ),
          ),
          const SizedBox(height: 16),
          OutlinedButton.icon(
            onPressed: () => diffService.rescanWorkspace(),
            icon: const Icon(Icons.search, size: 14),
            label: const Text('Scan Workspace for Created Files'),
            style: OutlinedButton.styleFrom(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              side: BorderSide(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(6)),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildDiffContent(bool isDark, List<WorkspaceFileDiff> files, WorkspaceFileDiff selectedDiff) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // Left Column: File List
        SizedBox(
          width: 260,
          child: Container(
            decoration: BoxDecoration(
              color: isDark ? const Color(0xFF11141E) : AppTokens.lightElevated,
              border: Border(right: BorderSide(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(14, 10, 14, 8),
                  child: Row(
                    children: [
                      Text(
                        'CHANGED FILES',
                        style: TextStyle(
                          fontSize: 10.5,
                          fontWeight: FontWeight.bold,
                          letterSpacing: 0.8,
                          color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
                        ),
                      ),
                      const Spacer(),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                        decoration: BoxDecoration(
                          color: AppTokens.brandPrimary.withOpacity(0.15),
                          borderRadius: BorderRadius.circular(4),
                        ),
                        child: Text(
                          '${files.length}',
                          style: const TextStyle(
                            fontSize: 10.5,
                            fontWeight: FontWeight.bold,
                            color: AppTokens.brandPrimary,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
                const Divider(height: 1, thickness: 1, color: Colors.white10),
                Expanded(
                  child: ListView.builder(
                    itemCount: files.length,
                    itemBuilder: (context, index) {
                      final item = files[index];
                      final isSelected = index == _selectedFileIndex;
                      return InkWell(
                        onTap: () {
                          setState(() {
                            _selectedFileIndex = index;
                            _copiedCurrent = false;
                          });
                        },
                        child: Container(
                          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                          decoration: BoxDecoration(
                            color: isSelected
                                ? (isDark ? AppTokens.brandPrimary.withOpacity(0.14) : AppTokens.brandPrimary.withOpacity(0.08))
                                : Colors.transparent,
                            border: Border(
                              left: BorderSide(
                                color: isSelected ? AppTokens.brandPrimary : Colors.transparent,
                                width: 3,
                              ),
                            ),
                          ),
                          child: Row(
                            children: [
                              _getFileIcon(item.fileName),
                              const SizedBox(width: 8),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  mainAxisSize: MainAxisSize.min,
                                  children: [
                                    Text(
                                      item.fileName,
                                      style: TextStyle(
                                        fontSize: 12.5,
                                        fontWeight: isSelected ? FontWeight.bold : FontWeight.w500,
                                        color: isSelected
                                            ? (isDark ? Colors.white : AppTokens.brandPrimary)
                                            : (isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary),
                                      ),
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                    ),
                                    if (item.filePath.contains('/'))
                                      Text(
                                        item.filePath,
                                        style: TextStyle(
                                          fontSize: 10,
                                          color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
                                        ),
                                        maxLines: 1,
                                        overflow: TextOverflow.ellipsis,
                                      ),
                                  ],
                                ),
                              ),
                              const SizedBox(width: 6),
                              // Add/Remove count badge
                              Row(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  if (item.additions > 0)
                                    Text(
                                      '+${item.additions}',
                                      style: const TextStyle(
                                        fontSize: 10.5,
                                        fontWeight: FontWeight.bold,
                                        fontFamily: 'monospace',
                                        color: AppTokens.diffAdded,
                                      ),
                                    ),
                                  if (item.additions > 0 && item.deletions > 0)
                                    const SizedBox(width: 4),
                                  if (item.deletions > 0)
                                    Text(
                                      '-${item.deletions}',
                                      style: const TextStyle(
                                        fontSize: 10.5,
                                        fontWeight: FontWeight.bold,
                                        fontFamily: 'monospace',
                                        color: AppTokens.diffRemoved,
                                      ),
                                    ),
                                ],
                              ),
                            ],
                          ),
                        ),
                      );
                    },
                  ),
                ),
              ],
            ),
          ),
        ),

        // Right Column: Diff Viewer
        Expanded(
          child: Container(
            color: isDark ? const Color(0xFF0C0E14) : AppTokens.lightSurface,
            padding: const EdgeInsets.all(AppTokens.space12),
            child: Column(
              children: [
                // File Header Bar inside right column
                Container(
                  margin: const EdgeInsets.only(bottom: AppTokens.space8),
                  padding: const EdgeInsets.symmetric(horizontal: AppTokens.space12, vertical: AppTokens.space6),
                  decoration: BoxDecoration(
                    color: isDark ? AppTokens.darkSurface : AppTokens.lightElevated,
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
                  ),
                  child: Row(
                    children: [
                      _getFileIcon(selectedDiff.fileName),
                      const SizedBox(width: 8),
                      Text(
                        selectedDiff.filePath,
                        style: TextStyle(
                          fontSize: 12.5,
                          fontFamily: 'monospace',
                          fontWeight: FontWeight.w600,
                          color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
                        ),
                      ),
                      const SizedBox(width: 8),
                      if (selectedDiff.isNew)
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                          decoration: BoxDecoration(
                            color: AppTokens.diffAdded.withOpacity(0.18),
                            borderRadius: BorderRadius.circular(3),
                          ),
                          child: const Text(
                            'NEW',
                            style: TextStyle(fontSize: 9.5, fontWeight: FontWeight.bold, color: AppTokens.diffAdded),
                          ),
                        ),
                      const Spacer(),
                      Tooltip(
                        message: _copiedCurrent ? 'Copied file diff!' : 'Copy this file\'s diff',
                        child: InkWell(
                          onTap: () => _copySingleDiff(selectedDiff),
                          borderRadius: BorderRadius.circular(4),
                          child: Padding(
                            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 3),
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Icon(
                                  _copiedCurrent ? Icons.check : Icons.copy,
                                  size: 13,
                                  color: _copiedCurrent ? AppTokens.diffAdded : (isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary),
                                ),
                                const SizedBox(width: 4),
                                Text(
                                  _copiedCurrent ? 'Copied' : 'Copy Diff',
                                  style: TextStyle(
                                    fontSize: 11,
                                    color: _copiedCurrent ? AppTokens.diffAdded : (isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary),
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

                // Unified Diff View
                Expanded(
                  child: DiffViewer(
                    diffText: selectedDiff.diffText,
                    filename: selectedDiff.filePath,
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _getFileIcon(String fileName) {
    final lower = fileName.toLowerCase();
    if (lower.endsWith('.html') || lower.endsWith('.htm')) {
      return const Icon(Icons.html, size: 16, color: Color(0xFFF97316)); // Orange
    } else if (lower.endsWith('.js') || lower.endsWith('.ts') || lower.endsWith('.mjs')) {
      return const Icon(Icons.javascript, size: 16, color: Color(0xFFFACC15)); // Yellow
    } else if (lower.endsWith('.css') || lower.endsWith('.scss')) {
      return const Icon(Icons.css, size: 16, color: Color(0xFF38BDF8)); // Cyan
    } else if (lower.endsWith('.py')) {
      return const Icon(Icons.code, size: 16, color: Color(0xFF3B82F6)); // Blue
    } else if (lower.endsWith('.dart')) {
      return const Icon(Icons.flutter_dash, size: 16, color: Color(0xFF06B6D4)); // Teal
    } else if (lower.endsWith('.json')) {
      return const Icon(Icons.data_object, size: 16, color: Color(0xFFA855F7)); // Purple
    }
    return const Icon(Icons.insert_drive_file_outlined, size: 16, color: Color(0xFF94A3B8));
  }

  void _copyAllDiffs() {
    final allDiffs = widget.diffService.fileDiffs.map((d) => d.diffText).join('\n\n');
    Clipboard.setData(ClipboardData(text: allDiffs));
    setState(() => _copiedAll = true);
    Future.delayed(const Duration(seconds: 2), () {
      if (mounted) setState(() => _copiedAll = false);
    });
  }

  void _copySingleDiff(WorkspaceFileDiff diff) {
    Clipboard.setData(ClipboardData(text: diff.diffText));
    setState(() => _copiedCurrent = true);
    Future.delayed(const Duration(seconds: 2), () {
      if (mounted) setState(() => _copiedCurrent = false);
    });
  }
}
