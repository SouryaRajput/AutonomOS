import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../state/app_state.dart';
import '../widgets/custom_card.dart';

class NotificationCenterDialog extends StatelessWidget {
  final AppState appState;

  const NotificationCenterDialog({super.key, required this.appState});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    final List<Map<String, dynamic>> notifications = [];

    // Check emergency stop
    if (appState.isEmergencyStopped) {
      notifications.add({
        'title': 'Emergency Stop Active',
        'subtitle': 'Workforce operations are halted: ${appState.emergencyStopReason}',
        'icon': Icons.stop_circle_outlined,
        'color': AppTokens.danger,
        'route': 'autonomos://workflow',
      });
    }

    // Check manager status
    final mgr = appState.managerStatus;
    if (mgr != null && mgr.pendingUserInput) {
      notifications.add({
        'title': 'User Clarification Required',
        'subtitle': 'Manager paused awaiting operator input',
        'icon': Icons.help_outline,
        'color': AppTokens.brandPrimaryLight,
        'route': 'autonomos://approvals',
      });
    }

    return AlertDialog(
      title: Row(
        children: [
          const Icon(Icons.notifications_none_outlined, size: 22, color: AppTokens.brandPrimaryLight),
          const SizedBox(width: AppTokens.space8),
          const Text('Workforce Notifications'),
          const Spacer(),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
            decoration: BoxDecoration(
              color: AppTokens.brandPrimary.withOpacity(0.15),
              borderRadius: BorderRadius.circular(4),
            ),
            child: Text(
              '${notifications.length} Active',
              style: const TextStyle(color: AppTokens.brandPrimaryLight, fontSize: 11, fontWeight: FontWeight.bold),
            ),
          ),
        ],
      ),
      content: SizedBox(
        width: 480,
        child: notifications.isEmpty
            ? Padding(
                padding: const EdgeInsets.symmetric(vertical: AppTokens.space24),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Icon(Icons.check_circle_outline, size: 40, color: AppTokens.success),
                    const SizedBox(height: AppTokens.space12),
                    Text('All Quiet', style: theme.textTheme.titleMedium),
                    const SizedBox(height: AppTokens.space4),
                    const Text('No urgent approvals or critical defects require attention.', style: TextStyle(fontSize: 12)),
                  ],
                ),
              )
            : ListView.separated(
                shrinkWrap: true,
                itemCount: notifications.length,
                separatorBuilder: (_, __) => const SizedBox(height: 8),
                itemBuilder: (context, index) {
                  final notif = notifications[index];
                  final color = notif['color'] as Color? ?? AppTokens.brandPrimaryLight;
                  return ListTile(
                    tileColor: color.withOpacity(0.08),
                    shape: RoundedRectangleBorder(
                      borderRadius: AppTokens.borderRadiusSm,
                      side: BorderSide(color: color.withOpacity(0.3)),
                    ),
                    leading: Icon(notif['icon'] as IconData? ?? Icons.info, color: color, size: 20),
                    title: Text(notif['title'] ?? '', style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13)),
                    subtitle: Text(notif['subtitle'] ?? '', style: const TextStyle(fontSize: 11)),
                    trailing: const Icon(Icons.chevron_right, size: 16),
                    onTap: () {
                      Navigator.of(context).pop();
                      final route = notif['route'] as String? ?? '';
                      appState.deepLinkNavigator.handleDeepLink(route);
                    },
                  );
                },
              ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Dismiss'),
        ),
      ],
    );
  }
}
