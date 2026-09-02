import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../state/app_state.dart';
import '../project/project_dialog.dart';
import '../widgets/emergency_stop_dialog.dart';

class AppSidebar extends StatelessWidget {
  final AppState appState;

  const AppSidebar({super.key, required this.appState});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final currentTab = appState.currentTab;

    return Container(
      width: 240,
      decoration: BoxDecoration(
        color: isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
        border: Border(right: BorderSide(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder)),
      ),
      child: Column(
        children: [
          // Logo & Branding
          Padding(
            padding: const EdgeInsets.all(AppTokens.space20),
            child: Row(
              children: [
                Container(
                  width: 32,
                  height: 32,
                  decoration: BoxDecoration(
                    color: AppTokens.brandPrimary,
                    borderRadius: AppTokens.borderRadiusSm,
                  ),
                  child: const Center(
                    child: Text(
                      'A',
                      style: TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 18),
                    ),
                  ),
                ),
                const SizedBox(width: AppTokens.space12),
                const Text(
                  'AutonomOS',
                  style: TextStyle(fontWeight: FontWeight.w700, fontSize: 16, letterSpacing: 0.5),
                ),
              ],
            ),
          ),
          const Divider(height: 1),

          // Project Selector Trigger
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: AppTokens.space12, vertical: AppTokens.space8),
            child: InkWell(
              borderRadius: AppTokens.borderRadiusSm,
              onTap: () {
                showDialog(
                  context: context,
                  builder: (ctx) => ProjectDialog(appState: appState),
                );
              },
              child: Container(
                padding: const EdgeInsets.all(AppTokens.space8),
                decoration: BoxDecoration(
                  color: isDark ? AppTokens.darkCard : AppTokens.lightCard,
                  borderRadius: AppTokens.borderRadiusSm,
                  border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
                ),
                child: Row(
                  children: [
                    const Icon(Icons.folder_outlined, size: 16, color: AppTokens.brandPrimaryLight),
                    const SizedBox(width: AppTokens.space8),
                    Expanded(
                      child: Text(
                        appState.selectedProject?.name ?? 'Select Project',
                        style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    const Icon(Icons.unfold_more, size: 14, color: AppTokens.darkTextMuted),
                  ],
                ),
              ),
            ),
          ),
          const SizedBox(height: AppTokens.space8),

          // Navigation Links
          Expanded(
            child: ListView(
              padding: const EdgeInsets.symmetric(horizontal: AppTokens.space8),
              children: [
                _buildNavItem(context, tab: AppTab.home, label: 'Overview', icon: Icons.dashboard_outlined),
                _buildNavItem(context, tab: AppTab.chat, label: 'Workforce Chat', icon: Icons.chat_bubble_outline),
                _buildNavItem(context, tab: AppTab.workflow, label: 'Active Workflow', icon: Icons.account_tree_outlined),
                _buildNavItem(context, tab: AppTab.workforce, label: 'Workforce Roster', icon: Icons.groups_outlined),
                _buildNavItem(context, tab: AppTab.activity, label: 'Activity Timeline', icon: Icons.timeline_outlined),
                _buildNavItem(context, tab: AppTab.artifacts, label: 'Artifact Browser', icon: Icons.inventory_2_outlined),
                _buildNavItem(context, tab: AppTab.approvals, label: 'Approvals', icon: Icons.gavel_outlined),
                _buildNavItem(context, tab: AppTab.settings, label: 'Settings', icon: Icons.tune_outlined),
              ],
            ),
          ),

          // Emergency Stop Trigger
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: AppTokens.space12, vertical: 4),
            child: OutlinedButton.icon(
              onPressed: () {
                showDialog(
                  context: context,
                  builder: (ctx) => EmergencyStopConfirmDialog(
                    onConfirm: (reason) => appState.activateEmergencyStop(reason),
                  ),
                );
              },
              icon: const Icon(Icons.stop_circle_outlined, size: 16, color: AppTokens.danger),
              label: const Text('Emergency Stop', style: TextStyle(color: AppTokens.danger, fontSize: 12, fontWeight: FontWeight.bold)),
              style: OutlinedButton.styleFrom(
                side: const BorderSide(color: AppTokens.danger),
                minimumSize: const Size(double.infinity, 34),
              ),
            ),
          ),
          const SizedBox(height: 4),

          // Footer info & Theme Toggle
          const Divider(height: 1),
          Padding(
            padding: const EdgeInsets.all(AppTokens.space12),
            child: Row(
              children: [
                Text(
                  'AutonomOS v1.0',
                  style: theme.textTheme.labelSmall,
                ),
                const Spacer(),
                IconButton(
                  icon: Icon(appState.themeMode == ThemeMode.dark ? Icons.light_mode_outlined : Icons.dark_mode_outlined, size: 18),
                  onPressed: appState.toggleTheme,
                  splashRadius: 16,
                  padding: EdgeInsets.zero,
                  constraints: const BoxConstraints(),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildNavItem(BuildContext context, {required AppTab tab, required String label, required IconData icon}) {
    final isSelected = appState.currentTab == tab;
    return Container(
      margin: const EdgeInsets.symmetric(vertical: 2),
      child: ListTile(
        dense: true,
        selected: isSelected,
        selectedTileColor: AppTokens.brandPrimary.withOpacity(0.12),
        shape: RoundedRectangleBorder(borderRadius: AppTokens.borderRadiusSm),
        leading: Icon(
          icon,
          size: 18,
          color: isSelected ? AppTokens.brandPrimaryLight : null,
        ),
        title: Text(
          label,
          style: TextStyle(
            fontSize: 13,
            fontWeight: isSelected ? FontWeight.w600 : FontWeight.normal,
            color: isSelected ? AppTokens.brandPrimaryLight : null,
          ),
        ),
        onTap: () => appState.setTab(tab),
      ),
    );
  }
}
