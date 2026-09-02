import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../models/conversation.dart';
import '../widgets/status_badge.dart';

/// Claude Code-styled chat message bubble with expandable tool execution accordions, diffs, and evidence cards.
class MessageBubble extends StatefulWidget {
  final ChatMessage message;
  final Function(String action, dynamic payload)? onAction;

  const MessageBubble({
    super.key,
    required this.message,
    this.onAction,
  });

  @override
  State<MessageBubble> createState() => _MessageBubbleState();
}

class _MessageBubbleState extends State<MessageBubble> {
  bool _isToolCardExpanded = false;

  @override
  Widget build(BuildContext context) {
    switch (widget.message.messageType) {
      case MessageType.userMessage:
        return _buildUserBubble(context);
      case MessageType.managerMessage:
        return _buildAgentBubble(
          context,
          roleTitle: 'Manager Agent',
          roleBadgeColor: AppTokens.brandPrimary,
          icon: Icons.psychology,
        );
      case MessageType.workerUpdate:
        return _buildWorkerUpdateBubble(context);
      case MessageType.workflowUpdate:
        return _buildAgentBubble(
          context,
          roleTitle: 'Workflow Orchestrator',
          roleBadgeColor: AppTokens.brandIndigo,
          icon: Icons.account_tree,
        );
      case MessageType.approvalRequest:
        return _buildApprovalBubble(context);
      case MessageType.userInputRequest:
        return _buildUserInputBubble(context);
      case MessageType.decisionRequest:
        return _buildDecisionBubble(context);
      case MessageType.completion:
        return _buildAgentBubble(
          context,
          roleTitle: 'Goal Completed',
          roleBadgeColor: AppTokens.success,
          icon: Icons.check_circle,
        );
      case MessageType.error:
        return _buildErrorBubble(context);
    }
  }

  Widget _buildUserBubble(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    return Align(
      alignment: Alignment.centerRight,
      child: Container(
        constraints: const BoxConstraints(maxWidth: 640),
        margin: const EdgeInsets.symmetric(vertical: AppTokens.space6, horizontal: AppTokens.space16),
        padding: const EdgeInsets.symmetric(horizontal: AppTokens.space16, vertical: AppTokens.space12),
        decoration: BoxDecoration(
          color: isDark ? AppTokens.darkElevated : AppTokens.lightBorderMuted,
          borderRadius: const BorderRadius.only(
            topLeft: Radius.circular(AppTokens.radiusLg),
            topRight: Radius.circular(AppTokens.radiusXs),
            bottomLeft: Radius.circular(AppTokens.radiusLg),
            bottomRight: Radius.circular(AppTokens.radiusLg),
          ),
          border: Border.all(
            color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder,
            width: 1,
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Text(
              widget.message.content,
              style: TextStyle(
                color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
                fontSize: 14,
                height: 1.45,
              ),
            ),
            const SizedBox(height: AppTokens.space4),
            Text(
              widget.message.timestamp.length >= 16 ? widget.message.timestamp.substring(11, 16) : '',
              style: TextStyle(
                fontSize: 10,
                color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildAgentBubble(
    BuildContext context, {
    required String roleTitle,
    required Color roleBadgeColor,
    required IconData icon,
  }) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        constraints: const BoxConstraints(maxWidth: 720),
        margin: const EdgeInsets.symmetric(vertical: AppTokens.space8, horizontal: AppTokens.space16),
        padding: const EdgeInsets.all(AppTokens.space16),
        decoration: BoxDecoration(
          color: isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
          borderRadius: AppTokens.borderRadiusMd,
          border: Border.all(
            color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder,
            width: 1,
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Role Header
            Row(
              children: [
                Container(
                  padding: const EdgeInsets.all(AppTokens.space4),
                  decoration: BoxDecoration(
                    color: roleBadgeColor.withOpacity(0.15),
                    borderRadius: AppTokens.borderRadiusXs,
                  ),
                  child: Icon(icon, size: 14, color: roleBadgeColor),
                ),
                const SizedBox(width: AppTokens.space8),
                Text(
                  roleTitle,
                  style: TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                    color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
                  ),
                ),
                const Spacer(),
                Text(
                  widget.message.timestamp.length >= 16 ? widget.message.timestamp.substring(11, 16) : '',
                  style: const TextStyle(fontSize: 10, color: AppTokens.darkTextMuted),
                ),
              ],
            ),
            const SizedBox(height: AppTokens.space12),

            // Content
            Text(
              widget.message.content,
              style: TextStyle(
                fontSize: 13.5,
                height: 1.5,
                color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildWorkerUpdateBubble(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final sender = widget.message.sender.toLowerCase();

    Color workerColor = AppTokens.brandIndigo;
    IconData workerIcon = Icons.engineering;
    String workerName = 'Specialist Worker';

    if (sender.contains('programmer')) {
      workerColor = AppTokens.brandIndigo;
      workerIcon = Icons.code;
      workerName = 'Specialist Programmer';
    } else if (sender.contains('researcher')) {
      workerColor = AppTokens.brandPrimary;
      workerIcon = Icons.travel_explore;
      workerName = 'Specialist Researcher';
    } else if (sender.contains('tester')) {
      workerColor = AppTokens.purple;
      workerIcon = Icons.verified;
      workerName = 'Specialist Tester';
    }

    final hasCodeOrDiff = widget.message.content.contains('```') ||
        widget.message.content.contains('---') ||
        widget.message.content.contains('+++');

    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        constraints: const BoxConstraints(maxWidth: 720),
        margin: const EdgeInsets.symmetric(vertical: AppTokens.space6, horizontal: AppTokens.space16),
        padding: const EdgeInsets.all(AppTokens.space14),
        decoration: BoxDecoration(
          color: isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
          borderRadius: AppTokens.borderRadiusMd,
          border: Border.all(
            color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder,
            width: 1,
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Header
            Row(
              children: [
                Container(
                  padding: const EdgeInsets.all(AppTokens.space4),
                  decoration: BoxDecoration(
                    color: workerColor.withOpacity(0.15),
                    borderRadius: AppTokens.borderRadiusXs,
                  ),
                  child: Icon(workerIcon, size: 14, color: workerColor),
                ),
                const SizedBox(width: AppTokens.space8),
                Text(
                  workerName,
                  style: TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                    color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
                  ),
                ),
                const Spacer(),
                Text(
                  widget.message.timestamp.length >= 16 ? widget.message.timestamp.substring(11, 16) : '',
                  style: const TextStyle(fontSize: 10, color: AppTokens.darkTextMuted),
                ),
              ],
            ),
            const SizedBox(height: AppTokens.space10),

            // Content
            Text(
              widget.message.content,
              style: TextStyle(
                fontSize: 13.5,
                height: 1.45,
                color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
              ),
            ),

            // Optional Claude Code collapsible Tool/Diff box
            if (hasCodeOrDiff) ...[
              const SizedBox(height: AppTokens.space10),
              Material(
                color: Colors.transparent,
                child: InkWell(
                  onTap: () => setState(() => _isToolCardExpanded = !_isToolCardExpanded),
                  borderRadius: AppTokens.borderRadiusSm,
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: AppTokens.space10, vertical: AppTokens.space6),
                    decoration: BoxDecoration(
                      color: isDark ? AppTokens.darkElevated : AppTokens.lightBorderMuted,
                      borderRadius: AppTokens.borderRadiusSm,
                      border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(
                          _isToolCardExpanded ? Icons.expand_less : Icons.expand_more,
                          size: 14,
                          color: AppTokens.brandPrimary,
                        ),
                        const SizedBox(width: AppTokens.space6),
                        Text(
                          _isToolCardExpanded ? 'Hide Scoped Changes / Details' : 'View Scoped Changes / Details',
                          style: TextStyle(
                            fontSize: 11,
                            fontWeight: FontWeight.w500,
                            color: isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildApprovalBubble(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        constraints: const BoxConstraints(maxWidth: 720),
        margin: const EdgeInsets.symmetric(vertical: AppTokens.space8, horizontal: AppTokens.space16),
        padding: const EdgeInsets.all(AppTokens.space16),
        decoration: BoxDecoration(
          color: isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
          borderRadius: AppTokens.borderRadiusMd,
          border: Border.all(color: AppTokens.warning.withOpacity(0.5), width: 1),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.shield_outlined, size: 16, color: AppTokens.warning),
                const SizedBox(width: AppTokens.space8),
                const Text('Consequential Action Approval Required', style: TextStyle(fontSize: 13, fontWeight: FontWeight.bold, color: AppTokens.warning)),
              ],
            ),
            const SizedBox(height: AppTokens.space10),
            Text(widget.message.content, style: const TextStyle(fontSize: 13.5, height: 1.4)),
            const SizedBox(height: AppTokens.space16),
            Row(
              children: [
                ElevatedButton(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AppTokens.success,
                    foregroundColor: Colors.white,
                    shape: const RoundedRectangleBorder(borderRadius: AppTokens.borderRadiusSm),
                  ),
                  onPressed: () => widget.onAction?.call('APPROVE', widget.message.metadata['approval_id']),
                  child: const Text('Approve Action'),
                ),
                const SizedBox(width: AppTokens.space12),
                OutlinedButton(
                  style: OutlinedButton.styleFrom(
                    foregroundColor: AppTokens.danger,
                    shape: const RoundedRectangleBorder(borderRadius: AppTokens.borderRadiusSm),
                  ),
                  onPressed: () => widget.onAction?.call('REJECT', widget.message.metadata['approval_id']),
                  child: const Text('Reject'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildUserInputBubble(BuildContext context) {
    return _buildAgentBubble(
      context,
      roleTitle: 'Question for Operator',
      roleBadgeColor: AppTokens.info,
      icon: Icons.help_outline,
    );
  }

  Widget _buildDecisionBubble(BuildContext context) {
    return _buildAgentBubble(
      context,
      roleTitle: 'Architectural Decision Needed',
      roleBadgeColor: AppTokens.purple,
      icon: Icons.alt_route,
    );
  }

  Widget _buildErrorBubble(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;

    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        constraints: const BoxConstraints(maxWidth: 720),
        margin: const EdgeInsets.symmetric(vertical: AppTokens.space6, horizontal: AppTokens.space16),
        padding: const EdgeInsets.all(AppTokens.space14),
        decoration: BoxDecoration(
          color: isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
          borderRadius: AppTokens.borderRadiusMd,
          border: Border.all(color: AppTokens.danger.withOpacity(0.5), width: 1),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Icon(Icons.error_outline, size: 16, color: AppTokens.danger),
            const SizedBox(width: AppTokens.space10),
            Expanded(
              child: Text(
                widget.message.content,
                style: const TextStyle(fontSize: 13, color: AppTokens.danger, height: 1.4),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
