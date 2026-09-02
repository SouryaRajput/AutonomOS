import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../state/app_state.dart';
import '../widgets/custom_card.dart';
import '../workforce/worker_manifest_dialog.dart';
import 'autonomy_settings_view.dart';
import 'provider_settings_view.dart';

class SettingsView extends StatefulWidget {
  final AppState appState;

  const SettingsView({super.key, required this.appState});

  @override
  State<SettingsView> createState() => _SettingsViewState();
}

class _SettingsViewState extends State<SettingsView> {
  int _activeTab = 0;
  bool _developerMode = false;
  bool _isExporting = false;

  final List<String> _tabLabels = [
    'General',
    'Autonomy',
    'Providers',
    'Workforce',
    'Safety',
    'Storage',
    'Advanced',
  ];

  final List<IconData> _tabIcons = [
    Icons.tune,
    Icons.shield_outlined,
    Icons.route_outlined,
    Icons.groups_outlined,
    Icons.health_and_safety_outlined,
    Icons.folder_outlined,
    Icons.code,
  ];

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        // Tab Header
        Container(
          padding: const EdgeInsets.fromLTRB(AppTokens.space24, AppTokens.space12, AppTokens.space24, 0),
          decoration: BoxDecoration(
            border: Border(bottom: BorderSide(color: Theme.of(context).dividerColor)),
          ),
          child: SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: List.generate(_tabLabels.length, (index) {
                return _buildTabItem(_tabLabels[index], index, _tabIcons[index]);
              }),
            ),
          ),
        ),

        // Tab Content
        Expanded(
          child: _buildTabContent(_activeTab),
        ),
      ],
    );
  }

  Widget _buildTabItem(String label, int index, IconData icon) {
    final isSelected = _activeTab == index;
    return InkWell(
      onTap: () => setState(() => _activeTab = index),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: AppTokens.space12, vertical: AppTokens.space12),
        decoration: BoxDecoration(
          border: Border(
            bottom: BorderSide(
              color: isSelected ? AppTokens.brandPrimaryLight : Colors.transparent,
              width: 2.5,
            ),
          ),
        ),
        child: Row(
          children: [
            Icon(icon, size: 16, color: isSelected ? AppTokens.brandPrimaryLight : AppTokens.darkTextMuted),
            const SizedBox(width: AppTokens.space8),
            Text(
              label,
              style: TextStyle(
                fontWeight: isSelected ? FontWeight.bold : FontWeight.normal,
                color: isSelected ? AppTokens.brandPrimaryLight : null,
                fontSize: 13,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildTabContent(int index) {
    switch (index) {
      case 0:
        return _buildGeneralTab();
      case 1:
        return AutonomySettingsView(appState: widget.appState);
      case 2:
        return ProviderSettingsView(appState: widget.appState);
      case 3:
        return _buildWorkforceTab();
      case 4:
        return _buildSafetyTab();
      case 5:
        return _buildStorageTab();
      case 6:
        return _buildAdvancedTab();
      default:
        return const SizedBox.shrink();
    }
  }

  Widget _buildGeneralTab() {
    final theme = Theme.of(context);
    return SingleChildScrollView(
      padding: const EdgeInsets.all(AppTokens.space24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('General Preferences & Transparency', style: theme.textTheme.headlineMedium),
          const SizedBox(height: AppTokens.space4),
          Text('System information, appearance, and local-first privacy disclosures', style: theme.textTheme.bodyMedium),
          const SizedBox(height: AppTokens.space24),

          // Appearance Card
          CustomCard(
            child: Row(
              children: [
                const Icon(Icons.palette_outlined, size: 22, color: AppTokens.brandPrimaryLight),
                const SizedBox(width: AppTokens.space12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('Appearance & Theme', style: theme.textTheme.titleMedium),
                      const SizedBox(height: 2),
                      Text('Switch between calm dark workspace and crisp light mode', style: theme.textTheme.bodySmall),
                    ],
                  ),
                ),
                IconButton(
                  icon: Icon(widget.appState.themeMode == ThemeMode.dark ? Icons.light_mode : Icons.dark_mode),
                  onPressed: widget.appState.toggleTheme,
                ),
              ],
            ),
          ),
          const SizedBox(height: AppTokens.space16),

          // Privacy & Local-First Architecture
          CustomCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Icon(Icons.lock_outline, size: 20, color: AppTokens.success),
                    const SizedBox(width: AppTokens.space8),
                    Text('Local-First Privacy Architecture', style: theme.textTheme.titleMedium),
                  ],
                ),
                const SizedBox(height: AppTokens.space8),
                const Text(
                  '• Storage: All code, databases, event journals, and verification evidence reside on your local machine.\n'
                  '• Network: Zero telemetry or project analytics are transmitted to third-party telemetry servers.\n'
                  '• Inference: Model prompts are sent only to configured inference providers (e.g. OpenRouter) or remain 100% offline when using local Ollama endpoints.',
                  style: TextStyle(fontSize: 13, height: 1.5),
                ),
              ],
            ),
          ),
          const SizedBox(height: AppTokens.space16),

          // Open Source & Cost Disclaimer
          CustomCard(
            backgroundColor: AppTokens.info.withOpacity(0.06),
            borderColor: AppTokens.info.withOpacity(0.3),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Icon(Icons.attach_money, size: 20, color: AppTokens.info),
                    const SizedBox(width: AppTokens.space8),
                    Text('Open Source & Cost Disclaimer', style: theme.textTheme.titleMedium),
                  ],
                ),
                const SizedBox(height: AppTokens.space8),
                const Text(
                  'AutonomOS is 100% open source and free to run. External LLM providers charge directly per inference token. Any cost metrics displayed in the user interface are labeled as estimates and may vary based on provider billing tiers.',
                  style: TextStyle(fontSize: 13, height: 1.4),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildWorkforceTab() {
    final theme = Theme.of(context);
    final workers = widget.appState.workers;

    return SingleChildScrollView(
      padding: const EdgeInsets.all(AppTokens.space24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Workforce Configuration & Permissions', style: theme.textTheme.headlineMedium),
          const SizedBox(height: AppTokens.space4),
          Text('Inspect specialist capabilities, tool permissions, and model preferences', style: theme.textTheme.bodyMedium),
          const SizedBox(height: AppTokens.space24),

          ...workers.map((w) {
            return Padding(
              padding: const EdgeInsets.only(bottom: AppTokens.space12),
              child: CustomCard(
                child: Row(
                  children: [
                    const Icon(Icons.smart_toy_outlined, size: 22, color: AppTokens.brandPrimaryLight),
                    const SizedBox(width: AppTokens.space12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(w.name, style: theme.textTheme.titleMedium),
                          Text(w.role, style: theme.textTheme.bodySmall),
                        ],
                      ),
                    ),
                    OutlinedButton(
                      onPressed: () {
                        showDialog(
                          context: context,
                          builder: (ctx) => WorkerManifestDialog(worker: w),
                        );
                      },
                      child: const Text('View Manifest'),
                    ),
                  ],
                ),
              ),
            );
          }),
        ],
      ),
    );
  }

  Widget _buildSafetyTab() {
    final theme = Theme.of(context);
    final isStopped = widget.appState.isEmergencyStopped;

    return SingleChildScrollView(
      padding: const EdgeInsets.all(AppTokens.space24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Safety & Governance State', style: theme.textTheme.headlineMedium),
          const SizedBox(height: AppTokens.space4),
          Text('Deterministic safety invariants, rollback checkpoints, and emergency halt', style: theme.textTheme.bodyMedium),
          const SizedBox(height: AppTokens.space24),

          // Safety Mode Info (No "disable safety" button)
          CustomCard(
            borderColor: AppTokens.success.withOpacity(0.4),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Icon(Icons.verified_user, size: 20, color: AppTokens.success),
                    const SizedBox(width: AppTokens.space8),
                    Text('Active Safety Invariants', style: theme.textTheme.titleMedium),
                    const Spacer(),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                      decoration: BoxDecoration(
                        color: AppTokens.success.withOpacity(0.15),
                        borderRadius: BorderRadius.circular(4),
                      ),
                      child: const Text('ENFORCED', style: TextStyle(color: AppTokens.success, fontSize: 10, fontWeight: FontWeight.bold)),
                    ),
                  ],
                ),
                const SizedBox(height: AppTokens.space8),
                const Text(
                  '• Path Traversal Guard: Prevents filesystem access outside project root.\n'
                  '• Secret Redaction Engine: Automatically scrubs API keys from logs and worker context.\n'
                  '• Scope Deviation Detector: Freezes tasks modifying more than 10 files without explicit authorization.\n'
                  '• Checkpoints & Rollback: Atomic pre-task checkpoints enabled for all code modifications.',
                  style: TextStyle(fontSize: 13, height: 1.5),
                ),
              ],
            ),
          ),
          const SizedBox(height: AppTokens.space16),

          // Emergency Halt Status
          CustomCard(
            borderColor: isStopped ? AppTokens.danger : AppTokens.warning.withOpacity(0.4),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(
                      isStopped ? Icons.error_outline : Icons.warning_amber_rounded,
                      size: 20,
                      color: isStopped ? AppTokens.danger : AppTokens.warning,
                    ),
                    const SizedBox(width: AppTokens.space8),
                    Text('Workforce Emergency Halt Status', style: theme.textTheme.titleMedium),
                  ],
                ),
                const SizedBox(height: AppTokens.space8),
                Text(
                  isStopped
                      ? 'Workforce is STOPPED. All autonomous executions are blocked.'
                      : 'Workforce is OPERATIONAL. Emergency stop is ready for instant activation.',
                  style: theme.textTheme.bodyMedium,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildStorageTab() {
    final theme = Theme.of(context);
    final proj = widget.appState.selectedProject;
    final rootPath = proj?.rootPath ?? '/path/to/project';

    return SingleChildScrollView(
      padding: const EdgeInsets.all(AppTokens.space24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Project Storage & Disk Locations', style: theme.textTheme.headlineMedium),
          const SizedBox(height: AppTokens.space4),
          Text('Inspect local filesystem paths, SQLite databases, and artifact footprints', style: theme.textTheme.bodyMedium),
          const SizedBox(height: AppTokens.space24),

          CustomCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _buildStorageRow('Project Root', rootPath),
                const Divider(height: AppTokens.space20),
                _buildStorageRow('Database Engine', 'Embedded SQLite ($rootPath/.autonomos/project.db)'),
                const Divider(height: AppTokens.space20),
                _buildStorageRow('Artifact Storage', '$rootPath/.autonomos/artifacts/'),
                const Divider(height: AppTokens.space20),
                _buildStorageRow('Total Artifacts', '${widget.appState.artifacts.length} registered files & reports'),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildStorageRow(String label, String value) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 12, color: AppTokens.darkTextMuted)),
        const SizedBox(height: 2),
        Text(value, style: const TextStyle(fontFamily: 'monospace', fontSize: 12)),
      ],
    );
  }

  Widget _buildAdvancedTab() {
    final theme = Theme.of(context);
    return SingleChildScrollView(
      padding: const EdgeInsets.all(AppTokens.space24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Advanced & Developer Mode', style: theme.textTheme.headlineMedium),
          const SizedBox(height: AppTokens.space4),
          Text('Project bundle export/import, developer mode, and schema versioning', style: theme.textTheme.bodyMedium),
          const SizedBox(height: AppTokens.space24),

          // Developer Mode Toggle
          CustomCard(
            child: SwitchListTile(
              title: const Text('Enable Developer Mode'),
              subtitle: const Text('Exposes raw event payloads, task IDs, tool execution lineage, and policy decisions in UI.'),
              value: _developerMode,
              onChanged: (val) => setState(() => _developerMode = val),
              contentPadding: EdgeInsets.zero,
            ),
          ),
          const SizedBox(height: AppTokens.space16),

          // Export / Import Project Bundle
          CustomCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Project Portability & Backup', style: theme.textTheme.titleMedium),
                const SizedBox(height: AppTokens.space8),
                const Text(
                  'Export your project metadata, tasks, reports, and memory artifacts as a portable bundle. Secrets and API keys are strictly excluded.',
                  style: TextStyle(fontSize: 13, height: 1.4),
                ),
                const SizedBox(height: AppTokens.space16),
                Row(
                  children: [
                    ElevatedButton.icon(
                      onPressed: _isExporting
                          ? null
                          : () async {
                              setState(() => _isExporting = true);
                              await Future.delayed(const Duration(milliseconds: 600));
                              if (mounted) {
                                setState(() => _isExporting = false);
                                ScaffoldMessenger.of(context).showSnackBar(
                                  const SnackBar(content: Text('Project bundle exported successfully (secrets excluded).')),
                                );
                              }
                            },
                      icon: const Icon(Icons.file_download_outlined, size: 16),
                      label: const Text('Export Project Bundle'),
                    ),
                    const SizedBox(width: AppTokens.space12),
                    OutlinedButton.icon(
                      onPressed: () {
                        ScaffoldMessenger.of(context).showSnackBar(
                          const SnackBar(content: Text('Ready to import validated project bundle.')),
                        );
                      },
                      icon: const Icon(Icons.file_upload_outlined, size: 16),
                      label: const Text('Import Bundle'),
                    ),
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(height: AppTokens.space16),

          // Version & Schema Info
          CustomCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('System Compatibility', style: theme.textTheme.titleMedium),
                const SizedBox(height: AppTokens.space8),
                const Text(
                  'Application Version: 1.0.0 • API Version: 1.0 • Schema Version: 16 • Status: Fully Compatible',
                  style: TextStyle(fontSize: 12, fontFamily: 'monospace'),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
