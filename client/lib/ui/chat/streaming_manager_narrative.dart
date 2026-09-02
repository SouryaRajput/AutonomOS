import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';

class NarrativePhase {
  final String title;
  final String body;
  final IconData? icon;
  final bool isLatest;

  const NarrativePhase({
    required this.title,
    required this.body,
    this.icon,
    this.isLatest = false,
  });
}

/// Streaming Manager Narrative.
/// Replaces static checklist rows with a living progressive narrative timeline.
class StreamingManagerNarrative extends StatelessWidget {
  final List<NarrativePhase> phases;

  const StreamingManagerNarrative({
    super.key,
    required this.phases,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    if (phases.isEmpty) {
      return const SizedBox.shrink();
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (int i = 0; i < phases.length; i++) ...[
          _buildPhaseBlock(context, phases[i], i, phases.length, isDark),
          if (i < phases.length - 1)
            _buildTransitionArrow(context, isDark),
        ],
      ],
    );
  }

  Widget _buildPhaseBlock(
    BuildContext context,
    NarrativePhase phase,
    int index,
    int total,
    bool isDark,
  ) {
    final isLast = index == total - 1;

    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Left Stem Indicator
        Column(
          children: [
            Container(
              width: 18,
              height: 18,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: isLast
                    ? AppTokens.brandPrimary.withOpacity(0.2)
                    : (isDark ? AppTokens.darkElevated : AppTokens.lightElevated),
                border: Border.all(
                  color: isLast
                      ? AppTokens.brandPrimary
                      : (isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
                  width: 1.5,
                ),
              ),
              child: Center(
                child: Container(
                  width: 6,
                  height: 6,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: isLast ? AppTokens.brandPrimary : AppTokens.darkTextMuted,
                  ),
                ),
              ),
            ),
          ],
        ),
        const SizedBox(width: AppTokens.space12),

        // Phase Content
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                phase.title,
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                  color: isLast
                      ? (isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary)
                      : (isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary),
                  letterSpacing: -0.1,
                ),
              ),
              if (phase.body.isNotEmpty) ...[
                const SizedBox(height: 3),
                Text(
                  phase.body,
                  style: TextStyle(
                    fontSize: 12,
                    color: isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary,
                    height: 1.4,
                  ),
                ),
              ],
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildTransitionArrow(BuildContext context, bool isDark) {
    return Padding(
      padding: const EdgeInsets.only(left: 8, top: 4, bottom: 4),
      child: Icon(
        Icons.south,
        size: 12,
        color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder,
      ),
    );
  }
}
