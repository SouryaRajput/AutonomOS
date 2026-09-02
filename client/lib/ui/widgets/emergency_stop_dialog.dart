import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';

class EmergencyStopConfirmDialog extends StatefulWidget {
  final Future<void> Function(String reason) onConfirm;

  const EmergencyStopConfirmDialog({super.key, required this.onConfirm});

  @override
  State<EmergencyStopConfirmDialog> createState() => _EmergencyStopConfirmDialogState();
}

class _EmergencyStopConfirmDialogState extends State<EmergencyStopConfirmDialog> {
  final TextEditingController _reasonController = TextEditingController(text: 'Operator initiated emergency workforce halt');
  bool _isStopping = false;

  @override
  void dispose() {
    _reasonController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    return AlertDialog(
      title: Row(
        children: [
          const Icon(Icons.warning_amber_rounded, color: AppTokens.danger, size: 24),
          const SizedBox(width: AppTokens.space8),
          const Text('Stop AutonomOS?'),
        ],
      ),
      content: SizedBox(
        width: 440,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'This will immediately prevent all new consequential actions, cancel running tool executions, and freeze the autonomous workforce.',
              style: theme.textTheme.bodyLarge,
            ),
            const SizedBox(height: AppTokens.space16),
            TextField(
              controller: _reasonController,
              decoration: const InputDecoration(
                labelText: 'Halt Reason',
                hintText: 'Describe reason for emergency halt...',
              ),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: _isStopping ? null : () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        ElevatedButton.icon(
          onPressed: _isStopping
              ? null
              : () async {
                  setState(() => _isStopping = true);
                  await widget.onConfirm(_reasonController.text.trim());
                  if (mounted) Navigator.of(context).pop();
                },
          icon: _isStopping
              ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
              : const Icon(Icons.stop_circle_outlined, size: 16),
          label: const Text('Emergency Stop'),
          style: ElevatedButton.styleFrom(
            backgroundColor: AppTokens.danger,
            foregroundColor: Colors.white,
          ),
        ),
      ],
    );
  }
}

class EmergencyResumeDialog extends StatefulWidget {
  final String stoppedReason;
  final Future<void> Function() onResume;
  final VoidCallback onReview;

  const EmergencyResumeDialog({
    super.key,
    required this.stoppedReason,
    required this.onResume,
    required this.onReview,
  });

  @override
  State<EmergencyResumeDialog> createState() => _EmergencyResumeDialogState();
}

class _EmergencyResumeDialogState extends State<EmergencyResumeDialog> {
  bool _isResuming = false;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return AlertDialog(
      title: Row(
        children: [
          const Icon(Icons.restore, color: AppTokens.warning, size: 24),
          const SizedBox(width: AppTokens.space8),
          const Text('Resume Workforce Operations'),
        ],
      ),
      content: SizedBox(
        width: 440,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Emergency stop is currently active. Review active task states, checkpoints, and policy rules before resuming autonomous operations.',
              style: TextStyle(height: 1.4),
            ),
            const SizedBox(height: AppTokens.space12),
            Container(
              padding: const EdgeInsets.all(AppTokens.space12),
              decoration: BoxDecoration(
                color: AppTokens.danger.withOpacity(0.1),
                borderRadius: AppTokens.borderRadiusSm,
                border: Border.all(color: AppTokens.danger.withOpacity(0.3)),
              ),
              child: Text(
                'Original Halt Reason: ${widget.stoppedReason}',
                style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: AppTokens.danger),
              ),
            ),
          ],
        ),
      ),
      actions: [
        OutlinedButton(
          onPressed: () {
            Navigator.of(context).pop();
            widget.onReview();
          },
          child: const Text('Review Workflow State'),
        ),
        ElevatedButton.icon(
          onPressed: _isResuming
              ? null
              : () async {
                  setState(() => _isResuming = true);
                  await widget.onResume();
                  if (mounted) Navigator.of(context).pop();
                },
          icon: _isResuming
              ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
              : const Icon(Icons.play_arrow, size: 16),
          label: const Text('Resume Operations'),
          style: ElevatedButton.styleFrom(
            backgroundColor: AppTokens.success,
            foregroundColor: Colors.white,
          ),
        ),
      ],
    );
  }
}
