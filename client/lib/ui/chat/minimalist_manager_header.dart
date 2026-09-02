import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';

/// Minimalist, accessible Manager State Indicator.
/// Renders subtle gray status typography for active in-flight states
/// without heavy cards, borders, or excessive motion.
class MinimalistManagerStateHeader extends StatefulWidget {
  final String activeStateText;
  final bool isLive;
  final List<String> completedSummaries;

  const MinimalistManagerStateHeader({
    super.key,
    required this.activeStateText,
    this.isLive = false,
    this.completedSummaries = const [],
  });

  @override
  State<MinimalistManagerStateHeader> createState() => _MinimalistManagerStateHeaderState();
}

class _MinimalistManagerStateHeaderState extends State<MinimalistManagerStateHeader>
    with SingleTickerProviderStateMixin {
  late AnimationController _pulseController;
  late Animation<double> _glowAnimation;

  @override
  void initState() {
    super.initState();
    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    )..repeat(reverse: true);

    _glowAnimation = Tween<double>(begin: 0.4, end: 1.0).animate(
      CurvedAnimation(parent: _pulseController, curve: Curves.easeInOut),
    );
  }

  @override
  void dispose() {
    _pulseController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final reduceMotion = MediaQuery.of(context).disableAnimations;
    final isLive = widget.isLive;

    final primaryGray = isDark ? const Color(0xFF94A3B8) : const Color(0xFF64748B);
    final secondaryGray = isDark ? const Color(0xFF64748B) : const Color(0xFF94A3B8);
    final isPausedOrWaiting = widget.activeStateText.toLowerCase().contains('paused') ||
        widget.activeStateText.toLowerCase().contains('waiting') ||
        widget.activeStateText.toLowerCase().contains('inactive');

    final indicatorColor = isLive
        ? AppTokens.brandSecondary
        : (isPausedOrWaiting ? AppTokens.warning : AppTokens.brandPrimary);

    final rawText = widget.activeStateText.trim();
    final displayText = rawText.startsWith('Manager ·') || rawText.startsWith('Manager:')
        ? rawText
        : 'Manager · $rawText';

    return Container(
      margin: const EdgeInsets.only(bottom: AppTokens.space8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Live / Current State Line with subtle indicator
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (isLive && !reduceMotion)
                AnimatedBuilder(
                  animation: _glowAnimation,
                  builder: (context, child) {
                    return Container(
                      width: 6.5,
                      height: 6.5,
                      margin: const EdgeInsets.only(right: 8),
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: indicatorColor.withOpacity(_glowAnimation.value),
                        boxShadow: [
                          BoxShadow(
                            color: indicatorColor.withOpacity(0.4 * _glowAnimation.value),
                            blurRadius: 4,
                            spreadRadius: 1,
                          ),
                        ],
                      ),
                    );
                  },
                )
              else
                Container(
                  width: 6,
                  height: 6,
                  margin: const EdgeInsets.only(right: 8),
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: isLive ? indicatorColor : (isPausedOrWaiting ? AppTokens.warning : secondaryGray),
                  ),
                ),

              // Animated Transitioning State Text
              Flexible(
                child: AnimatedSwitcher(
                  duration: reduceMotion ? Duration.zero : const Duration(milliseconds: 280),
                  transitionBuilder: (child, animation) {
                    if (reduceMotion) return child;
                    return FadeTransition(
                      opacity: animation,
                      child: SlideTransition(
                        position: Tween<Offset>(
                          begin: const Offset(0.0, 0.2),
                          end: Offset.zero,
                        ).animate(CurvedAnimation(parent: animation, curve: Curves.easeOutCubic)),
                        child: child,
                      ),
                    );
                  },
                  child: Text(
                    displayText,
                    key: ValueKey<String>(displayText),
                    style: TextStyle(
                      fontSize: 12.5,
                      fontWeight: FontWeight.w500,
                      letterSpacing: 0.15,
                      color: isLive
                          ? (isDark ? const Color(0xFFCBD5E1) : const Color(0xFF334155))
                          : primaryGray,
                    ),
                  ),
                ),
              ),
            ],
          ),

          // Completed Milestones / Short Summaries (if any provided)
          if (widget.completedSummaries.isNotEmpty) ...[
            const SizedBox(height: 4),
            ...widget.completedSummaries.map((summary) {
              final isCheck = summary.startsWith('✓') || summary.startsWith('●');
              final isPause = summary.startsWith('⏸') || summary.contains('paused');
              final cleanText = summary.replaceFirst(RegExp(r'^[✓●⏸⚠️\s\-]+'), '').trim();

              return Padding(
                padding: const EdgeInsets.only(left: 14, top: 2, bottom: 2),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      isPause ? '⏸ ' : (isCheck ? '✓ ' : '• '),
                      style: TextStyle(
                        fontSize: 11,
                        color: isPause ? AppTokens.warning : (isCheck ? AppTokens.diffAdded : secondaryGray),
                      ),
                    ),
                    Expanded(
                      child: Text(
                        cleanText,
                        style: TextStyle(
                          fontSize: 12,
                          height: 1.35,
                          color: isDark ? const Color(0xFF64748B) : const Color(0xFF94A3B8),
                        ),
                      ),
                    ),
                  ],
                ),
              );
            }),
          ],
        ],
      ),
    );
  }
}
