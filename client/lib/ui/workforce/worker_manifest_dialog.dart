import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../models/worker.dart';
import '../widgets/status_badge.dart';

class WorkerManifestDialog extends StatelessWidget {
  final WorkerInfo worker;

  const WorkerManifestDialog({super.key, required this.worker});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    return AlertDialog(
      title: Row(
        children: [
          const Icon(Icons.badge_outlined, size: 22, color: AppTokens.brandPrimaryLight),
          const SizedBox(width: AppTokens.space8),
          Text(worker.name),
          const Spacer(),
          StatusBadge(status: worker.status, isSmall: true),
        ],
      ),
      content: SizedBox(
        width: 520,
        child: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('Role: ${worker.role}', style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13)),
              const SizedBox(height: AppTokens.space4),
              Text(
                worker.description.isNotEmpty ? worker.description : 'Specialist workforce agent.',
                style: theme.textTheme.bodyMedium,
              ),
              const Divider(height: AppTokens.space24),

              // Capabilities
              Text('Advertised Capabilities', style: theme.textTheme.titleSmall),
              const SizedBox(height: AppTokens.space8),
              Wrap(
                spacing: 6.0,
                runSpacing: 6.0,
                children: worker.capabilities.map((cap) {
                  return Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(
                      color: AppTokens.brandPrimary.withOpacity(0.12),
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: Text(cap, style: const TextStyle(fontSize: 11, color: AppTokens.brandPrimaryLight)),
                  );
                }).toList(),
              ),
              const SizedBox(height: AppTokens.space16),

              // Tool Permissions
              Text('Authorized Tool Categories', style: theme.textTheme.titleSmall),
              const SizedBox(height: AppTokens.space8),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(AppTokens.space12),
                decoration: BoxDecoration(
                  color: isDark ? AppTokens.darkSurface : AppTokens.lightBorder.withOpacity(0.3),
                  borderRadius: AppTokens.borderRadiusSm,
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    _buildPermissionItem(Icons.folder_open, 'Filesystem: Read / List (Workspace scoped)'),
                    _buildPermissionItem(Icons.terminal, 'Shell: Sandboxed Execution (with timeout limits)'),
                    _buildPermissionItem(Icons.fact_check, 'Verification: Test execution & Evidence recording'),
                  ],
                ),
              ),
              const SizedBox(height: AppTokens.space16),

              // Model & Routing Preferences
              Text('Inference Preferences', style: theme.textTheme.titleSmall),
              const SizedBox(height: AppTokens.space8),
              Text(
                'Preferred Model Tier: High Reasoning • Context Window: 32K+ Tokens • Structured Output: Required',
                style: theme.textTheme.bodySmall,
              ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Close'),
        ),
      ],
    );
  }

  Widget _buildPermissionItem(IconData icon, String text) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        children: [
          Icon(icon, size: 14, color: AppTokens.darkTextMuted),
          const SizedBox(width: AppTokens.space8),
          Expanded(child: Text(text, style: const TextStyle(fontSize: 12))),
        ],
      ),
    );
  }
}
