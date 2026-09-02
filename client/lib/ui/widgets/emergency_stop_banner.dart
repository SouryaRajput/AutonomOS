import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../state/app_state.dart';
import 'emergency_stop_dialog.dart';

class EmergencyStopBanner extends StatelessWidget {
  final bool isStopped;
  final String stoppedReason;
  final String stoppedAt;
  final VoidCallback onResume;
  final VoidCallback onReviewWorkflows;

  const EmergencyStopBanner({
    super.key,
    required this.isStopped,
    this.stoppedReason = 'Emergency stop triggered by operator',
    this.stoppedAt = '',
    required this.onResume,
    required this.onReviewWorkflows,
  });

  @override
  Widget build(BuildContext context) {
    if (!isStopped) return const SizedBox.shrink();

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: AppTokens.space20, vertical: AppTokens.space12),
      decoration: BoxDecoration(
        color: AppTokens.danger,
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.3),
            blurRadius: 8,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      child: Row(
        children: [
          const Icon(Icons.error_outline, color: Colors.white, size: 24),
          const SizedBox(width: AppTokens.space12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                const Text(
                  'AUTONOMOS STOPPED',
                  style: TextStyle(
                    color: Colors.white,
                    fontWeight: FontWeight.w900,
                    fontSize: 14,
                    letterSpacing: 1.0,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  'All autonomous tasks and tool executions are halted. Reason: $stoppedReason',
                  style: const TextStyle(color: Colors.white, fontSize: 12),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ],
            ),
          ),
          const SizedBox(width: AppTokens.space16),
          OutlinedButton(
            onPressed: onReviewWorkflows,
            style: OutlinedButton.styleFrom(
              foregroundColor: Colors.white,
              side: const BorderSide(color: Colors.white70),
              padding: const EdgeInsets.symmetric(horizontal: AppTokens.space12, vertical: 8),
            ),
            child: const Text('Review', style: TextStyle(fontSize: 12)),
          ),
          const SizedBox(width: AppTokens.space8),
          ElevatedButton.icon(
            onPressed: onResume,
            icon: const Icon(Icons.play_arrow, size: 14),
            label: const Text('Resume Workforce', style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold)),
            style: ElevatedButton.styleFrom(
              backgroundColor: Colors.white,
              foregroundColor: AppTokens.danger,
              padding: const EdgeInsets.symmetric(horizontal: AppTokens.space12, vertical: 8),
            ),
          ),
        ],
      ),
    );
  }
}
