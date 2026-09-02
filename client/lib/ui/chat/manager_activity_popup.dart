import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';

/// Temporary compact status popup for the Manager's current live activity.
/// Positioned directly above the chat composer.
/// Respects accessibility and reduced motion preferences.
class ManagerActivityPopup extends StatefulWidget {
  final String title;
  final String? subtitle;
  final bool isVisible;

  const ManagerActivityPopup({
    super.key,
    required this.title,
    this.subtitle,
    this.isVisible = true,
  });

  @override
  State<ManagerActivityPopup> createState() => _ManagerActivityPopupState();
}

class _ManagerActivityPopupState extends State<ManagerActivityPopup>
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

    _glowAnimation = Tween<double>(begin: 0.45, end: 1.0).animate(
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
    if (!widget.isVisible || widget.title.isEmpty) {
      return const SizedBox.shrink();
    }

    final isDark = Theme.of(context).brightness == Brightness.dark;
    final reduceMotion = MediaQuery.of(context).disableAnimations;

    final cardBg = isDark ? const Color(0xFF141824) : const Color(0xFFFFFFFF);
    final borderColor = isDark ? const Color(0xFF334155).withOpacity(0.6) : const Color(0xFFE2E8F0);
    final titleColor = isDark ? const Color(0xFFF1F5F9) : const Color(0xFF0F172A);
    final subtitleColor = isDark ? const Color(0xFF94A3B8) : const Color(0xFF64748B);

    return AnimatedSwitcher(
      duration: reduceMotion ? Duration.zero : const Duration(milliseconds: 240),
      transitionBuilder: (child, animation) {
        if (reduceMotion) return child;
        return FadeTransition(
          opacity: animation,
          child: SlideTransition(
            position: Tween<Offset>(
              begin: const Offset(0.0, 0.15),
              end: Offset.zero,
            ).animate(CurvedAnimation(parent: animation, curve: Curves.easeOutCubic)),
            child: child,
          ),
        );
      },
      child: Container(
        key: ValueKey<String>('${widget.title}_${widget.subtitle}'),
        margin: const EdgeInsets.only(bottom: AppTokens.space8),
        padding: const EdgeInsets.symmetric(horizontal: AppTokens.space14, vertical: AppTokens.space10),
        decoration: BoxDecoration(
          color: cardBg,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: borderColor, width: 1),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withOpacity(isDark ? 0.25 : 0.06),
              blurRadius: 8,
              offset: const Offset(0, 2),
            ),
          ],
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            // Micro pulsing indicator
            if (reduceMotion)
              Container(
                width: 7,
                height: 7,
                margin: const EdgeInsets.only(right: 10),
                decoration: const BoxDecoration(
                  shape: BoxShape.circle,
                  color: AppTokens.brandSecondary,
                ),
              )
            else
              AnimatedBuilder(
                animation: _glowAnimation,
                builder: (context, child) {
                  return Container(
                    width: 7,
                    height: 7,
                    margin: const EdgeInsets.only(right: 10),
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: AppTokens.brandSecondary.withOpacity(_glowAnimation.value),
                      boxShadow: [
                        BoxShadow(
                          color: AppTokens.brandSecondary.withOpacity(0.5 * _glowAnimation.value),
                          blurRadius: 5,
                          spreadRadius: 1,
                        ),
                      ],
                    ),
                  );
                },
              ),

            // Activity Text (Title & Subtitle)
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Row(
                    children: [
                      Text(
                        'Manager · ',
                        style: TextStyle(
                          fontSize: 12.5,
                          fontWeight: FontWeight.w600,
                          color: titleColor,
                          letterSpacing: 0.1,
                        ),
                      ),
                      Flexible(
                        child: Text(
                          widget.title,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            fontSize: 12.5,
                            fontWeight: FontWeight.w500,
                            color: titleColor,
                          ),
                        ),
                      ),
                    ],
                  ),
                  if (widget.subtitle != null && widget.subtitle!.isNotEmpty) ...[
                    const SizedBox(height: 2),
                    Text(
                      widget.subtitle!,
                      style: TextStyle(
                        fontSize: 11.5,
                        color: subtitleColor,
                        height: 1.3,
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
