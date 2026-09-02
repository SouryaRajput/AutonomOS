import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../state/app_state.dart';
import '../widgets/custom_card.dart';

class GlobalSearchDialog extends StatefulWidget {
  final AppState appState;

  const GlobalSearchDialog({super.key, required this.appState});

  @override
  State<GlobalSearchDialog> createState() => _GlobalSearchDialogState();
}

class _GlobalSearchDialogState extends State<GlobalSearchDialog> {
  final TextEditingController _queryController = TextEditingController();
  List<Map<String, dynamic>> _results = [];
  bool _isSearching = false;

  @override
  void dispose() {
    _queryController.dispose();
    super.dispose();
  }

  Future<void> _performSearch(String query) async {
    if (query.trim().isEmpty) {
      setState(() => _results = []);
      return;
    }

    setState(() => _isSearching = true);
    final q = query.trim().toLowerCase();

    // Search tasks
    final matchingTasks = widget.appState.tasks
        .where((t) => t.title.toLowerCase().contains(q) || t.objective.toLowerCase().contains(q))
        .map((t) => {
              'id': t.id,
              'kind': 'TASK',
              'title': t.title,
              'snippet': t.objective,
              'route': 'autonomos://tasks/${t.id}',
            })
        .toList();

    // Search artifacts
    final matchingArtifacts = widget.appState.artifacts
        .where((a) => a.relativePath.toLowerCase().contains(q) || a.description.toLowerCase().contains(q))
        .map((a) => {
              'id': a.id,
              'kind': 'ARTIFACT',
              'title': a.relativePath,
              'snippet': a.description,
              'route': 'autonomos://artifacts/${a.id}',
            })
        .toList();

    setState(() {
      _results = [...matchingTasks, ...matchingArtifacts];
      _isSearching = false;
    });
  }

  void _handleSelect(Map<String, dynamic> result) {
    Navigator.of(context).pop();
    final route = result['route'] as String? ?? '';
    widget.appState.deepLinkNavigator.handleDeepLink(route);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    return Dialog(
      backgroundColor: Colors.transparent,
      insetPadding: const EdgeInsets.symmetric(horizontal: 24, vertical: 48),
      child: Container(
        width: 600,
        constraints: const BoxConstraints(maxHeight: 520),
        decoration: BoxDecoration(
          color: isDark ? AppTokens.darkBackground : AppTokens.lightBackground,
          borderRadius: AppTokens.borderRadiusMd,
          border: Border.all(color: Theme.of(context).dividerColor),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withOpacity(0.4),
              blurRadius: 24,
              offset: const Offset(0, 8),
            ),
          ],
        ),
        child: Column(
          children: [
            // Search Input Header
            Padding(
              padding: const EdgeInsets.all(AppTokens.space16),
              child: TextField(
                controller: _queryController,
                autofocus: true,
                decoration: InputDecoration(
                  hintText: 'Search tasks, artifacts, code, and chat...',
                  prefixIcon: const Icon(Icons.search, size: 20),
                  suffixIcon: _isSearching
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: Padding(
                            padding: EdgeInsets.all(12),
                            child: CircularProgressIndicator(strokeWidth: 2),
                          ),
                        )
                      : (_queryController.text.isNotEmpty
                          ? IconButton(
                              icon: const Icon(Icons.clear, size: 18),
                              onPressed: () {
                                _queryController.clear();
                                _performSearch('');
                              },
                            )
                          : null),
                ),
                onChanged: (val) => _performSearch(val),
              ),
            ),
            const Divider(height: 1),

            // Search Results List
            Expanded(
              child: _results.isEmpty
                  ? Center(
                      child: Text(
                        _queryController.text.trim().isEmpty
                            ? 'Type to search across current project...'
                            : 'No matching tasks or artifacts found.',
                        style: theme.textTheme.bodyMedium?.copyWith(color: AppTokens.darkTextMuted),
                      ),
                    )
                  : ListView.separated(
                      padding: const EdgeInsets.all(AppTokens.space12),
                      itemCount: _results.length,
                      separatorBuilder: (_, __) => const SizedBox(height: 6),
                      itemBuilder: (context, index) {
                        final item = _results[index];
                        final isTask = item['kind'] == 'TASK';
                        return ListTile(
                          dense: true,
                          leading: Icon(
                            isTask ? Icons.task_alt : Icons.description_outlined,
                            size: 18,
                            color: isTask ? AppTokens.brandPrimaryLight : AppTokens.purple,
                          ),
                          title: Text(item['title'] ?? '', style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
                          subtitle: Text(
                            item['snippet'] ?? '',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(fontSize: 11),
                          ),
                          trailing: Container(
                            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                            decoration: BoxDecoration(
                              color: (isTask ? AppTokens.brandPrimary : AppTokens.purple).withOpacity(0.12),
                              borderRadius: BorderRadius.circular(4),
                            ),
                            child: Text(
                              item['kind'] ?? '',
                              style: TextStyle(
                                fontSize: 9,
                                fontWeight: FontWeight.bold,
                                color: isTask ? AppTokens.brandPrimaryLight : AppTokens.purple,
                              ),
                            ),
                          ),
                          onTap: () => _handleSelect(item),
                        );
                      },
                    ),
            ),

            // Footer Shortcut Helper
            Container(
              padding: const EdgeInsets.symmetric(horizontal: AppTokens.space16, vertical: AppTokens.space8),
              decoration: BoxDecoration(
                color: isDark ? AppTokens.darkSurface : AppTokens.lightBorder.withOpacity(0.2),
                borderRadius: const BorderRadius.vertical(bottom: Radius.circular(8)),
              ),
              child: Row(
                children: [
                  const Text('Navigation: ', style: TextStyle(fontSize: 11, color: AppTokens.darkTextMuted)),
                  const Text('Click to jump directly to item context', style: TextStyle(fontSize: 11, fontWeight: FontWeight.w500)),
                  const Spacer(),
                  TextButton(
                    onPressed: () => Navigator.of(context).pop(),
                    child: const Text('Close', style: TextStyle(fontSize: 11)),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
