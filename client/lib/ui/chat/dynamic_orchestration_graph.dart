import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';

enum WorkerNodeStatus {
  active,
  idle,
  inactive,
  verifying,
}

class OrchestrationNodeData {
  final String id;
  final String title;
  final String role;
  final WorkerNodeStatus status;
  final List<String> targets;

  const OrchestrationNodeData({
    required this.id,
    required this.title,
    required this.role,
    this.status = WorkerNodeStatus.inactive,
    this.targets = const [],
  });
}

/// Dynamic Orchestration Graph.
/// Compact, animated topological visualization of the active workforce and subsystem flows.
class DynamicOrchestrationGraph extends StatefulWidget {
  final List<OrchestrationNodeData> workers;
  final List<String> subsystems;
  final Function(String taskIdOrSubsystem)? onNodeTap;

  const DynamicOrchestrationGraph({
    super.key,
    this.workers = const [],
    this.subsystems = const [],
    this.onNodeTap,
  });

  @override
  State<DynamicOrchestrationGraph> createState() => _DynamicOrchestrationGraphState();
}

class _DynamicOrchestrationGraphState extends State<DynamicOrchestrationGraph>
    with SingleTickerProviderStateMixin {
  late AnimationController _pulseController;

  @override
  void initState() {
    super.initState();
    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 3),
    )..repeat();
  }

  @override
  void dispose() {
    _pulseController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    final defaultWorkers = widget.workers.isNotEmpty
        ? widget.workers
        : const [
            OrchestrationNodeData(
              id: 'worker.programmer',
              title: 'PROGRAMMER',
              role: 'Implementation',
              status: WorkerNodeStatus.inactive,
              targets: ['Components', 'Routing'],
            ),
            OrchestrationNodeData(
              id: 'worker.tester',
              title: 'TESTER',
              role: 'Verification',
              status: WorkerNodeStatus.inactive,
              targets: ['Validation'],
            ),
            OrchestrationNodeData(
              id: 'worker.researcher',
              title: 'RESEARCHER',
              role: 'Discovery',
              status: WorkerNodeStatus.inactive,
              targets: ['Context'],
            ),
          ];

    final displaySubsystems = widget.subsystems.isNotEmpty
        ? widget.subsystems
        : const ['Visual Scene', 'UI Integration', 'Verification'];

    return Container(
      margin: const EdgeInsets.symmetric(vertical: AppTokens.space12),
      padding: const EdgeInsets.all(AppTokens.space16),
      decoration: BoxDecoration(
        color: isDark ? AppTokens.darkSurface.withOpacity(0.6) : AppTokens.lightSurface,
        borderRadius: AppTokens.borderRadiusLg,
        border: Border.all(
          color: isDark ? AppTokens.darkBorder.withOpacity(0.8) : AppTokens.lightBorder,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          // Section Header
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Row(
                children: [
                  Icon(
                    Icons.hub_outlined,
                    size: 13,
                    color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
                  ),
                  const SizedBox(width: 6),
                  Text(
                    'ORCHESTRATION TOPOLOGY',
                    style: TextStyle(
                      fontSize: 10.5,
                      fontWeight: FontWeight.w600,
                      letterSpacing: 1.2,
                      color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
                    ),
                  ),
                ],
              ),
              Text(
                'Workers Disabled',
                style: TextStyle(
                  fontSize: 10,
                  fontWeight: FontWeight.w500,
                  color: AppTokens.warning.withOpacity(0.9),
                ),
              ),
            ],
          ),
          const SizedBox(height: AppTokens.space16),

          // Worker Nodes Row
          Wrap(
            spacing: AppTokens.space16,
            runSpacing: AppTokens.space12,
            alignment: WrapAlignment.center,
            children: defaultWorkers.map((w) => _buildWorkerCard(context, w, isDark)).toList(),
          ),

          const SizedBox(height: AppTokens.space16),

          // Connecting Indicator
          AnimatedBuilder(
            animation: _pulseController,
            builder: (context, child) {
              return Opacity(
                opacity: 0.5 + 0.3 * math.sin(_pulseController.value * 2 * math.pi),
                child: const Icon(
                  Icons.south,
                  size: 14,
                  color: AppTokens.brandSecondary,
                ),
              );
            },
          ),
          const SizedBox(height: AppTokens.space8),

          // Target Subsystems Row
          Wrap(
            spacing: AppTokens.space8,
            runSpacing: AppTokens.space6,
            alignment: WrapAlignment.center,
            children: displaySubsystems.map((sub) => _buildSubsystemPill(context, sub, isDark)).toList(),
          ),
        ],
      ),
    );
  }

  Widget _buildWorkerCard(BuildContext context, OrchestrationNodeData worker, bool isDark) {
    Color statusColor;
    String statusText;
    bool isActive = false;

    switch (worker.status) {
      case WorkerNodeStatus.active:
        statusColor = AppTokens.diffAdded;
        statusText = '● Working';
        isActive = true;
        break;
      case WorkerNodeStatus.verifying:
        statusColor = AppTokens.brandIndigo;
        statusText = '● Verifying';
        isActive = true;
        break;
      case WorkerNodeStatus.idle:
        statusColor = AppTokens.brandSecondary;
        statusText = '○ Idle';
        break;
      case WorkerNodeStatus.inactive:
        statusColor = isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted;
        statusText = '○ Inactive';
        break;
    }

    return InkWell(
      onTap: () {
        if (widget.onNodeTap != null) {
          widget.onNodeTap!(worker.id);
        }
      },
      borderRadius: AppTokens.borderRadiusMd,
      child: Container(
        width: 140,
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
        decoration: BoxDecoration(
          color: isDark ? AppTokens.darkElevated : AppTokens.lightElevated,
          borderRadius: AppTokens.borderRadiusMd,
          border: Border.all(
            color: isActive
                ? statusColor.withOpacity(0.5)
                : (isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
            width: isActive ? 1.5 : 1.0,
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Expanded(
                  child: Text(
                    worker.title,
                    style: TextStyle(
                      fontSize: 10.5,
                      fontWeight: FontWeight.w700,
                      letterSpacing: 0.5,
                      color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                Text(
                  statusText,
                  style: TextStyle(
                    fontSize: 9.5,
                    fontWeight: FontWeight.w600,
                    color: statusColor,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 2),
            Text(
              worker.role,
              style: TextStyle(
                fontSize: 9.5,
                color: isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary,
              ),
              overflow: TextOverflow.ellipsis,
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildSubsystemPill(BuildContext context, String title, bool isDark) {
    return InkWell(
      onTap: () {
        if (widget.onNodeTap != null) {
          widget.onNodeTap!(title);
        }
      },
      borderRadius: AppTokens.borderRadiusSm,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          color: isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
          borderRadius: AppTokens.borderRadiusSm,
          border: Border.all(
            color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder,
          ),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              Icons.layers_outlined,
              size: 11,
              color: AppTokens.brandSecondary,
            ),
            const SizedBox(width: 5),
            Text(
              title,
              style: TextStyle(
                fontSize: 10.5,
                fontFamily: 'monospace',
                color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
