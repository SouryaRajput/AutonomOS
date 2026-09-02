import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';

class StatusBadge extends StatelessWidget {
  final String status;
  final bool isSmall;

  const StatusBadge({
    super.key,
    required this.status,
    this.isSmall = false,
  });

  @override
  Widget build(BuildContext context) {
    final statusUpper = status.toUpperCase();

    Color bg;
    Color fg;
    IconData icon;

    switch (statusUpper) {
      case 'COMPLETED':
      case 'PASSED':
      case 'SUCCESS':
      case 'APPROVED':
        bg = AppTokens.success.withOpacity(0.15);
        fg = AppTokens.success;
        icon = Icons.check_circle_outline;
        break;
      case 'RUNNING':
      case 'WORKING':
      case 'ACTIVE':
        bg = AppTokens.info.withOpacity(0.15);
        fg = AppTokens.info;
        icon = Icons.play_circle_outline;
        break;
      case 'READY':
      case 'IDLE':
        bg = AppTokens.purple.withOpacity(0.15);
        fg = AppTokens.purple;
        icon = Icons.pause_circle_outline;
        break;
      case 'BLOCKED':
      case 'AWAITING_APPROVAL':
      case 'PENDING':
      case 'WAITING':
        bg = AppTokens.warning.withOpacity(0.15);
        fg = AppTokens.warning;
        icon = Icons.hourglass_empty;
        break;
      case 'FAILED':
      case 'REJECTED':
      case 'ERROR':
        bg = AppTokens.danger.withOpacity(0.15);
        fg = AppTokens.danger;
        icon = Icons.error_outline;
        break;
      default:
        bg = Colors.grey.withOpacity(0.15);
        fg = Colors.grey;
        icon = Icons.info_outline;
    }

    final double fontSize = isSmall ? 11 : 12;
    final double iconSize = isSmall ? 12 : 14;
    final EdgeInsets padding = isSmall
        ? const EdgeInsets.symmetric(horizontal: 6.0, vertical: 2)
        : const EdgeInsets.symmetric(horizontal: AppTokens.space8, vertical: AppTokens.space4);

    return Semantics(
      label: 'Status $statusUpper',
      child: Container(
        padding: padding,
        decoration: BoxDecoration(
          color: bg,
          borderRadius: AppTokens.borderRadiusSm,
          border: Border.all(color: fg.withOpacity(0.3), width: 1),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: iconSize, color: fg),
            const SizedBox(width: AppTokens.space4),
            Text(
              statusUpper,
              style: TextStyle(
                color: fg,
                fontSize: fontSize,
                fontWeight: FontWeight.w600,
                letterSpacing: 0.3,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
