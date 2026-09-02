import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';

class ErrorBanner extends StatelessWidget {
  final String message;
  final VoidCallback? onDismiss;

  const ErrorBanner({
    super.key,
    required this.message,
    this.onDismiss,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: AppTokens.space16, vertical: AppTokens.space12),
      margin: const EdgeInsets.all(AppTokens.space12),
      decoration: BoxDecoration(
        color: AppTokens.danger.withOpacity(0.12),
        borderRadius: AppTokens.borderRadiusMd,
        border: Border.all(color: AppTokens.danger.withOpacity(0.3)),
      ),
      child: Row(
        children: [
          const Icon(Icons.error_outline, size: 20, color: AppTokens.danger),
          const SizedBox(width: AppTokens.space12),
          Expanded(
            child: Text(
              message,
              style: const TextStyle(color: AppTokens.danger, fontSize: 13, fontWeight: FontWeight.w500),
            ),
          ),
          if (onDismiss != null)
            IconButton(
              icon: const Icon(Icons.close, size: 16, color: AppTokens.danger),
              onPressed: onDismiss,
              splashRadius: 16,
              padding: EdgeInsets.zero,
              constraints: const BoxConstraints(),
            ),
        ],
      ),
    );
  }
}
