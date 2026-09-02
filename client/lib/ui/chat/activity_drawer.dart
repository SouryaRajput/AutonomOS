import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';

/// Task Inspection Drawer / Modal.
/// Deep-dive overlay for detailed inspection of worker contracts, acceptance criteria, and prompts.
class TaskInspectionDrawer extends StatelessWidget {
  final List<Map<String, dynamic>> tasks;
  final String projectName;
  final VoidCallback onClose;

  const TaskInspectionDrawer({
    super.key,
    required this.tasks,
    required this.projectName,
    required this.onClose,
  });

  static void show(
    BuildContext context, {
    required List<Map<String, dynamic>> tasks,
    required String projectName,
  }) {
    showDialog(
      context: context,
      builder: (context) => TaskInspectionDrawer(
        tasks: tasks,
        projectName: projectName,
        onClose: () => Navigator.of(context).pop(),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    return AlertDialog(
      backgroundColor: isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
      insetPadding: const EdgeInsets.all(AppTokens.space24),
      titlePadding: const EdgeInsets.fromLTRB(AppTokens.space20, AppTokens.space16, AppTokens.space12, AppTokens.space12),
      contentPadding: const EdgeInsets.fromLTRB(AppTokens.space20, 0, AppTokens.space20, AppTokens.space16),
      title: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Row(
            children: [
              Container(
                padding: const EdgeInsets.all(AppTokens.space6),
                decoration: BoxDecoration(
                  color: isDark ? AppTokens.darkElevated : AppTokens.lightElevated,
                  borderRadius: AppTokens.borderRadiusSm,
                ),
                child: const Icon(
                  Icons.assignment_outlined,
                  size: 15,
                  color: AppTokens.brandPrimary,
                ),
              ),
              const SizedBox(width: 8),
              Text(
                'Task Contracts & Workforce Plans',
                style: TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.w600,
                  color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
                ),
              ),
            ],
          ),
          IconButton(
            icon: const Icon(Icons.close, size: 16),
            onPressed: onClose,
          ),
        ],
      ),
      content: Container(
        width: 750,
        constraints: const BoxConstraints(maxHeight: 520),
        child: tasks.isEmpty
            ? Center(
                child: Text(
                  'No planned tasks recorded.',
                  style: TextStyle(
                    fontSize: 12,
                    color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
                  ),
                ),
              )
            : ListView.separated(
                itemCount: tasks.length,
                separatorBuilder: (_, __) => const SizedBox(height: AppTokens.space12),
                itemBuilder: (context, index) {
                  final task = tasks[index];
                  return _buildTaskContractCard(context, task, isDark);
                },
              ),
      ),
      actions: [
        TextButton(
          onPressed: onClose,
          child: const Text('Close'),
        ),
      ],
    );
  }

  Widget _buildTaskContractCard(BuildContext context, Map<String, dynamic> task, bool isDark) {
    final title = task['title'] ?? task['objective'] ?? 'Task';
    final taskId = task['task_id'] ?? task['id'] ?? 'TASK';
    final workerType = task['worker_type'] ?? task['worker'] ?? 'Worker';
    final prompt = task['worker_prompt'] as String? ?? 'No prompt attached.';
    final files = (task['relevant_files'] as List<dynamic>?)?.cast<String>() ?? [];
    final criteria = (task['acceptance_criteria'] as List<dynamic>?)?.cast<String>() ?? [];

    Color workerBadgeColor;
    if (workerType.toString().contains('Programmer')) {
      workerBadgeColor = AppTokens.brandSecondary;
    } else if (workerType.toString().contains('Tester')) {
      workerBadgeColor = AppTokens.purple;
    } else {
      workerBadgeColor = AppTokens.brandPrimary;
    }

    return Container(
      padding: const EdgeInsets.all(AppTokens.space12),
      decoration: BoxDecoration(
        color: isDark ? AppTokens.darkElevated : AppTokens.lightElevated,
        borderRadius: AppTokens.borderRadiusMd,
        border: Border.all(
          color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header
          Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(
                  color: workerBadgeColor.withOpacity(0.15),
                  borderRadius: AppTokens.borderRadiusSm,
                  border: Border.all(color: workerBadgeColor.withOpacity(0.3)),
                ),
                child: Text(
                  workerType,
                  style: TextStyle(
                    fontSize: 10,
                    fontWeight: FontWeight.w600,
                    color: workerBadgeColor,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  '$taskId: $title',
                  style: TextStyle(
                    fontSize: 12.5,
                    fontWeight: FontWeight.w600,
                    color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
                  ),
                ),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(
                  color: AppTokens.warning.withOpacity(0.15),
                  borderRadius: AppTokens.borderRadiusSm,
                ),
                child: const Text(
                  'PAUSED (Workers Inactive)',
                  style: TextStyle(
                    fontSize: 9.5,
                    fontWeight: FontWeight.w500,
                    color: AppTokens.warning,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: AppTokens.space10),

          // Scoped Files
          if (files.isNotEmpty) ...[
            Text(
              'Scoped Files:',
              style: TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.w600,
                color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
              ),
            ),
            const SizedBox(height: 4),
            Wrap(
              spacing: 6,
              runSpacing: 4,
              children: files.map((f) {
                return Container(
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                  decoration: BoxDecoration(
                    color: isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
                    borderRadius: AppTokens.borderRadiusSm,
                  ),
                  child: Text(
                    f,
                    style: TextStyle(
                      fontSize: 10.5,
                      fontFamily: 'monospace',
                      color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
                    ),
                  ),
                );
              }).toList(),
            ),
            const SizedBox(height: AppTokens.space8),
          ],

          // Acceptance Criteria Checklist
          if (criteria.isNotEmpty) ...[
            Text(
              'Acceptance Criteria:',
              style: TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.w600,
                color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
              ),
            ),
            const SizedBox(height: 4),
            ...criteria.map((c) => Padding(
                  padding: const EdgeInsets.only(bottom: 2),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Icon(Icons.check, size: 12, color: AppTokens.success),
                      const SizedBox(width: 4),
                      Expanded(
                        child: Text(
                          c,
                          style: TextStyle(
                            fontSize: 11.5,
                            color: isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary,
                          ),
                        ),
                      ),
                    ],
                  ),
                )),
            const SizedBox(height: AppTokens.space8),
          ],

          // Worker Prompt
          Text(
            'Worker Prompt Instruction:',
            style: TextStyle(
              fontSize: 11,
              fontWeight: FontWeight.w600,
              color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
            ),
          ),
          const SizedBox(height: 4),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(8),
            decoration: BoxDecoration(
              color: isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
              borderRadius: AppTokens.borderRadiusSm,
              border: Border.all(
                color: isDark ? AppTokens.darkBorder.withOpacity(0.6) : AppTokens.lightBorder,
              ),
            ),
            child: SelectableText(
              prompt,
              style: TextStyle(
                fontSize: 11,
                fontFamily: 'monospace',
                color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
                height: 1.35,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
