import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../models/worker.dart';
import '../../state/workforce_controller.dart';
import '../widgets/custom_card.dart';
import '../widgets/status_badge.dart';

import 'worker_manifest_dialog.dart';

class WorkforceView extends StatelessWidget {
  final WorkforceController controller;

  const WorkforceView({super.key, required this.controller});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        if (controller.isLoading && controller.workers.isEmpty) {
          return const Center(child: CircularProgressIndicator());
        }

        final workers = controller.workers;

        return SingleChildScrollView(
          padding: const EdgeInsets.all(AppTokens.space24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('Workforce Roster', style: theme.textTheme.headlineMedium),
              const SizedBox(height: AppTokens.space4),
              Text(
                'Deterministic specialist agents operating under Manager orchestration. Click any worker to view manifest.',
                style: theme.textTheme.bodyMedium,
              ),
              const SizedBox(height: AppTokens.space24),
              LayoutBuilder(
                builder: (context, constraints) {
                  final isWide = constraints.maxWidth > 800;
                  return Wrap(
                    spacing: AppTokens.space16,
                    runSpacing: AppTokens.space16,
                    children: workers.map((w) {
                      return SizedBox(
                        width: isWide ? (constraints.maxWidth - AppTokens.space16) / 2 : constraints.maxWidth,
                        child: _buildWorkerCard(context, w),
                      );
                    }).toList(),
                  );
                },
              ),
            ],
          ),
        );
      },
    );
  }

  Widget _buildWorkerCard(BuildContext context, WorkerInfo worker) {
    final theme = Theme.of(context);

    IconData roleIcon;
    Color roleColor;
    switch (worker.name.toLowerCase()) {
      case 'manager':
        roleIcon = Icons.psychology;
        roleColor = AppTokens.brandPrimaryLight;
        break;
      case 'researcher':
        roleIcon = Icons.search;
        roleColor = AppTokens.info;
        break;
      case 'programmer':
        roleIcon = Icons.code;
        roleColor = AppTokens.success;
        break;
      case 'tester':
        roleIcon = Icons.bug_report_outlined;
        roleColor = AppTokens.warning;
        break;
      default:
        roleIcon = Icons.smart_toy_outlined;
        roleColor = AppTokens.purple;
    }

    return CustomCard(
      onTap: () {
        showDialog(
          context: context,
          builder: (ctx) => WorkerManifestDialog(worker: worker),
        );
      },
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                padding: const EdgeInsets.all(AppTokens.space8),
                decoration: BoxDecoration(
                  color: roleColor.withOpacity(0.12),
                  borderRadius: AppTokens.borderRadiusSm,
                ),
                child: Icon(roleIcon, size: 20, color: roleColor),
              ),
              const SizedBox(width: AppTokens.space12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(worker.name, style: theme.textTheme.titleMedium),
                    Text(worker.role, style: theme.textTheme.bodyMedium),
                  ],
                ),
              ),
              StatusBadge(status: worker.status),
            ],
          ),
          const SizedBox(height: AppTokens.space12),
          Text(worker.description, style: theme.textTheme.bodyMedium),
          const SizedBox(height: AppTokens.space16),

          // Active Task
          if (worker.activeTaskId != null) ...[
            Container(
              padding: const EdgeInsets.all(AppTokens.space12),
              decoration: BoxDecoration(
                color: AppTokens.info.withOpacity(0.08),
                borderRadius: AppTokens.borderRadiusSm,
                border: Border.all(color: AppTokens.info.withOpacity(0.2)),
              ),
              child: Row(
                children: [
                  const Icon(Icons.bolt, size: 16, color: AppTokens.info),
                  const SizedBox(width: AppTokens.space8),
                  Expanded(
                    child: Text(
                      worker.activeTaskTitle ?? worker.activeTaskId!,
                      style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: AppTokens.info),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: AppTokens.space12),
          ],

          // Capabilities chips
          Wrap(
            spacing: 6.0,
            runSpacing: AppTokens.space4,
            children: worker.capabilities.map((c) {
              return Container(
                padding: const EdgeInsets.symmetric(horizontal: AppTokens.space8, vertical: 2),
                decoration: BoxDecoration(
                  color: Theme.of(context).brightness == Brightness.dark
                      ? AppTokens.darkSurface
                      : AppTokens.lightBorder.withOpacity(0.5),
                  borderRadius: AppTokens.borderRadiusXs,
                ),
                child: Text(
                  c,
                  style: const TextStyle(fontSize: 10, fontWeight: FontWeight.w500),
                ),
              );
            }).toList(),
          ),
        ],
      ),
    );
  }
}
