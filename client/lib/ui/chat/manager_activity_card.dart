import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../models/manager_activity.dart';

/// Backward-compatible adapter for Manager activity cards.
/// Preserves StatefulWidget identity for smooth Flutter Hot Reload and reassembly.
class ManagerActivityCard extends StatefulWidget {
  final List<ManagerActivityStep> steps;
  final String? summaryText;

  const ManagerActivityCard({
    super.key,
    required this.steps,
    this.summaryText,
  });

  @override
  State<ManagerActivityCard> createState() => _ManagerActivityCardState();
}

class _ManagerActivityCardState extends State<ManagerActivityCard> {
  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Text(
        widget.summaryText ?? 'I inspected the workspace and prepared the engineering plan.',
        style: TextStyle(
          fontSize: 13,
          color: isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary,
        ),
      ),
    );
  }
}
