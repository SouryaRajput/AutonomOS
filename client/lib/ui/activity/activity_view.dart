import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../models/event.dart';
import '../../state/activity_controller.dart';
import '../widgets/custom_card.dart';
import '../widgets/empty_state.dart';

class ActivityView extends StatelessWidget {
  final ActivityController controller;

  const ActivityView({super.key, required this.controller});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        if (controller.isLoading && controller.activities.isEmpty) {
          return const Center(child: CircularProgressIndicator());
        }

        final activities = controller.activities;

        return SingleChildScrollView(
          padding: const EdgeInsets.all(AppTokens.space24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Header
              Text('Activity Timeline', style: theme.textTheme.headlineMedium),
              const SizedBox(height: AppTokens.space4),
              Text(
                'Authoritative audit feed powered by the Stage 2 event store',
                style: theme.textTheme.bodyMedium,
              ),
              const SizedBox(height: AppTokens.space16),

              // Filter Bar
              SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: Row(
                  children: [
                    _buildFilterChip(context, 'All Workers', isSelected: controller.selectedWorkerFilter == null, onSelected: () => controller.setWorkerFilter(null)),
                    const SizedBox(width: AppTokens.space8),
                    _buildFilterChip(context, 'Manager', isSelected: controller.selectedWorkerFilter == 'worker.manager.orchestrator', onSelected: () => controller.setWorkerFilter('worker.manager.orchestrator')),
                    const SizedBox(width: AppTokens.space8),
                    _buildFilterChip(context, 'Researcher', isSelected: controller.selectedWorkerFilter == 'worker.researcher', onSelected: () => controller.setWorkerFilter('worker.researcher')),
                    const SizedBox(width: AppTokens.space8),
                    _buildFilterChip(context, 'Programmer', isSelected: controller.selectedWorkerFilter == 'worker.programmer', onSelected: () => controller.setWorkerFilter('worker.programmer')),
                    const SizedBox(width: AppTokens.space8),
                    _buildFilterChip(context, 'Tester', isSelected: controller.selectedWorkerFilter == 'worker.tester', onSelected: () => controller.setWorkerFilter('worker.tester')),
                  ],
                ),
              ),
              const SizedBox(height: AppTokens.space20),

              // Activities Timeline List
              if (activities.isEmpty)
                EmptyState(
                  icon: Icons.history,
                  title: 'No activity recorded',
                  message: 'No events match the selected filters.',
                )
              else
                ListView.separated(
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  itemCount: activities.length,
                  separatorBuilder: (_, __) => const SizedBox(height: AppTokens.space8),
                  itemBuilder: (context, index) {
                    final item = activities[index];
                    return _buildActivityTile(context, item);
                  },
                ),
            ],
          ),
        );
      },
    );
  }

  Widget _buildFilterChip(BuildContext context, String label, {required bool isSelected, required VoidCallback onSelected}) {
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

  Widget _buildActivityTile(BuildContext context, ActivityItemModel item) {
    final theme = Theme.of(context);

    Color levelColor;
    switch (item.level.toUpperCase()) {
      case 'SUCCESS':
        levelColor = AppTokens.success;
        break;
      case 'WARNING':
        levelColor = AppTokens.warning;
        break;
      case 'ERROR':
        levelColor = AppTokens.danger;
        break;
      default:
        levelColor = AppTokens.info;
    }

    return CustomCard(
      padding: const EdgeInsets.symmetric(horizontal: AppTokens.space16, vertical: AppTokens.space12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            padding: const EdgeInsets.all(AppTokens.space8),
            decoration: BoxDecoration(
              color: levelColor.withOpacity(0.12),
              borderRadius: AppTokens.borderRadiusSm,
            ),
            child: Text(item.icon, style: const TextStyle(fontSize: 16)),
          ),
          const SizedBox(width: AppTokens.space12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text(item.title, style: theme.textTheme.titleMedium),
                    const Spacer(),
                    Text(item.timestamp, style: theme.textTheme.labelSmall),
                  ],
                ),
                const SizedBox(height: 2),
                Text(item.subtitle, style: theme.textTheme.bodyMedium),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
