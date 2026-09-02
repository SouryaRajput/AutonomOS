import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../models/artifact.dart';
import '../observability/code_viewer.dart';
import '../observability/diff_viewer.dart';
import '../observability/markdown_view.dart';
import '../widgets/custom_card.dart';
import '../widgets/empty_state.dart';

class ArtifactBrowserView extends StatefulWidget {
  final List<ArtifactModel> artifacts;
  final Future<String?> Function(String artifactId)? onLoadContent;

  const ArtifactBrowserView({
    super.key,
    required this.artifacts,
    this.onLoadContent,
  });

  @override
  State<ArtifactBrowserView> createState() => _ArtifactBrowserViewState();
}

class _ArtifactBrowserViewState extends State<ArtifactBrowserView> {
  ArtifactKind? _selectedKindFilter;
  ArtifactModel? _selectedArtifact;
  String? _loadedContent;
  bool _isLoadingContent = false;

  List<ArtifactModel> get _filteredArtifacts {
    if (_selectedKindFilter == null) return widget.artifacts;
    return widget.artifacts.where((a) => a.kind == _selectedKindFilter).toList();
  }

  void _handleSelectArtifact(ArtifactModel artifact) async {
    setState(() {
      _selectedArtifact = artifact;
      _loadedContent = artifact.content;
    });

    if (_loadedContent == null && widget.onLoadContent != null) {
      setState(() {
        _isLoadingContent = true;
      });
      final content = await widget.onLoadContent!(artifact.id);
      if (mounted) {
        setState(() {
          _loadedContent = content;
          _isLoadingContent = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    return LayoutBuilder(
      builder: (context, constraints) {
        final isSplit = constraints.maxWidth > 800;

        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Header & Filter Bar
            Padding(
              padding: const EdgeInsets.fromLTRB(AppTokens.space24, AppTokens.space24, AppTokens.space24, AppTokens.space12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Artifact Browser', style: theme.textTheme.headlineMedium),
                  const SizedBox(height: AppTokens.space4),
                  Text(
                    'Browse generated reports, diffs, test logs, code files, and evidence',
                    style: theme.textTheme.bodyMedium,
                  ),
                  const SizedBox(height: AppTokens.space16),
                  SingleChildScrollView(
                    scrollDirection: Axis.horizontal,
                    child: Row(
                      children: [
                        _buildFilterChip('All Types', isSelected: _selectedKindFilter == null, onSelected: () {
                          setState(() => _selectedKindFilter = null);
                        }),
                        const SizedBox(width: AppTokens.space8),
                        _buildFilterChip('Reports', isSelected: _selectedKindFilter == ArtifactKind.report, onSelected: () {
                          setState(() => _selectedKindFilter = ArtifactKind.report);
                        }),
                        const SizedBox(width: AppTokens.space8),
                        _buildFilterChip('Code & Diffs', isSelected: _selectedKindFilter == ArtifactKind.sourceCode || _selectedKindFilter == ArtifactKind.diff, onSelected: () {
                          setState(() => _selectedKindFilter = ArtifactKind.sourceCode);
                        }),
                        const SizedBox(width: AppTokens.space8),
                        _buildFilterChip('Test Reports', isSelected: _selectedKindFilter == ArtifactKind.testReport, onSelected: () {
                          setState(() => _selectedKindFilter = ArtifactKind.testReport);
                        }),
                        const SizedBox(width: AppTokens.space8),
                        _buildFilterChip('Memory & Docs', isSelected: _selectedKindFilter == ArtifactKind.memoryDoc, onSelected: () {
                          setState(() => _selectedKindFilter = ArtifactKind.memoryDoc);
                        }),
                      ],
                    ),
                  ),
                ],
              ),
            ),

            // Content Area (Split View on Wide screens, Stacked on Narrow)
            Expanded(
              child: isSplit
                  ? Row(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        // Left List
                        SizedBox(
                          width: 320,
                          child: _buildArtifactsList(),
                        ),
                        const VerticalDivider(width: 1),
                        // Right Viewer
                        Expanded(
                          child: _buildArtifactPreviewPane(),
                        ),
                      ],
                    )
                  : _selectedArtifact == null
                      ? _buildArtifactsList()
                      : Column(
                          children: [
                            Container(
                              padding: const EdgeInsets.symmetric(horizontal: AppTokens.space16, vertical: AppTokens.space8),
                              color: isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
                              child: Row(
                                children: [
                                  IconButton(
                                    icon: const Icon(Icons.arrow_back, size: 18),
                                    onPressed: () => setState(() => _selectedArtifact = null),
                                  ),
                                  const SizedBox(width: AppTokens.space8),
                                  Expanded(
                                    child: Text(
                                      _selectedArtifact!.path,
                                      style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13),
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                    ),
                                  ),
                                ],
                              ),
                            ),
                            Expanded(child: _buildArtifactPreviewPane()),
                          ],
                        ),
            ),
          ],
        );
      },
    );
  }

  Widget _buildFilterChip(String label, {required bool isSelected, required VoidCallback onSelected}) {
    return ChoiceChip(
      label: Text(label, style: TextStyle(fontSize: 12, fontWeight: isSelected ? FontWeight.w600 : FontWeight.normal)),
      selected: isSelected,
      onSelected: (_) => onSelected(),
      selectedColor: AppTokens.brandPrimary.withOpacity(0.2),
      side: BorderSide(
        color: isSelected ? AppTokens.brandPrimaryLight : Theme.of(context).dividerColor,
      ),
    );
  }

  Widget _buildArtifactsList() {
    final list = _filteredArtifacts;
    if (list.isEmpty) {
      return EmptyState(
        icon: Icons.inventory_2_outlined,
        title: 'No Artifacts Found',
        message: 'No registered artifacts match the selected filter.',
      );
    }

    return ListView.separated(
      padding: const EdgeInsets.all(AppTokens.space16),
      itemCount: list.length,
      separatorBuilder: (_, __) => const SizedBox(height: AppTokens.space8),
      itemBuilder: (context, index) {
        final art = list[index];
        final isSelected = _selectedArtifact?.id == art.id;

        return CustomCard(
          borderColor: isSelected ? AppTokens.brandPrimaryLight : null,
          backgroundColor: isSelected ? AppTokens.brandPrimary.withOpacity(0.08) : null,
          onTap: () => _handleSelectArtifact(art),
          child: Row(
            children: [
              _buildArtifactKindIcon(art.kind),
              const SizedBox(width: AppTokens.space12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      art.path.split('/').last,
                      style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const SizedBox(height: 2),
                    Text(
                      art.description.isNotEmpty ? art.description : art.path,
                      style: Theme.of(context).textTheme.bodyMedium,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                ),
              ),
            ],
          ),
        );
      },
    );
  }

  Widget _buildArtifactPreviewPane() {
    if (_selectedArtifact == null) {
      return EmptyState(
        icon: Icons.preview_outlined,
        title: 'Select an Artifact',
        message: 'Choose an artifact from the list to inspect its contents.',
      );
    }

    if (_isLoadingContent) {
      return const Center(child: CircularProgressIndicator());
    }

    final art = _selectedArtifact!;
    final content = _loadedContent ?? 'No content available for this artifact.';

    // Choose appropriate viewer based on artifact kind or file extension
    final pathLower = art.path.toLowerCase();

    if (art.kind == ArtifactKind.diff || pathLower.endsWith('.diff') || pathLower.endsWith('.patch')) {
      return DiffViewer(diffText: content, filename: art.path);
    }

    if (art.kind == ArtifactKind.report || art.kind == ArtifactKind.memoryDoc || pathLower.endsWith('.md')) {
      return SafeMarkdownView(markdown: content);
    }

    // Default Code Viewer
    String lang = 'text';
    if (pathLower.endsWith('.py')) lang = 'python';
    if (pathLower.endsWith('.dart')) lang = 'dart';
    if (pathLower.endsWith('.json')) lang = 'json';
    if (pathLower.endsWith('.yaml') || pathLower.endsWith('.yml')) lang = 'yaml';
    if (pathLower.endsWith('.sh')) lang = 'bash';

    return CodeViewer(code: content, language: lang, filename: art.path);
  }

  Widget _buildArtifactKindIcon(ArtifactKind kind) {
    IconData icon;
    Color color;

    switch (kind) {
      case ArtifactKind.report:
        icon = Icons.description_outlined;
        color = AppTokens.brandPrimaryLight;
        break;
      case ArtifactKind.diff:
        icon = Icons.difference_outlined;
        color = AppTokens.info;
        break;
      case ArtifactKind.testReport:
        icon = Icons.fact_check_outlined;
        color = AppTokens.warning;
        break;
      case ArtifactKind.sourceCode:
        icon = Icons.code;
        color = AppTokens.success;
        break;
      case ArtifactKind.screenshot:
        icon = Icons.image_outlined;
        color = AppTokens.purple;
        break;
      case ArtifactKind.memoryDoc:
        icon = Icons.menu_book_outlined;
        color = AppTokens.brandPrimary;
        break;
      case ArtifactKind.evidence:
        icon = Icons.vpn_key_outlined;
        color = AppTokens.info;
        break;
      case ArtifactKind.general:
      default:
        icon = Icons.insert_drive_file_outlined;
        color = AppTokens.darkTextMuted;
        break;
    }

    return Container(
      padding: const EdgeInsets.all(AppTokens.space8),
      decoration: BoxDecoration(
        color: color.withOpacity(0.12),
        borderRadius: AppTokens.borderRadiusSm,
      ),
      child: Icon(icon, size: 18, color: color),
    );
  }
}
