import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../state/app_state.dart';
import '../widgets/custom_card.dart';
import '../widgets/status_badge.dart';

class ProjectHomeView extends StatelessWidget {
  final AppState appState;

  const ProjectHomeView({super.key, required this.appState});

  @override
  Widget build(BuildContext context) {
    final project = appState.selectedProject;
    if (project == null) {
      return const Center(child: Text('No project selected'));
    }

    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    return SingleChildScrollView(
      padding: const EdgeInsets.all(AppTokens.space24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header
          Row(
            children: [
              Container(
                width: 44,
                height: 44,
                decoration: BoxDecoration(
                  color: AppTokens.brandPrimary.withOpacity(0.12),
                  borderRadius: AppTokens.borderRadiusMd,
                ),
                child: const Icon(Icons.rocket_launch_outlined, color: AppTokens.brandPrimaryLight),
              ),
              const SizedBox(width: AppTokens.space16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(project.name, style: theme.textTheme.headlineMedium),
                    const SizedBox(height: 2),
                    Text(
                      project.description.isNotEmpty ? project.description : project.rootPath,
                      style: theme.textTheme.bodyMedium,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                ),
              ),
              StatusBadge(status: project.status),
            ],
          ),
          const SizedBox(height: AppTokens.space24),

          // Primary Objective & Manager Status
          CustomCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Icon(Icons.psychology, size: 18, color: AppTokens.brandPrimaryLight),
                    const SizedBox(width: AppTokens.space8),
                    Text('Current Focus & Objective', style: theme.textTheme.titleMedium),
                    const Spacer(),
                    if (appState.managerStatus?.isActive == true)
                      const StatusBadge(status: 'WORKING', isSmall: true),
                  ],
                ),
                const SizedBox(height: AppTokens.space12),
                Text(
                  appState.managerStatus?.summaryText.isNotEmpty == true
                      ? appState.managerStatus!.summaryText
                      : 'Workforce is ready to receive instructions.',
                  style: theme.textTheme.bodyLarge,
                ),
                const SizedBox(height: AppTokens.space16),
                ElevatedButton.icon(
                  onPressed: () => appState.setTab(AppTab.chat),
                  icon: const Icon(Icons.chat_bubble_outline, size: 16),
                  label: const Text('Open Workforce Chat'),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AppTokens.brandPrimary,
                    foregroundColor: Colors.white,
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: AppTokens.space20),

          // Quick Navigation Grid
          LayoutBuilder(
            builder: (context, constraints) {
              final isWide = constraints.maxWidth > 700;
              return Wrap(
                spacing: AppTokens.space16,
                runSpacing: AppTokens.space16,
                children: [
                  SizedBox(
                    width: isWide ? (constraints.maxWidth - AppTokens.space16) / 2 : constraints.maxWidth,
                    child: _buildNavCard(
                      context,
                      icon: Icons.account_tree_outlined,
                      title: 'Active Workflow',
                      subtitle: 'Track milestone progress and inspect task execution graphs',
                      color: AppTokens.info,
                      onTap: () => appState.setTab(AppTab.workflow),
                    ),
                  ),
                  SizedBox(
                    width: isWide ? (constraints.maxWidth - AppTokens.space16) / 2 : constraints.maxWidth,
                    child: _buildNavCard(
                      context,
                      icon: Icons.groups_outlined,
                      title: 'Workforce Roster',
                      subtitle: 'Inspect specialist agents: Researcher, Programmer, Tester',
                      color: AppTokens.purple,
                      onTap: () => appState.setTab(AppTab.workforce),
                    ),
                  ),
                  SizedBox(
                    width: isWide ? (constraints.maxWidth - AppTokens.space16) / 2 : constraints.maxWidth,
                    child: _buildNavCard(
                      context,
                      icon: Icons.timeline,
                      title: 'Activity Feed',
                      subtitle: 'Real-time timeline of deterministic runtime events',
                      color: AppTokens.brandPrimaryLight,
                      onTap: () => appState.setTab(AppTab.activity),
                    ),
                  ),
                  SizedBox(
                    width: isWide ? (constraints.maxWidth - AppTokens.space16) / 2 : constraints.maxWidth,
                    child: _buildNavCard(
                      context,
                      icon: Icons.gavel_outlined,
                      title: 'Human-in-the-Loop',
                      subtitle: 'Review pending authorizations and autonomy boundaries',
                      color: AppTokens.warning,
                      onTap: () => appState.setTab(AppTab.approvals),
                    ),
                  ),
                ],
              );
            },
          ),
        ],
      ),
    );
  }

  Widget _buildNavCard(
    BuildContext context, {
    required IconData icon,
    required String title,
    required String subtitle,
    required Color color,
    required VoidCallback onTap,
  }) {
    final theme = Theme.of(context);
    return CustomCard(
      onTap: onTap,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            padding: const EdgeInsets.all(10.0),
            decoration: BoxDecoration(
              color: color.withOpacity(0.12),
              borderRadius: AppTokens.borderRadiusSm,
            ),
            child: Icon(icon, size: 20, color: color),
          ),
          const SizedBox(width: AppTokens.space12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title, style: theme.textTheme.titleMedium),
                const SizedBox(height: AppTokens.space4),
                Text(subtitle, style: theme.textTheme.bodyMedium),
              ],
            ),
          ),
          const Icon(Icons.chevron_right, size: 18, color: AppTokens.darkTextMuted),
        ],
      ),
    );
  }
}
