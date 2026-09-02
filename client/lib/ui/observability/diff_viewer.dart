import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';

class DiffViewer extends StatelessWidget {
  final String diffText;
  final String? filename;

  const DiffViewer({
    super.key,
    required this.diffText,
    this.filename,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    final lines = diffText.split('\n');

    int additions = 0;
    int deletions = 0;
    for (final l in lines) {
      if (l.startsWith('+') && !l.startsWith('+++')) additions++;
      if (l.startsWith('-') && !l.startsWith('---')) deletions++;
    }

    return Container(
      decoration: BoxDecoration(
        color: isDark ? const Color(0xFF11141E) : const Color(0xFF1E2230),
        borderRadius: AppTokens.borderRadiusMd,
        border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // Header Bar
          Container(
            padding: const EdgeInsets.symmetric(horizontal: AppTokens.space16, vertical: 10.0),
            decoration: BoxDecoration(
              color: isDark ? AppTokens.darkSurface : const Color(0xFF161922),
              borderRadius: const BorderRadius.vertical(top: Radius.circular(AppTokens.radiusMd)),
              border: Border(bottom: BorderSide(color: isDark ? AppTokens.darkBorder : Colors.black26)),
            ),
            child: Row(
              children: [
                const Icon(Icons.difference_outlined, size: 16, color: AppTokens.info),
                const SizedBox(width: AppTokens.space8),
                Text(
                  filename ?? 'Unified Diff',
                  style: const TextStyle(color: AppTokens.darkTextPrimary, fontSize: 13, fontWeight: FontWeight.w600),
                ),
                const Spacer(),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                  decoration: BoxDecoration(
                    color: AppTokens.success.withOpacity(0.2),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Text(
                    '+$additions',
                    style: const TextStyle(color: AppTokens.success, fontSize: 11, fontWeight: FontWeight.bold),
                  ),
                ),
                const SizedBox(width: 6.0),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                  decoration: BoxDecoration(
                    color: AppTokens.danger.withOpacity(0.2),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Text(
                    '-$deletions',
                    style: const TextStyle(color: AppTokens.danger, fontSize: 11, fontWeight: FontWeight.bold),
                  ),
                ),
              ],
            ),
          ),

          // Diff Lines
          Expanded(
            child: ListView.builder(
              padding: const EdgeInsets.symmetric(vertical: AppTokens.space4),
              itemCount: lines.length,
              itemBuilder: (context, index) {
                final line = lines[index];
                return _buildDiffLine(line);
              },
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildDiffLine(String line) {
    Color? bg;
    Color fg = const Color(0xFFE2E8F0);
    IconData? icon;

    if (line.startsWith('+') && !line.startsWith('+++')) {
      bg = AppTokens.success.withOpacity(0.18);
      fg = const Color(0xFF34D399); // Light emerald
      icon = Icons.add;
    } else if (line.startsWith('-') && !line.startsWith('---')) {
      bg = AppTokens.danger.withOpacity(0.18);
      fg = const Color(0xFFF87171); // Light red
      icon = Icons.remove;
    } else if (line.startsWith('@@')) {
      bg = AppTokens.brandPrimary.withOpacity(0.15);
      fg = AppTokens.brandPrimaryLight;
    }

    return Container(
      color: bg ?? Colors.transparent,
      padding: const EdgeInsets.symmetric(horizontal: AppTokens.space12, vertical: 1.5),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 16,
            child: icon != null
                ? Icon(icon, size: 12, color: fg)
                : const SizedBox.shrink(),
          ),
          const SizedBox(width: AppTokens.space8),
          Expanded(
            child: SelectableText(
              line.isEmpty ? ' ' : line,
              style: TextStyle(
                fontFamily: 'monospace',
                fontSize: 12,
                height: 1.35,
                color: fg,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
