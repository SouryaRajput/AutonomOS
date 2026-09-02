import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../widgets/custom_card.dart';
import '../widgets/status_badge.dart';

class WorkflowResultView extends StatelessWidget {
  final bool isSuccess;
  final String title;
  final String summary;
  final int filesChanged;
  final int testsPassed;
  final int totalTests;
  final int defectsFound;
  final String? failureReason;
  final String? evidenceSummary;
  final VoidCallback? onViewChanges;
  final VoidCallback? onViewReport;
  final VoidCallback? onRollback;
  final VoidCallback? onRetry;

  const WorkflowResultView({
    super.key,
    required this.isSuccess,
    required this.title,
    required this.summary,
    this.filesChanged = 0,
    this.testsPassed = 0,
    this.totalTests = 0,
    this.defectsFound = 0,
    this.failureReason,
    this.evidenceSummary,
    this.onViewChanges,
    this.onViewReport,
    this.onRollback,
    this.onRetry,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    return SingleChildScrollView(
      padding: const EdgeInsets.all(AppTokens.space24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Banner Status
          CustomCard(
            borderColor: isSuccess ? AppTokens.success.withOpacity(0.4) : AppTokens.danger.withOpacity(0.4),
            backgroundColor: isSuccess ? AppTokens.success.withOpacity(0.08) : AppTokens.danger.withOpacity(0.08),
            child: Row(
              children: [
                Icon(
                  isSuccess ? Icons.check_circle_outline : Icons.error_outline,
                  size: 28,
                  color: isSuccess ? AppTokens.success : AppTokens.danger,
                ),
                const SizedBox(width: AppTokens.space16),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        isSuccess ? '$title Completed' : '$title Failed',
                        style: theme.textTheme.titleLarge?.copyWith(
                          color: isSuccess ? AppTokens.success : AppTokens.danger,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(summary, style: theme.textTheme.bodyLarge),
                    ],
                  ),
                ),
                StatusBadge(status: isSuccess ? 'VERIFIED' : 'FAILED'),
              ],
            ),
          ),
          const SizedBox(height: AppTokens.space24),

          if (isSuccess) ...[
            // Metrics Summary Grid
            Row(
              children: [
                Expanded(
                  child: _buildMetricCard(context, label: 'Files Modified', value: '$filesChanged', icon: Icons.description_outlined, color: AppTokens.brandPrimaryLight),
                ),
                const SizedBox(width: AppTokens.space16),
                Expanded(
                  child: _buildMetricCard(context, label: 'Tests Passed', value: '$testsPassed / $totalTests', icon: Icons.fact_check_outlined, color: AppTokens.success),
                ),
                const SizedBox(width: AppTokens.space16),
                Expanded(
                  child: _buildMetricCard(context, label: 'Verification Gate', value: 'PASSED', icon: Icons.verified, color: AppTokens.purple),
                ),
              ],
            ),
            const SizedBox(height: AppTokens.space24),

            // Milestone Checklist
            CustomCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Completed Milestones', style: theme.textTheme.titleMedium),
                  const SizedBox(height: AppTokens.space12),
                  _buildCheckItem('1. Requirements & domain context synthesized by Researcher'),
                  _buildCheckItem('2. Software changes implemented cleanly by Programmer'),
                  _buildCheckItem('3. Test suite executed independently with 0 regressions by Tester'),
                  _buildCheckItem('4. Authoritative deterministic verification passed'),
                ],
              ),
            ),
            const SizedBox(height: AppTokens.space24),

            // Primary Actions
            Row(
              children: [
                if (onViewChanges != null)
                  ElevatedButton.icon(
                    onPressed: onViewChanges,
                    icon: const Icon(Icons.difference_outlined, size: 16),
                    label: const Text('View Changes'),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: AppTokens.brandPrimary,
                      foregroundColor: Colors.white,
                    ),
                  ),
                if (onViewReport != null) ...[
                  const SizedBox(width: AppTokens.space12),
                  OutlinedButton.icon(
                    onPressed: onViewReport,
                    icon: const Icon(Icons.article_outlined, size: 16),
                    label: const Text('View Full Report'),
                  ),
                ],
              ],
            ),
          ] else ...[
            // Failure Diagnostic Breakdown
            CustomCard(
              borderColor: AppTokens.danger.withOpacity(0.3),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Failure Root Cause Diagnostic', style: theme.textTheme.titleMedium),
                  const SizedBox(height: AppTokens.space8),
                  Text(
                    failureReason ?? 'Test regression encountered during verification gate.',
                    style: const TextStyle(color: AppTokens.danger, fontSize: 13, height: 1.4),
                  ),
                  if (evidenceSummary != null) ...[
                    const SizedBox(height: AppTokens.space12),
                    Container(
                      width: double.infinity,
                      padding: const EdgeInsets.all(AppTokens.space12),
                      decoration: BoxDecoration(
                        color: isDark ? const Color(0xFF161922) : const Color(0xFFF1F5F9),
                        borderRadius: AppTokens.borderRadiusSm,
                      ),
                      child: Text(
                        evidenceSummary!,
                        style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
                      ),
                    ),
                  ],
                ],
              ),
            ),
            const SizedBox(height: AppTokens.space24),

            // Recommended Next Actions
            CustomCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Recovery Options', style: theme.textTheme.titleMedium),
                  const SizedBox(height: AppTokens.space12),
                  Text(
                    'Choose how to proceed without manual codebase restoration:',
                    style: theme.textTheme.bodyMedium,
                  ),
                  const SizedBox(height: AppTokens.space16),
                  Row(
                    children: [
                      if (onRollback != null)
                        ElevatedButton.icon(
                          onPressed: onRollback,
                          icon: const Icon(Icons.history, size: 16),
                          label: const Text('Rollback to Pre-Task Checkpoint'),
                          style: ElevatedButton.styleFrom(
                            backgroundColor: AppTokens.warning,
                            foregroundColor: Colors.black86,
                          ),
                        ),
                      if (onRetry != null) ...[
                        const SizedBox(width: AppTokens.space12),
                        OutlinedButton.icon(
                          onPressed: onRetry,
                          icon: const Icon(Icons.replay, size: 16),
                          label: const Text('Replan with Defect Constraints'),
                        ),
                      ],
                    ],
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildCheckItem(String label) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          const Icon(Icons.check_circle, size: 16, color: AppTokens.success),
          const SizedBox(width: AppTokens.space8),
          Expanded(child: Text(label, style: const TextStyle(fontSize: 13))),
        ],
      ),
    );
  }

  Widget _buildMetricCard(BuildContext context, {required String label, required String value, required IconData icon, required Color color}) {
    final theme = Theme.of(context);
    return CustomCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, size: 16, color: color),
              const SizedBox(width: 6.0),
              Text(label, style: theme.textTheme.labelSmall),
            ],
          ),
          const SizedBox(height: AppTokens.space8),
          Text(value, style: theme.textTheme.headlineMedium?.copyWith(fontSize: 20)),
        ],
      ),
    );
  }
}
