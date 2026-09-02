import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';

enum ManagerCoreState {
  interpreting,
  planning,
  coordinating,
  executing,
  waiting,
  verifying,
  escalating,
  completed,
  failed,
}

/// Living Manager Core Visualizer.
/// The animated visual centerpiece of the Autonomous Engineering Session.
/// Renders state-driven orbital energy rings, pulsing core iris, and dynamic state badges.
class ManagerCoreVisualizer extends StatefulWidget {
  final ManagerCoreState state;
  final String? subtitle;
  final bool compact;

  const ManagerCoreVisualizer({
    super.key,
    required this.state,
    this.subtitle,
    this.compact = false,
  });

  @override
  State<ManagerCoreVisualizer> createState() => _ManagerCoreVisualizerState();
}

class _ManagerCoreVisualizerState extends State<ManagerCoreVisualizer>
    with SingleTickerProviderStateMixin {
  late AnimationController _animController;

  @override
  void initState() {
    super.initState();
    _animController = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 8),
    )..repeat();
  }

  @override
  void dispose() {
    _animController.dispose();
    super.dispose();
  }

  Color _getStateColor() {
    switch (widget.state) {
      case ManagerCoreState.interpreting:
        return AppTokens.brandSecondary;
      case ManagerCoreState.planning:
        return AppTokens.brandPrimary;
      case ManagerCoreState.coordinating:
        return AppTokens.purple;
      case ManagerCoreState.executing:
        return AppTokens.diffAdded;
      case ManagerCoreState.waiting:
        return AppTokens.brandPrimaryLight;
      case ManagerCoreState.verifying:
        return AppTokens.brandIndigo;
      case ManagerCoreState.escalating:
        return AppTokens.danger;
      case ManagerCoreState.completed:
        return AppTokens.success;
      case ManagerCoreState.failed:
        return AppTokens.danger;
    }
  }

  String _getStateLabel() {
    switch (widget.state) {
      case ManagerCoreState.interpreting:
        return 'INTERPRETING REQUEST';
      case ManagerCoreState.planning:
        return 'PLANNING WORK';
      case ManagerCoreState.coordinating:
        return 'COORDINATING WORKFORCE';
      case ManagerCoreState.executing:
        return 'EXECUTING';
      case ManagerCoreState.waiting:
        return 'WAITING FOR WORKERS';
      case ManagerCoreState.verifying:
        return 'VERIFYING RESULTS';
      case ManagerCoreState.escalating:
        return 'ESCALATED';
      case ManagerCoreState.completed:
        return 'COMPLETED';
      case ManagerCoreState.failed:
        return 'EXECUTION FAILED';
    }
  }

  IconData _getStateIcon() {
    switch (widget.state) {
      case ManagerCoreState.interpreting:
        return Icons.psychology_outlined;
      case ManagerCoreState.planning:
        return Icons.auto_awesome;
      case ManagerCoreState.coordinating:
        return Icons.account_tree_outlined;
      case ManagerCoreState.executing:
        return Icons.play_arrow_outlined;
      case ManagerCoreState.waiting:
        return Icons.pause_circle_outline;
      case ManagerCoreState.verifying:
        return Icons.radar_outlined;
      case ManagerCoreState.escalating:
        return Icons.warning_amber_rounded;
      case ManagerCoreState.completed:
        return Icons.check_circle_outline;
      case ManagerCoreState.failed:
        return Icons.error_outline;
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final stateColor = _getStateColor();
    final size = widget.compact ? 64.0 : 88.0;

    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // Animated Core Canvas
          SizedBox(
            width: size,
            height: size,
            child: AnimatedBuilder(
              animation: _animController,
              builder: (context, child) {
                return CustomPaint(
                  painter: _ManagerCorePainter(
                    animationValue: _animController.value,
                    color: stateColor,
                    isDark: isDark,
                    state: widget.state,
                  ),
                );
              },
            ),
          ),
          const SizedBox(height: AppTokens.space10),

          // Central Manager Identity
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                'MANAGER',
                style: TextStyle(
                  fontSize: widget.compact ? 12 : 13,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 2.0,
                  color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
                ),
              ),
            ],
          ),
          const SizedBox(height: 4),

          // Dynamic State Badge
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
            decoration: BoxDecoration(
              color: stateColor.withOpacity(isDark ? 0.15 : 0.1),
              borderRadius: AppTokens.borderRadiusFull,
              border: Border.all(
                color: stateColor.withOpacity(isDark ? 0.35 : 0.25),
                width: 1,
              ),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(
                  _getStateIcon(),
                  size: 11,
                  color: stateColor,
                ),
                const SizedBox(width: 5),
                Text(
                  _getStateLabel(),
                  style: TextStyle(
                    fontSize: 10,
                    fontWeight: FontWeight.w600,
                    letterSpacing: 0.8,
                    color: stateColor,
                  ),
                ),
              ],
            ),
          ),

          if (widget.subtitle != null && widget.subtitle!.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(
              widget.subtitle!,
              style: TextStyle(
                fontSize: 11.5,
                color: isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _ManagerCorePainter extends CustomPainter {
  final double animationValue;
  final Color color;
  final bool isDark;
  final ManagerCoreState state;

  _ManagerCorePainter({
    required this.animationValue,
    required this.color,
    required this.isDark,
    required this.state,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final maxRadius = size.width / 2;

    // 1. Ambient Glow Aura
    final glowPaint = Paint()
      ..shader = RadialGradient(
        colors: [
          color.withOpacity(isDark ? 0.3 : 0.2),
          color.withOpacity(0.0),
        ],
      ).createShader(Rect.fromCircle(center: center, radius: maxRadius));
    canvas.drawCircle(center, maxRadius, glowPaint);

    // 2. State-Driven Outer Orbit Ring
    final ringPaint = Paint()
      ..color = color.withOpacity(isDark ? 0.35 : 0.25)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.0;

    final outerRadius = maxRadius * 0.85;
    canvas.drawCircle(center, outerRadius, ringPaint);

    // 3. Counter-Rotating Dashed Ring
    final angle = animationValue * 2 * math.pi;
    final innerRadius = maxRadius * 0.65;

    final dashedPaint = Paint()
      ..color = color.withOpacity(isDark ? 0.6 : 0.4)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.2;

    const segmentCount = 6;
    for (int i = 0; i < segmentCount; i++) {
      final startAngle = angle + (i * 2 * math.pi / segmentCount);
      const sweepAngle = math.pi / (segmentCount * 1.5);
      canvas.drawArc(
        Rect.fromCircle(center: center, radius: innerRadius),
        startAngle,
        sweepAngle,
        false,
        dashedPaint,
      );
    }

    // 4. Orbiting Particles
    final particleAngle = -angle * 1.5;
    final particleRadius = 2.5;
    final particleOffset = Offset(
      center.dx + outerRadius * math.cos(particleAngle),
      center.dy + outerRadius * math.sin(particleAngle),
    );
    final particlePaint = Paint()
      ..color = color
      ..style = PaintingStyle.fill;
    canvas.drawCircle(particleOffset, particleRadius, particlePaint);

    final secondParticleOffset = Offset(
      center.dx + outerRadius * math.cos(particleAngle + math.pi),
      center.dy + outerRadius * math.sin(particleAngle + math.pi),
    );
    canvas.drawCircle(secondParticleOffset, particleRadius * 0.8, particlePaint);

    // 5. Radar Sweep if in Verifying State
    if (state == ManagerCoreState.verifying) {
      final sweepPaint = Paint()
        ..shader = SweepGradient(
          center: FractionalOffset.center,
          startAngle: 0.0,
          endAngle: math.pi / 2,
          colors: [
            color.withOpacity(0.0),
            color.withOpacity(0.4),
          ],
          transform: GradientRotation(angle * 2),
        ).createShader(Rect.fromCircle(center: center, radius: outerRadius));
      canvas.drawCircle(center, outerRadius, sweepPaint);
    }

    // 6. Central Core Iris
    final corePulse = 0.85 + 0.15 * math.sin(animationValue * 4 * math.pi);
    final coreRadius = maxRadius * 0.35 * corePulse;

    final corePaint = Paint()
      ..shader = RadialGradient(
        colors: [
          color,
          color.withOpacity(0.8),
        ],
      ).createShader(Rect.fromCircle(center: center, radius: coreRadius));
    canvas.drawCircle(center, coreRadius, corePaint);

    // 7. Center Specular Highlight
    final highlightPaint = Paint()
      ..color = Colors.white.withOpacity(0.8)
      ..style = PaintingStyle.fill;
    canvas.drawCircle(
      Offset(center.dx - coreRadius * 0.25, center.dy - coreRadius * 0.25),
      coreRadius * 0.3,
      highlightPaint,
    );
  }

  @override
  bool shouldRepaint(covariant _ManagerCorePainter oldDelegate) {
    return oldDelegate.animationValue != animationValue ||
        oldDelegate.color != color ||
        oldDelegate.state != state;
  }
}
