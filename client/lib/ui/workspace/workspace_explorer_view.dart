import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../services/folder_picker_service.dart';
import '../../services/workspace_service.dart';
import '../../state/app_state.dart';

/// Workspace Explorer View exposing the Manager's project understanding,
/// subsystems, file dependency graphs, and audit history.
class WorkspaceExplorerView extends StatefulWidget {
  final AppState appState;

  const WorkspaceExplorerView({super.key, required this.appState});

  @override
  State<WorkspaceExplorerView> createState() => _WorkspaceExplorerViewState();
}

class _WorkspaceExplorerViewState extends State<WorkspaceExplorerView> with SingleTickerProviderStateMixin {
  late TabController _tabController;
  final WorkspaceClientService _workspaceService = WorkspaceClientService();

  Map<String, dynamic>? _status;
  Map<String, dynamic>? _projectMap;
  List<dynamic> _subsystems = [];
  List<dynamic> _auditHistory = [];
  String _mapMarkdown = '';
  bool _isLoading = true;

  String _searchFilter = '';
  Map<String, dynamic>? _selectedFile;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 4, vsync: this);
    _loadWorkspaceData();
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }

  Future<void> _loadWorkspaceData() async {
    setState(() => _isLoading = true);
    try {
      final status = await _workspaceService.getStatus();
      final pmap = await _workspaceService.getProjectMap();
      final subs = await _workspaceService.listSubsystems();
      final history = await _workspaceService.getAuditHistory();
      final md = await _workspaceService.getProjectMapMarkdown();

      setState(() {
        _status = status;
        _projectMap = pmap;
        _subsystems = subs;
        _auditHistory = history;
        _mapMarkdown = md;
        _isLoading = false;
      });
    } catch (_) {
      setState(() => _isLoading = false);
    }
  }

  Future<void> _runAudit({bool full = false}) async {
    setState(() => _isLoading = true);
    await _workspaceService.runAudit(full: full);
    await _loadWorkspaceData();
  }

  Future<void> _changeWorkspaceFolder() async {
    final current = widget.appState.activeWorkingPath;
    final selected = await FolderPickerService.pickFolder(initialDirectory: current);
    if (selected != null && selected.isNotEmpty) {
      widget.appState.setWorkingPath(selected);
      await _workspaceService.setFolder(selected);
      await _workspaceService.runAudit(full: false, trigger: 'FOLDER_CHANGED');
      await _loadWorkspaceData();
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    final projectName = _status?['project_name'] ?? 'AutonomOS';
    final activePath = widget.appState.activeWorkingPath;
    final totalFiles = _status?['total_files'] ?? 0;
    final isInit = _status?['is_initialized'] ?? false;
    final techStack = _status?['tech_stack'] as Map<String, dynamic>? ?? {};
    final languages = (techStack['languages'] as List?)?.join(', ') ?? 'Auto-detected';
    final frameworks = (techStack['frameworks'] as List?)?.join(', ') ?? '';

    return Container(
      color: isDark ? AppTokens.darkBg : AppTokens.lightBg,
      child: Column(
        children: [
          // 1. Top Workspace Header
          Container(
            padding: const EdgeInsets.symmetric(horizontal: AppTokens.space24, vertical: AppTokens.space16),
            decoration: BoxDecoration(
              color: isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
              border: Border(bottom: BorderSide(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder)),
            ),
            child: Row(
              children: [
                Container(
                  padding: const EdgeInsets.all(AppTokens.space8),
                  decoration: BoxDecoration(
                    color: AppTokens.brandPrimary.withOpacity(0.12),
                    borderRadius: AppTokens.borderRadiusMd,
                  ),
                  child: const Icon(Icons.account_tree_outlined, size: 20, color: AppTokens.brandPrimary),
                ),
                const SizedBox(width: AppTokens.space12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Text(
                            projectName,
                            style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
                          ),
                          const SizedBox(width: AppTokens.space8),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: AppTokens.space6, vertical: 2),
                            decoration: BoxDecoration(
                              color: isInit ? AppTokens.success.withOpacity(0.12) : AppTokens.warning.withOpacity(0.12),
                              borderRadius: AppTokens.borderRadiusXs,
                            ),
                            child: Text(
                              isInit ? 'PROJECT MAP ACTIVE' : 'UNAUDITED',
                              style: TextStyle(
                                fontSize: 10,
                                fontWeight: FontWeight.bold,
                                color: isInit ? AppTokens.success : AppTokens.warning,
                              ),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 2),
                      InkWell(
                        onTap: _changeWorkspaceFolder,
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Text(
                              activePath,
                              style: TextStyle(fontSize: 11.5, fontFamily: 'monospace', color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted),
                            ),
                            const SizedBox(width: 4),
                            Icon(Icons.edit_outlined, size: 12, color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
                OutlinedButton.icon(
                  style: OutlinedButton.styleFrom(
                    foregroundColor: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
                    side: BorderSide(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
                    padding: const EdgeInsets.symmetric(horizontal: AppTokens.space12, vertical: AppTokens.space8),
                  ),
                  icon: const Icon(Icons.folder_open, size: 14),
                  label: const Text('Open Folder...', style: TextStyle(fontSize: 12)),
                  onPressed: _changeWorkspaceFolder,
                ),
                const SizedBox(width: AppTokens.space8),
                ElevatedButton.icon(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AppTokens.brandPrimary,
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(horizontal: AppTokens.space12, vertical: AppTokens.space8),
                  ),
                  icon: const Icon(Icons.refresh, size: 14),
                  label: Text(isInit ? 'Audit Changes' : 'Initial Audit', style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
                  onPressed: () => _runAudit(full: !isInit),
                ),
              ],
            ),
          ),

          // 2. Navigation Tabs
          Container(
            decoration: BoxDecoration(
              color: isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
              border: Border(bottom: BorderSide(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder)),
            ),
            child: TabBar(
              controller: _tabController,
              indicatorColor: AppTokens.brandPrimary,
              labelColor: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
              unselectedLabelColor: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
              tabs: const [
                Tab(text: 'Subsystems'),
                Tab(text: 'File Knowledge'),
                Tab(text: 'Audit History'),
                Tab(text: 'Project Map Markdown'),
              ],
            ),
          ),

          // 3. Tab Body
          Expanded(
            child: _isLoading
                ? const Center(child: CircularProgressIndicator())
                : TabBarView(
                    controller: _tabController,
                    children: [
                      _buildSubsystemsTab(isDark),
                      _buildFileKnowledgeTab(isDark),
                      _buildAuditHistoryTab(isDark),
                      _buildMarkdownTab(isDark),
                    ],
                  ),
          ),
        ],
      ),
    );
  }

  // --- Subsystems Tab ---
  Widget _buildSubsystemsTab(bool isDark) {
    if (_subsystems.isEmpty) {
      return Center(
        child: Text('No subsystems indexed. Run an initial audit.', style: TextStyle(color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted)),
      );
    }

    return ListView.builder(
      padding: const EdgeInsets.all(AppTokens.space20),
      itemCount: _subsystems.length,
      itemBuilder: (context, index) {
        final sub = _subsystems[index];
        final name = sub['name'] as String? ?? '';
        final fileCount = sub['file_count'] as int? ?? 0;
        final files = (sub['files'] as List?)?.cast<String>() ?? [];

        return Container(
          margin: const EdgeInsets.only(bottom: AppTokens.space12),
          decoration: BoxDecoration(
            color: isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
            borderRadius: AppTokens.borderRadiusMd,
            border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
          ),
          child: ExpansionTile(
            title: Row(
              children: [
                const Icon(Icons.folder_outlined, size: 16, color: AppTokens.brandPrimary),
                const SizedBox(width: AppTokens.space8),
                Text(name, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
                const Spacer(),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: AppTokens.space6, vertical: 2),
                  decoration: BoxDecoration(
                    color: isDark ? AppTokens.darkElevated : AppTokens.lightBorder,
                    borderRadius: AppTokens.borderRadiusXs,
                  ),
                  child: Text('$fileCount files', style: const TextStyle(fontSize: 11, fontFamily: 'monospace')),
                ),
              ],
            ),
            children: files.map((fp) {
              return ListTile(
                dense: true,
                leading: const Icon(Icons.description_outlined, size: 14, color: AppTokens.darkTextMuted),
                title: Text(fp, style: const TextStyle(fontSize: 12.5, fontFamily: 'monospace')),
                onTap: () async {
                  final detail = await _workspaceService.getFileDetail(fp);
                  setState(() {
                    _selectedFile = detail;
                    _tabController.animateTo(1);
                  });
                },
              );
            }).toList(),
          ),
        );
      },
    );
  }

  // --- File Knowledge Tab ---
  Widget _buildFileKnowledgeTab(bool isDark) {
    final allFiles = (_projectMap?['files'] as Map<String, dynamic>?) ?? {};
    final filtered = allFiles.entries.where((e) {
      if (_searchFilter.isEmpty) return true;
      return e.key.toLowerCase().contains(_searchFilter.toLowerCase());
    }).toList();

    return Row(
      children: [
        // File list
        SizedBox(
          width: 320,
          child: Container(
            decoration: BoxDecoration(
              border: Border(right: BorderSide(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder)),
            ),
            child: Column(
              children: [
                Padding(
                  padding: const EdgeInsets.all(AppTokens.space10),
                  child: TextField(
                    onChanged: (val) => setState(() => _searchFilter = val),
                    style: const TextStyle(fontSize: 12.5),
                    decoration: InputDecoration(
                      hintText: 'Filter files...',
                      isDense: true,
                      prefixIcon: const Icon(Icons.search, size: 14),
                      contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                    ),
                  ),
                ),
                Expanded(
                  child: ListView.builder(
                    itemCount: filtered.length,
                    itemBuilder: (context, index) {
                      final item = filtered[index];
                      final path = item.key;
                      final isSelected = _selectedFile?['path'] == path;

                      return ListTile(
                        dense: true,
                        selected: isSelected,
                        title: Text(
                          path,
                          style: TextStyle(
                            fontSize: 12,
                            fontFamily: 'monospace',
                            color: isSelected ? AppTokens.brandPrimary : (isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary),
                          ),
                        ),
                        onTap: () => setState(() => _selectedFile = item.value as Map<String, dynamic>?),
                      );
                    },
                  ),
                ),
              ],
            ),
          ),
        ),

        // File details pane
        Expanded(
          child: _selectedFile == null
              ? Center(child: Text('Select a file to inspect Manager knowledge', style: TextStyle(color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted)))
              : _buildFileDetailPane(_selectedFile!, isDark),
        ),
      ],
    );
  }

  Widget _buildFileDetailPane(Map<String, dynamic> f, bool isDark) {
    final path = f['path'] ?? '';
    final purpose = f['purpose'] ?? 'Module component';
    final symbols = (f['symbols'] as List?)?.cast<String>() ?? [];
    final deps = (f['dependencies'] as List?)?.cast<String>() ?? [];
    final dependents = (f['dependents'] as List?)?.cast<String>() ?? [];
    final hash = f['hash'] ?? '';
    final lastAudited = f['last_audited'] ?? '';

    return SingleChildScrollView(
      padding: const EdgeInsets.all(AppTokens.space24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(path, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold, fontFamily: 'monospace')),
          const SizedBox(height: AppTokens.space8),
          Container(
            padding: const EdgeInsets.all(AppTokens.space12),
            decoration: BoxDecoration(
              color: isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
              borderRadius: AppTokens.borderRadiusMd,
              border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('INFERRED PURPOSE', style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: AppTokens.brandPrimary)),
                const SizedBox(height: 4),
                Text(purpose, style: const TextStyle(fontSize: 13)),
              ],
            ),
          ),
          const SizedBox(height: AppTokens.space16),

          // Exported Symbols
          const Text('EXPORTED SYMBOLS / FUNCTIONS', style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold)),
          const SizedBox(height: AppTokens.space6),
          if (symbols.isEmpty)
            Text('No exported symbols detected.', style: TextStyle(fontSize: 12, color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted))
          else
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: symbols.map((s) {
                return Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                  decoration: BoxDecoration(
                    color: isDark ? AppTokens.darkElevated : AppTokens.lightBorder,
                    borderRadius: AppTokens.borderRadiusXs,
                  ),
                  child: Text(s, style: const TextStyle(fontSize: 11.5, fontFamily: 'monospace')),
                );
              }).toList(),
            ),

          const SizedBox(height: AppTokens.space20),

          // Dependencies & Dependents
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('DEPENDS ON (${deps.length})', style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold)),
                    const SizedBox(height: AppTokens.space6),
                    if (deps.isEmpty)
                      Text('No internal dependencies', style: TextStyle(fontSize: 11.5, color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted))
                    else
                      ...deps.map((d) => Text('• $d', style: const TextStyle(fontSize: 11.5, fontFamily: 'monospace'))),
                  ],
                ),
              ),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('DEPENDENTS (${dependents.length})', style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold)),
                    const SizedBox(height: AppTokens.space6),
                    if (dependents.isEmpty)
                      Text('No dependents importing this', style: TextStyle(fontSize: 11.5, color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted))
                    else
                      ...dependents.map((d) => Text('• $d', style: const TextStyle(fontSize: 11.5, fontFamily: 'monospace'))),
                  ],
                ),
              ),
            ],
          ),

          const SizedBox(height: AppTokens.space24),
          Text('Hash: $hash', style: TextStyle(fontSize: 10.5, fontFamily: 'monospace', color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted)),
          Text('Last Audited: $lastAudited', style: TextStyle(fontSize: 10.5, fontFamily: 'monospace', color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted)),
        ],
      ),
    );
  }

  // --- Audit History Tab ---
  Widget _buildAuditHistoryTab(bool isDark) {
    if (_auditHistory.isEmpty) {
      return Center(
        child: Text('No audit history recorded yet.', style: TextStyle(color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted)),
      );
    }

    return ListView.builder(
      padding: const EdgeInsets.all(AppTokens.space20),
      itemCount: _auditHistory.length,
      itemBuilder: (context, index) {
        final item = _auditHistory[index];
        final trigger = item['trigger'] ?? 'AUDIT';
        final summary = item['summary'] ?? '';
        final timestamp = item['timestamp'] ?? '';
        final duration = item['duration_seconds'] ?? 0;

        return Container(
          margin: const EdgeInsets.only(bottom: AppTokens.space12),
          padding: const EdgeInsets.all(AppTokens.space14),
          decoration: BoxDecoration(
            color: isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
            borderRadius: AppTokens.borderRadiusMd,
            border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Icon(Icons.history_outlined, size: 16, color: AppTokens.brandPrimary),
              const SizedBox(width: AppTokens.space12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Text(trigger, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.bold, fontFamily: 'monospace')),
                        const Spacer(),
                        Text('$duration s', style: const TextStyle(fontSize: 11, fontFamily: 'monospace', color: AppTokens.brandPrimary)),
                      ],
                    ),
                    const SizedBox(height: 4),
                    Text(summary, style: TextStyle(fontSize: 12.5, color: isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary)),
                    const SizedBox(height: 4),
                    Text(timestamp, style: TextStyle(fontSize: 10.5, color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted)),
                  ],
                ),
              ),
            ],
          ),
        );
      },
    );
  }

  // --- Project Map Markdown Tab ---
  Widget _buildMarkdownTab(bool isDark) {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(AppTokens.space24),
      child: SelectableText(
        _mapMarkdown,
        style: TextStyle(
          fontFamily: 'monospace',
          fontSize: 12,
          height: 1.45,
          color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
        ),
      ),
    );
  }
}
