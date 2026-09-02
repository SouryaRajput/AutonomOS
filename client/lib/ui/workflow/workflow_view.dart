import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../models/task.dart';
import '../../state/workflow_controller.dart';
import '../widgets/custom_card.dart';
import '../widgets/empty_state.dart';
import '../widgets/status_badge.dart';

class WorkflowView extends StatelessWidget {
  final WorkflowController controller;

  const WorkflowView({super.key, required this.controller});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        if (controller.isLoading && controller.tasks.isEmpty) {
          return const Center(child: CircularProgressIndicator());
        }

        final plan = controller.activePlan;
        final tasks = controller.tasks;

        return SingleChildScrollView(
          padding: const EdgeInsets.all(AppTokens.space24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Header
              Row(
                children: [
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        plan?.objective.isNotEmpty == true ? plan!.objective : 'Active Workflow',
                        style: theme.textTheme.headlineMedium,
                      ),
                      const SizedBox(height: AppTokens.space4),
                      Text(
                        'Sequential milestones and specialist task executions',
                        style: theme.textTheme.bodyMedium,
                      ),
                    ],
                  ),
                  const Spacer(),
                  OutlinedButton.icon(
                    onPressed: controller.stepManager,
                    icon: const Icon(Icons.play_arrow_outlined, size: 16),
                    label: const Text('Step Manager'),
                  ),
                ],
              ),
              const SizedBox(height: AppTokens.space24),

              // Human-readable Milestones Stepper
              if (plan?.milestones.isNotEmpty == true) ...[
                Text('Milestones', style: theme.textTheme.titleMedium),
                const SizedBox(height: AppTokens.space12),
                CustomCard(
                  child: Column(
                    children: plan!.milestones.asMap().entries.map((entry) {
                      final idx = entry.key;
                      final title = entry.value;
                      final isCompleted = idx < (controller.managerStatus?.totalCycles ?? 0);
                      final isCurrent = idx == (controller.managerStatus?.totalCycles ?? 0);

                      IconData icon;
                      Color iconColor;
                      if (isCompleted) {
                        icon = Icons.check_circle;
                        iconColor = AppTokens.success;
                      } else if (isCurrent) {
                        icon = Icons.radio_button_checked;
                        iconColor = AppTokens.brandPrimaryLight;
                      } else {
                        icon = Icons.radio_button_unchecked;
                        iconColor = AppTokens.darkTextMuted;
                      }

                      return Padding(
                        padding: const EdgeInsets.symmetric(vertical: AppTokens.space8),
                        child: Row(
                          children: [
                            Icon(icon, size: 20, color: iconColor),
                            const SizedBox(width: AppTokens.space12),
                            Text(
                              title,
                              style: TextStyle(
                                fontSize: 14,
                                fontWeight: isCurrent ? FontWeight.w600 : FontWeight.normal,
                                color: isCompleted || isCurrent ? null : AppTokens.darkTextMuted,
                              ),
                            ),
                            const Spacer(),
                            if (isCurrent)
                              const StatusBadge(status: 'RUNNING', isSmall: true)
                            else if (isCompleted)
                              const StatusBadge(status: 'COMPLETED', isSmall: true)
                            else
                              const StatusBadge(status: 'PENDING', isSmall: true),
                          ],
                        ),
                      );
                    }).toList(),
                  ),
                ),
                const SizedBox(height: AppTokens.space24),
              ],

              // Task DAG Executions List
              Text('Task Executions', style: theme.textTheme.titleMedium),
              const SizedBox(height: AppTokens.space12),
              if (tasks.isEmpty)
                EmptyState(
                  icon: Icons.task_alt,
                  title: 'No tasks scheduled',
                  message: 'The Manager will generate discrete DAG tasks once given an objective.',
                )
              else
                ListView.separated(
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  itemCount: tasks.length,
                  separatorBuilder: (_, __) => const SizedBox(height: AppTokens.space8),
                  itemBuilder: (context, index) {
                    final t = tasks[index];
                    return _buildTaskCard(context, t);
                  },
                ),
            ],
          ),
        );
      },
    );
  }

  Widget _buildTaskCard(BuildContext context, TaskItem task) {
    final theme = Theme.of(context);
    return CustomCard(
      onTap: () => controller.selectTask(task),
      child: Row(
        children: [
          Container(
            width: 32,
            height: 32,
            decoration: BoxDecoration(
              color: AppTokens.brandPrimary.withOpacity(0.1),
              borderRadius: AppTokens.borderRadiusSm,
            ),
            child: Center(
              child: Text(
                '${task.priority}',
                style: const TextStyle(fontWeight: FontWeight.w700, color: AppTokens.brandPrimaryLight),
              ),
            ),
          ),
          const SizedBox(width: AppTokens.space12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(task.title, style: theme.textTheme.titleMedium),
                if (task.objective.isNotEmpty) ...[
                  const SizedBox(height: 2),
                  Text(task.objective, style: theme.textTheme.bodyMedium, maxLines: 1, overflow: TextOverflow.ellipsis),
                ],
                if (task.assignedWorkerName != null) ...[
                  const SizedBox(height: 4),
                  Row(
                    children: [
                      const Icon(Icons.person_outline, size: 12, color: AppTokens.darkTextMuted),
                      const SizedBox(width: 4),
                      Text(task.assignedWorkerName!, style: theme.textTheme.labelSmall),
                    ],
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(width: AppTokens.space12),
          StatusBadge(status: task.status),
        ],
      ),
    );
  }
}
