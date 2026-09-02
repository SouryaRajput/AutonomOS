import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';

/// Compact Status Bar.
/// Minimal metadata footer displaying current project, active state, worker counts, and task inspector trigger.
class CompactStatusBar extends StatelessWidget {
  final String projectName;
  final String stateName;
  final int activeWorkers;
  final int plannedTasks;
  final VoidCallback? onInspectTasks;

  const CompactStatusBar({
    super.key,
    required this.projectName,
    this.stateName = 'Planning',
    this.activeWorkers = 0,
    this.plannedTasks = 0,
    this.onInspectTasks,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: AppTokens.space16, vertical: AppTokens.space10),
      decoration: BoxDecoration(
        color: isDark ? AppTokens.darkSurface.withOpacity(0.4) : AppTokens.lightElevated,
        borderRadius: AppTokens.borderRadiusMd,
        border: Border.all(
          color: isDark ? AppTokens.darkBorder.withOpacity(0.6) : AppTokens.lightBorder,
        ),
      ),
      child: Row(
        children: [
          // Project Name
          Icon(
            Icons.folder_outlined,
            size: 13,
            color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
          ),
          const SizedBox(width: 5),
          Text(
            projectName,
            style: TextStyle(
              fontSize: 11,
              fontWeight: FontWeight.w600,
              color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
            ),
          ),
          const SizedBox(width: 8),

          Text('•', style: TextStyle(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder)),
          const SizedBox(width: 8),

          // State
          Text(
            stateName,
            style: TextStyle(
              fontSize: 11,
              color: isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary,
            ),
          ),
          const SizedBox(width: 8),

          Text('•', style: TextStyle(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder)),
          const SizedBox(width: 8),

          // Workers
          Text(
            '$activeWorkers active workers',
            style: TextStyle(
              fontSize: 11,
              color: isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary,
            ),
          ),

          const Spacer(),

          // Inspect Tasks Deep-Dive Trigger Button
          if (plannedTasks > 0)
            Material(
              color: Colors.transparent,
              child: InkWell(
                onTap: onInspectTasks,
                borderRadius: AppTokens.borderRadiusSm,
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                  decoration: BoxDecoration(
                    color: isDark ? AppTokens.darkElevated : AppTokens.lightSurface,
                    borderRadius: AppTokens.borderRadiusSm,
                    border: Border.all(
                      color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder,
                    ),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(
                        Icons.checklist_rtl_outlined,
                        size: 13,
                        color: AppTokens.brandPrimary,
                      ),
                      const SizedBox(width: 4),
                      Text(
                        'Inspect $plannedTasks Tasks',
                        style: TextStyle(
                          fontSize: 11,
                          fontWeight: FontWeight.w500,
                          color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}
