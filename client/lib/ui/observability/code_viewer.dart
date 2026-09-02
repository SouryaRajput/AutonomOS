import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../../core/tokens/tokens.dart';

/// Lightweight, readable code viewer with line numbers, search, copy, and syntax highlighting.
class CodeViewer extends StatefulWidget {
  final String code;
  final String language;
  final String? filename;

  const CodeViewer({
    super.key,
    required this.code,
    this.language = 'python',
    this.filename,
  });

  @override
  State<CodeViewer> createState() => _CodeViewerState();
}

class _CodeViewerState extends State<CodeViewer> {
  final TextEditingController _searchController = TextEditingController();
  String _searchQuery = '';
  bool _copied = false;

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  void _copyToClipboard() {
    Clipboard.setData(ClipboardData(text: widget.code));
    setState(() {
      _copied = true;
    });
    Future.delayed(const Duration(seconds: 2), () {
      if (mounted) {
        setState(() {
          _copied = false;
        });
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    final lines = widget.code.split('\n');

    return Container(
      decoration: BoxDecoration(
        color: isDark ? const Color(0xFF12151E) : const Color(0xFF1E2230),
        borderRadius: AppTokens.borderRadiusMd,
        border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
      ),
      child: Column(
        children: [
          // Toolbar
          Container(
            padding: const EdgeInsets.symmetric(horizontal: AppTokens.space12, vertical: AppTokens.space8),
            decoration: BoxDecoration(
              color: isDark ? AppTokens.darkSurface : const Color(0xFF161922),
              borderRadius: const BorderRadius.vertical(top: Radius.circular(AppTokens.radiusMd)),
              border: Border(bottom: BorderSide(color: isDark ? AppTokens.darkBorder : Colors.black26)),
            ),
            child: Row(
              children: [
                const Icon(Icons.code, size: 16, color: AppTokens.brandPrimaryLight),
                const SizedBox(width: AppTokens.space8),
                Text(
                  widget.filename ?? widget.language.toUpperCase(),
                  style: const TextStyle(color: AppTokens.darkTextPrimary, fontSize: 12, fontWeight: FontWeight.w600),
                ),
                const Spacer(),
                // Search Input
                SizedBox(
                  width: 140,
                  height: 28,
                  child: TextField(
                    controller: _searchController,
                    onChanged: (val) {
                      setState(() {
                        _searchQuery = val.trim().toLowerCase();
                      });
                    },
                    style: const TextStyle(color: Colors.white, fontSize: 11),
                    decoration: InputDecoration(
                      hintText: 'Search code...',
                      hintStyle: const TextStyle(color: AppTokens.darkTextMuted, fontSize: 11),
                      prefixIcon: const Icon(Icons.search, size: 14, color: AppTokens.darkTextMuted),
                      contentPadding: const EdgeInsets.symmetric(vertical: 0, horizontal: 8),
                      filled: true,
                      fillColor: isDark ? AppTokens.darkCard : const Color(0xFF242A3D),
                      border: OutlineInputBorder(borderRadius: BorderRadius.circular(4), borderSide: BorderSide.none),
                    ),
                  ),
                ),
                const SizedBox(width: AppTokens.space8),
                // Copy Button
                IconButton(
                  icon: Icon(_copied ? Icons.check : Icons.copy, size: 14, color: _copied ? AppTokens.success : AppTokens.darkTextSecondary),
                  onPressed: _copyToClipboard,
                  tooltip: 'Copy Code',
                  splashRadius: 14,
                  padding: EdgeInsets.zero,
                  constraints: const BoxConstraints(),
                ),
              ],
            ),
          ),

          // Code Content with Line Numbers
          Expanded(
            child: ListView.builder(
              padding: const EdgeInsets.symmetric(vertical: AppTokens.space8),
              itemCount: lines.length,
              itemBuilder: (context, index) {
                final line = lines[index];
                final lineNum = index + 1;
                final isMatch = _searchQuery.isNotEmpty && line.toLowerCase().contains(_searchQuery);

                return Container(
                  color: isMatch ? Colors.yellow.withOpacity(0.18) : Colors.transparent,
                  padding: const EdgeInsets.symmetric(horizontal: AppTokens.space12, vertical: 1),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      SizedBox(
                        width: 36,
                        child: Text(
                          '$lineNum',
                          style: const TextStyle(
                            fontFamily: 'monospace',
                            fontSize: 12,
                            color: AppTokens.darkTextMuted,
                          ),
                          textAlign: TextAlign.right,
                        ),
                      ),
                      const SizedBox(width: AppTokens.space16),
                      Expanded(
                        child: SelectableText(
                          line.isEmpty ? ' ' : line,
                          style: TextStyle(
                            fontFamily: 'monospace',
                            fontSize: 12,
                            height: 1.4,
                            color: _getLineColor(line),
                          ),
                        ),
                      ),
                    ],
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  Color _getLineColor(String line) {
    final trimmed = line.trim();
    if (trimmed.startsWith('#') || trimmed.startsWith('//')) {
      return const Color(0xFF6B7280); // Comments: grey
    }
    if (trimmed.startsWith('import ') || trimmed.startsWith('from ') || trimmed.startsWith('export ')) {
      return const Color(0xFFF472B6); // Imports: pink
    }
    if (trimmed.startsWith('class ') || trimmed.startsWith('def ') || trimmed.startsWith('function ')) {
      return const Color(0xFF60A5FA); // Definitions: blue
    }
    if (trimmed.startsWith('return ') || trimmed.startsWith('if ') || trimmed.startsWith('else:')) {
      return const Color(0xFFA78BFA); // Control flow: purple
    }
    return const Color(0xFFE2E8F0); // Default text
  }
}
