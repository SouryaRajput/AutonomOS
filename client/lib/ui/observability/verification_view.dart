import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../models/verification.dart';
import '../widgets/custom_card.dart';
import '../widgets/empty_state.dart';
import '../widgets/status_badge.dart';

class VerificationView extends StatelessWidget {
  final VerificationReportModel? report;
  final String? programmerClaim;
  final String? testerEvaluation;

  const VerificationView({
    super.key,
    this.report,
    this.programmerClaim,
    this.testerEvaluation,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    if (report == null) {
      return EmptyState(
        icon: Icons.verified_user_outlined,
        title: 'No Verification Report Available',
        message: 'Deterministic verification executes after tasks complete to validate success criteria.',
      );
    }

    final rep = report!;

    return SingleChildScrollView(
      padding: const EdgeInsets.all(AppTokens.space24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header
          Row(
            children: [
              Text('Deterministic Verification Gate', style: theme.textTheme.headlineMedium),
              const Spacer(),
              _buildVerificationStateBadge(rep.state),
            ],
          ),
          const SizedBox(height: AppTokens.space4),
          Text(
            'Authoritative runtime evaluation comparing worker claims against deterministic evidence',
            style: theme.textTheme.bodyMedium,
          ),
          const SizedBox(height: AppTokens.space24),

          // Multi-party Comparison Card (Implementation Claim vs QA vs Verification)
          CustomCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Triangulated Execution vs Truth', style: theme.textTheme.titleMedium),
                const SizedBox(height: AppTokens.space16),

                // 1. Programmer Claim
                _buildComparisonRow(
                  context,
                  role: 'Programmer Claim',
                  icon: Icons.code,
                  iconColor: AppTokens.success,
                  text: programmerClaim ?? 'Task implementation finished; files modified and diff generated.',
                  badgeStatus: 'CLAIM_SUBMITTED',
                ),
                const Divider(height: AppTokens.space24),

                // 2. Tester QA Evaluation
                _buildComparisonRow(
                  context,
                  role: 'Tester Evaluation',
                  icon: Icons.bug_report_outlined,
                  iconColor: AppTokens.warning,
                  text: testerEvaluation ?? 'Independent test suite executed; 0 defects diagnosed.',
                  badgeStatus: rep.failedChecks > 0 ? 'DEFECTS_FOUND' : 'QA_PASSED',
                ),
                const Divider(height: AppTokens.space24),

                // 3. Authoritative Verification Gate
                _buildComparisonRow(
                  context,
                  role: 'Authoritative Gate',
                  icon: Icons.shield_outlined,
                  iconColor: _getVerificationColor(rep.state),
                  text: rep.summary.isNotEmpty ? rep.summary : 'All mandatory success criteria evaluated by deterministic check adapters.',
                  customBadge: _buildVerificationStateBadge(rep.state, isSmall: true),
                ),
              ],
            ),
          ),
          const SizedBox(height: AppTokens.space24),

          // Discrete Verification Checks List
          Text('Discrete Verification Checks (${rep.passedChecks}/${rep.totalChecks} Passed)', style: theme.textTheme.titleMedium),
          const SizedBox(height: AppTokens.space12),

          ListView.separated(
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            itemCount: rep.checks.length,
            separatorBuilder: (_, __) => const SizedBox(height: AppTokens.space8),
            itemBuilder: (context, index) {
              final check = rep.checks[index];
              return CustomCard(
                child: Row(
                  children: [
                    Icon(
                      check.status.toUpperCase() == 'PASSED' ? Icons.check_circle : Icons.cancel,
                      size: 20,
                      color: check.status.toUpperCase() == 'PASSED' ? AppTokens.success : AppTokens.danger,
                    ),
                    const SizedBox(width: AppTokens.space12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Row(
                            children: [
                              Text(check.description, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
                              if (check.required) ...[
                                const SizedBox(width: 6.0),
                                const Text(
                                  '(Required)',
                                  style: TextStyle(color: AppTokens.danger, fontSize: 10, fontWeight: FontWeight.bold),
                                ),
                              ],
                            ],
                          ),
                          const SizedBox(height: 2),
                          Text(
                            'Adapter: ${check.checkType} • Duration: ${check.durationMs.toStringAsFixed(1)}ms',
                            style: theme.textTheme.labelSmall,
                          ),
                          if (check.errorMessage != null && check.errorMessage!.isNotEmpty) ...[
                            const SizedBox(height: 4),
                            Text(
                              check.errorMessage!,
                              style: const TextStyle(color: AppTokens.danger, fontSize: 12),
                            ),
                          ],
                        ],
                      ),
                    ),
                    const SizedBox(width: AppTokens.space12),
                    StatusBadge(status: check.status, isSmall: true),
                  ],
                ),
              );
            },
          ),
        ],
      ),
    );
  }

  Widget _buildComparisonRow(
    BuildContext context, {
    required String role,
    required IconData icon,
    required Color iconColor,
    required String text,
    String? badgeStatus,
    Widget? customBadge,
  }) {
    final theme = Theme.of(context);
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          padding: const EdgeInsets.all(AppTokens.space8),
          decoration: BoxDecoration(
            color: iconColor.withOpacity(0.12),
            borderRadius: AppTokens.borderRadiusSm,
          ),
          child: Icon(icon, size: 18, color: iconColor),
        ),
        const SizedBox(width: AppTokens.space12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(role, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 12, color: AppTokens.darkTextMuted)),
              const SizedBox(height: 2),
              Text(text, style: theme.textTheme.bodyLarge),
            ],
          ),
        ),
        const SizedBox(width: AppTokens.space12),
        if (customBadge != null)
          customBadge
        else if (badgeStatus != null)
          StatusBadge(status: badgeStatus, isSmall: true),
      ],
    );
  }

  Widget _buildVerificationStateBadge(VerificationState state, {bool isSmall = false}) {
    Color bg;
    Color fg;
    IconData icon;

    switch (state) {
      case VerificationState.verified:
        bg = AppTokens.success.withOpacity(0.15);
        fg = AppTokens.success;
        icon = Icons.verified;
        break;
      case VerificationState.failed:
        bg = AppTokens.danger.withOpacity(0.15);
        fg = AppTokens.danger;
        icon = Icons.gpp_bad;
        break;
      case VerificationState.partiallyVerified:
        bg = AppTokens.warning.withOpacity(0.15);
        fg = AppTokens.warning;
        icon = Icons.published_with_changes;
        break;
      case VerificationState.blocked:
        bg = AppTokens.danger.withOpacity(0.15);
        fg = AppTokens.danger;
        icon = Icons.block;
        break;
      case VerificationState.inconclusive:
      case VerificationState.pending:
      case VerificationState.running:
      default:
        bg = AppTokens.info.withOpacity(0.15);
        fg = AppTokens.info;
        icon = Icons.shield_outlined;
        break;
    }

    final double fontSize = isSmall ? 11 : 13;
    final double iconSize = isSmall ? 13 : 16;
    final EdgeInsets padding = isSmall
        ? const EdgeInsets.symmetric(horizontal: 6.0, vertical: 2)
        : const EdgeInsets.symmetric(horizontal: AppTokens.space12, vertical: 6.0);

    return Container(
      padding: padding,
      decoration: BoxDecoration(
        color: bg,
        borderRadius: AppTokens.borderRadiusSm,
        border: Border.all(color: fg.withOpacity(0.4), width: 1.5),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: iconSize, color: fg),
          const SizedBox(width: 6.0),
          Text(
            state.value.replaceAll('_', ' '),
            style: TextStyle(
              color: fg,
              fontSize: fontSize,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.5,
            ),
          ),
        ],
      ),
    );
  }

  Color _getVerificationColor(VerificationState state) {
    switch (state) {
      case VerificationState.verified:
        return AppTokens.success;
      case VerificationState.failed:
      case VerificationState.blocked:
        return AppTokens.danger;
      case VerificationState.partiallyVerified:
        return AppTokens.warning;
      default:
        return AppTokens.info;
    }
  }
}
