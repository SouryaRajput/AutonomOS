import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../models/approval.dart';
import '../../state/approval_controller.dart';
import '../widgets/custom_card.dart';
import '../widgets/empty_state.dart';
import '../widgets/status_badge.dart';

class ApprovalsView extends StatefulWidget {
  final ApprovalController controller;

  const ApprovalsView({super.key, required this.controller});

  @override
  State<ApprovalsView> createState() => _ApprovalsViewState();
}

class _ApprovalsViewState extends State<ApprovalsView> {
  final Map<String, TextEditingController> _inputControllers = {};
  final Set<String> _pendingActionIds = {};

  @override
  void dispose() {
    for (final c in _inputControllers.values) {
      c.dispose();
    }
    super.dispose();
  }

  TextEditingController _getInputController(String id) {
    return _inputControllers.putIfAbsent(id, () => TextEditingController());
  }

  Future<void> _handleGrant(String approvalId) async {
    setState(() => _pendingActionIds.add(approvalId));
    try {
      await widget.controller.grant(approvalId);
    } finally {
      if (mounted) {
        setState(() => _pendingActionIds.remove(approvalId));
      }
    }
  }

  Future<void> _handleReject(String approvalId) async {
    setState(() => _pendingActionIds.add(approvalId));
    try {
      await widget.controller.reject(approvalId, reason: 'Operator rejected action authorization');
    } finally {
      if (mounted) {
        setState(() => _pendingActionIds.remove(approvalId));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return AnimatedBuilder(
      animation: widget.controller,
      builder: (context, _) {
        if (widget.controller.isLoading &&
            widget.controller.approvals.isEmpty &&
            widget.controller.userInputs.isEmpty &&
            widget.controller.decisions.isEmpty) {
          return const Center(child: CircularProgressIndicator());
        }

        final approvals = widget.controller.approvals;
        final userInputs = widget.controller.userInputs;
        final decisions = widget.controller.decisions;

        final hasItems = approvals.isNotEmpty || userInputs.isNotEmpty || decisions.isNotEmpty;

        return SingleChildScrollView(
          padding: const EdgeInsets.all(AppTokens.space24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('Human-in-the-Loop Approval Center', style: theme.textTheme.headlineMedium),
              const SizedBox(height: AppTokens.space4),
              Text(
                'Authoritative gate for risky action authorizations, operator inputs, and architectural choices',
                style: theme.textTheme.bodyMedium,
              ),
              const SizedBox(height: AppTokens.space24),

              if (!hasItems)
                EmptyState(
                  icon: Icons.verified_user_outlined,
                  title: 'No Pending Authorizations',
                  message: 'The workforce is operating smoothly within established autonomy policies.',
                ),

              // Pending Action Approvals
              if (approvals.isNotEmpty) ...[
                Text('Action Authorizations (${approvals.length})', style: theme.textTheme.titleMedium),
                const SizedBox(height: AppTokens.space12),
                ...approvals.map((a) => _buildApprovalCard(context, a)),
                const SizedBox(height: AppTokens.space24),
              ],

              // User Input Questions
              if (userInputs.isNotEmpty) ...[
                Text('Workforce Input Requests (${userInputs.length})', style: theme.textTheme.titleMedium),
                const SizedBox(height: AppTokens.space12),
                ...userInputs.map((u) => _buildUserInputCard(context, u)),
                const SizedBox(height: AppTokens.space24),
              ],

              // Architectural Decisions
              if (decisions.isNotEmpty) ...[
                Text('Architectural & Product Decisions (${decisions.length})', style: theme.textTheme.titleMedium),
                const SizedBox(height: AppTokens.space12),
                ...decisions.map((d) => _buildDecisionCard(context, d)),
              ],
            ],
          ),
        );
      },
    );
  }

  Widget _buildApprovalCard(BuildContext context, ApprovalItem item) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final isProcessing = _pendingActionIds.contains(item.id);

    // Determine descriptive action verb
    String approveLabel = 'Approve Action';
    final actionLower = item.action.toLowerCase();
    if (actionLower.contains('delete') || actionLower.contains('rm') || actionLower.contains('remove')) {
      approveLabel = 'Approve Deletion';
    } else if (actionLower.contains('exec') || actionLower.contains('shell') || actionLower.contains('run')) {
      approveLabel = 'Approve Execution';
    } else if (actionLower.contains('write') || actionLower.contains('modify') || actionLower.contains('edit')) {
      approveLabel = 'Approve Code Modification';
    } else if (actionLower.contains('network') || actionLower.contains('http') || actionLower.contains('api')) {
      approveLabel = 'Approve External Call';
    }

    return Padding(
      padding: const EdgeInsets.only(bottom: AppTokens.space16),
      child: CustomCard(
        borderColor: AppTokens.warning.withOpacity(0.5),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Header Row: Action + Risk Badge
            Row(
              children: [
                const Icon(Icons.gavel, size: 18, color: AppTokens.warning),
                const SizedBox(width: AppTokens.space8),
                Expanded(
                  child: Text(
                    item.action,
                    style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.bold),
                  ),
                ),
                StatusBadge(status: item.riskLevel, isSmall: true),
              ],
            ),
            const SizedBox(height: AppTokens.space12),

            // Structured Details Grid (WHAT, WHY, SCOPE, EVIDENCE)
            _buildDetailRow('WHAT', 'Workforce requested execution of tool: ${item.action}'),
            const SizedBox(height: 6.0),
            _buildDetailRow('WHY', item.reason.isNotEmpty ? item.reason : 'Mandated by active milestone implementation requirements'),
            const SizedBox(height: 6.0),
            _buildDetailRow('RISK LEVEL', '${item.riskLevel} — Exceeds autonomous policy execution threshold'),
            if (item.requestedScope.isNotEmpty) ...[
              const SizedBox(height: 6.0),
              _buildDetailRow('SCOPE', item.requestedScope),
            ],
            if (item.evidence.isNotEmpty) ...[
              const SizedBox(height: 6.0),
              _buildDetailRow('EVIDENCE', item.evidence.join(', ')),
            ],

            const SizedBox(height: AppTokens.space20),

            // Action Buttons with Explicit Non-Ambiguous Labels
            Row(
              children: [
                ElevatedButton.icon(
                  onPressed: isProcessing ? null : () => _handleGrant(item.id),
                  icon: isProcessing
                      ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                      : const Icon(Icons.check, size: 16),
                  label: Text(approveLabel),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AppTokens.success,
                    foregroundColor: Colors.white,
                  ),
                ),
                const SizedBox(width: AppTokens.space12),
                OutlinedButton.icon(
                  onPressed: isProcessing ? null : () => _handleReject(item.id),
                  icon: const Icon(Icons.close, size: 16),
                  label: const Text('Reject Request'),
                  style: OutlinedButton.styleFrom(
                    foregroundColor: AppTokens.danger,
                    side: const BorderSide(color: AppTokens.danger),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildDetailRow(String label, String value) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          width: 90,
          child: Text(
            label,
            style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 11, color: AppTokens.darkTextMuted),
          ),
        ),
        Expanded(
          child: Text(
            value,
            style: const TextStyle(fontSize: 13, height: 1.3),
          ),
        ),
      ],
    );
  }

  Widget _buildUserInputCard(BuildContext context, UserInputItem item) {
    final theme = Theme.of(context);
    final textController = _getInputController(item.id);

    // Common structured option chips for natural UI selection
    final structuredOptions = _extractStructuredOptions(item.question);

    return Padding(
      padding: const EdgeInsets.only(bottom: AppTokens.space16),
      child: CustomCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.help_outline, size: 18, color: AppTokens.brandPrimaryLight),
                const SizedBox(width: AppTokens.space8),
                Text('Operator Input Requested', style: theme.textTheme.titleMedium),
              ],
            ),
            const SizedBox(height: AppTokens.space8),
            Text(item.question, style: theme.textTheme.bodyLarge),
            const SizedBox(height: AppTokens.space12),

            // Selectable Option Chips if detected
            if (structuredOptions.isNotEmpty) ...[
              Wrap(
                spacing: AppTokens.space8,
                runSpacing: AppTokens.space8,
                children: structuredOptions.map((opt) {
                  return ActionChip(
                    label: Text(opt),
                    onPressed: () {
                      textController.text = opt;
                      setState(() {});
                    },
                    backgroundColor: AppTokens.brandPrimary.withOpacity(0.12),
                    side: const BorderSide(color: AppTokens.brandPrimaryLight),
                  );
                }).toList(),
              ),
              const SizedBox(height: AppTokens.space12),
            ],

            TextField(
              controller: textController,
              decoration: const InputDecoration(
                hintText: 'Enter your answer or select from options above...',
              ),
            ),
            const SizedBox(height: AppTokens.space16),
            ElevatedButton(
              onPressed: () {
                if (textController.text.trim().isNotEmpty) {
                  widget.controller.answerInput(item.id, textController.text.trim());
                }
              },
              child: const Text('Submit Answer to Manager'),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildDecisionCard(BuildContext context, DecisionItem item) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.only(bottom: AppTokens.space16),
      child: CustomCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.alt_route, size: 18, color: AppTokens.purple),
                const SizedBox(width: AppTokens.space8),
                Text(item.title, style: theme.textTheme.titleMedium),
              ],
            ),
            if (item.rationale.isNotEmpty) ...[
              const SizedBox(height: AppTokens.space8),
              Text(item.rationale, style: theme.textTheme.bodyMedium),
            ],
            const SizedBox(height: AppTokens.space16),
            Wrap(
              spacing: AppTokens.space12,
              runSpacing: AppTokens.space12,
              children: item.options.map((opt) {
                return ElevatedButton.icon(
                  onPressed: () => widget.controller.makeDecision(item.id, opt),
                  icon: const Icon(Icons.check, size: 14),
                  label: Text('Choose $opt'),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AppTokens.purple,
                    foregroundColor: Colors.white,
                  ),
                );
              }).toList(),
            ),
          ],
        ),
      ),
    );
  }

  List<String> _extractStructuredOptions(String question) {
    final lower = question.toLowerCase();
    if (lower.contains('database') || lower.contains('db')) {
      return ['PostgreSQL', 'SQLite', 'MySQL', 'MongoDB'];
    }
    if (lower.contains('auth') || lower.contains('authentication')) {
      return ['OAuth 2.0 PKCE', 'JWT Bearer', 'Session Cookies', 'API Key'];
    }
    if (lower.contains('frontend') || lower.contains('ui')) {
      return ['Flutter', 'React', 'Vue', 'Next.js'];
    }
    return [];
  }
}
