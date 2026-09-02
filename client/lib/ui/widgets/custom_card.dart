import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';

class CustomCard extends StatelessWidget {
  final Widget child;
  final EdgeInsetsGeometry? padding;
  final VoidCallback? onTap;
  final Color? borderColor;
  final Color? backgroundColor;

  const CustomCard({
    super.key,
    required this.child,
    this.padding,
    this.onTap,
    this.borderColor,
    this.backgroundColor,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    final defaultBorder = isDark ? AppTokens.darkBorder : AppTokens.lightBorder;
    final defaultBg = isDark ? AppTokens.darkCard : AppTokens.lightCard;

    Widget content = Container(
      padding: padding ?? const EdgeInsets.all(AppTokens.space16),
      decoration: BoxDecoration(
        color: backgroundColor ?? defaultBg,
        borderRadius: AppTokens.borderRadiusMd,
        border: Border.all(color: borderColor ?? defaultBorder, width: 1),
      ),
      child: child,
    );

    if (onTap != null) {
      content = InkWell(
        onTap: onTap,
        borderRadius: AppTokens.borderRadiusMd,
        child: content,
      );
    }

    return content;
  }
}
