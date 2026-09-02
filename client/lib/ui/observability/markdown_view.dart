import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';

/// Pure Flutter, safe, virtualized Markdown renderer.
/// Safely renders headings, lists, tables, blockquotes, inline code, and code blocks
/// without executing any arbitrary scripts or HTML.
class SafeMarkdownView extends StatelessWidget {
  final String markdown;
  final EdgeInsetsGeometry padding;

  const SafeMarkdownView({
    super.key,
    required this.markdown,
    this.padding = const EdgeInsets.all(AppTokens.space16),
  });

  @override
  Widget build(BuildContext context) {
    final blocks = _parseMarkdownBlocks(markdown);

    return ListView.builder(
      shrinkWrap: true,
      physics: const ClampingScrollPhysics(),
      padding: padding,
      itemCount: blocks.length,
      itemBuilder: (context, index) {
        return blocks[index].build(context);
      },
    );
  }

  List<_MdBlock> _parseMarkdownBlocks(String text) {
    final lines = text.split('\n');
    final blocks = <_MdBlock>[];

    int i = 0;
    while (i < lines.length) {
      final line = lines[i];
      final trimmed = line.trim();

      if (trimmed.isEmpty) {
        i++;
        continue;
      }

      // Fenced Code Block
      if (trimmed.startsWith('```')) {
        final lang = trimmed.substring(3).trim();
        final codeLines = <String>[];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith('```')) {
          codeLines.add(lines[i]);
          i++;
        }
        if (i < lines.length) i++; // skip closing ```
        blocks.add(_MdCodeBlock(code: codeLines.join('\n'), language: lang));
        continue;
      }

      // Headings
      if (trimmed.startsWith('### ')) {
        blocks.add(_MdHeading(text: trimmed.substring(4), level: 3));
        i++;
        continue;
      }
      if (trimmed.startsWith('## ')) {
        blocks.add(_MdHeading(text: trimmed.substring(3), level: 2));
        i++;
        continue;
      }
      if (trimmed.startsWith('# ')) {
        blocks.add(_MdHeading(text: trimmed.substring(2), level: 1));
        i++;
        continue;
      }

      // Blockquote
      if (trimmed.startsWith('> ')) {
        final quoteLines = <String>[trimmed.substring(2)];
        i++;
        while (i < lines.length && lines[i].trim().startsWith('> ')) {
          quoteLines.add(lines[i].trim().substring(2));
          i++;
        }
        blocks.add(_MdBlockQuote(text: quoteLines.join(' ')));
        continue;
      }

      // Unordered List
      if (trimmed.startsWith('* ') || trimmed.startsWith('- ')) {
        final itemText = trimmed.substring(2);
        blocks.add(_MdListItem(text: itemText, isOrdered: false));
        i++;
        continue;
      }

      // Ordered List
      final orderedMatch = RegExp(r'^\d+\.\s+(.*)$').firstMatch(trimmed);
      if (orderedMatch != null) {
        blocks.add(_MdListItem(text: orderedMatch.group(1) ?? '', isOrdered: true));
        i++;
        continue;
      }

      // Table Row
      if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
        final tableLines = <String>[trimmed];
        i++;
        while (i < lines.length && lines[i].trim().startsWith('|') && lines[i].trim().endsWith('|')) {
          tableLines.add(lines[i].trim());
          i++;
        }
        blocks.add(_MdTable(rows: tableLines));
        continue;
      }

      // Paragraph
      blocks.add(_MdParagraph(text: line));
      i++;
    }

    return blocks;
  }
}

abstract class _MdBlock {
  Widget build(BuildContext context);
}

class _MdHeading extends _MdBlock {
  final String text;
  final int level;

  _MdHeading({required this.text, required this.level});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    TextStyle? style;
    switch (level) {
      case 1:
        style = theme.textTheme.headlineMedium;
        break;
      case 2:
        style = theme.textTheme.titleLarge;
        break;
      case 3:
      default:
        style = theme.textTheme.titleMedium;
        break;
    }

    return Padding(
      padding: const EdgeInsets.only(top: AppTokens.space16, bottom: AppTokens.space8),
      child: Text(text, style: style),
    );
  }
}

class _MdParagraph extends _MdBlock {
  final String text;

  _MdParagraph({required this.text});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppTokens.space4),
      child: _buildRichText(context, text),
    );
  }
}

class _MdBlockQuote extends _MdBlock {
  final String text;

  _MdBlockQuote({required this.text});

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return Container(
      margin: const EdgeInsets.symmetric(vertical: AppTokens.space8),
      padding: const EdgeInsets.symmetric(horizontal: AppTokens.space16, vertical: AppTokens.space8),
      decoration: BoxDecoration(
        color: isDark ? AppTokens.darkSurface : AppTokens.lightBorder.withOpacity(0.3),
        border: const Border(left: BorderSide(color: AppTokens.brandPrimaryLight, width: 3)),
        borderRadius: const BorderRadius.only(
          topRight: Radius.circular(AppTokens.radiusSm),
          bottomRight: Radius.circular(AppTokens.radiusSm),
        ),
      ),
      child: Text(
        text,
        style: TextStyle(fontStyle: FontStyle.italic, color: isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary),
      ),
    );
  }
}

class _MdListItem extends _MdBlock {
  final String text;
  final bool isOrdered;

  _MdListItem({required this.text, required this.isOrdered});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(isOrdered ? '● ' : '• ', style: const TextStyle(fontWeight: FontWeight.bold, color: AppTokens.brandPrimaryLight)),
          Expanded(child: _buildRichText(context, text)),
        ],
      ),
    );
  }
}

class _MdCodeBlock extends _MdBlock {
  final String code;
  final String language;

  _MdCodeBlock({required this.code, required this.language});

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return Container(
      margin: const EdgeInsets.symmetric(vertical: AppTokens.space8),
      padding: const EdgeInsets.all(AppTokens.space12),
      decoration: BoxDecoration(
        color: isDark ? AppTokens.darkSurface : const Color(0xFF1E1E2E),
        borderRadius: AppTokens.borderRadiusSm,
        border: Border.all(color: isDark ? AppTokens.darkBorder : Colors.transparent),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (language.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: 6.0),
              child: Text(
                language.toUpperCase(),
                style: const TextStyle(color: AppTokens.darkTextMuted, fontSize: 10, fontWeight: FontWeight.bold),
              ),
            ),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: SelectableText(
              code,
              style: const TextStyle(
                fontFamily: 'monospace',
                fontSize: 12,
                color: Color(0xFFE0E6F0),
                height: 1.4,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _MdTable extends _MdBlock {
  final List<String> rows;

  _MdTable({required this.rows});

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final parsedRows = rows
        .where((r) => !r.contains('---'))
        .map((r) => r.split('|').where((c) => c.isNotEmpty).map((c) => c.trim()).toList())
        .toList();

    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: AppTokens.space8),
        decoration: BoxDecoration(
          border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
          borderRadius: AppTokens.borderRadiusSm,
        ),
        child: Table(
          defaultColumnWidth: const IntrinsicColumnWidth(),
          border: TableBorder.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
          children: parsedRows.map((row) {
            return TableRow(
              children: row.map((cell) {
                return Padding(
                  padding: const EdgeInsets.all(AppTokens.space8),
                  child: Text(cell, style: const TextStyle(fontSize: 12)),
                );
              }).toList(),
            );
          }).toList(),
        ),
      ),
    );
  }
}

Widget _buildRichText(BuildContext context, String raw) {
  final isDark = Theme.of(context).brightness == Brightness.dark;
  final spans = <InlineSpan>[];

  final tokens = raw.split('`');
  for (int i = 0; i < tokens.length; i++) {
    if (i % 2 == 1) {
      // Inline Code
      spans.add(WidgetSpan(
        alignment: PlaceholderAlignment.middle,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
          decoration: BoxDecoration(
            color: isDark ? AppTokens.darkSurface : AppTokens.lightBorder.withOpacity(0.5),
            borderRadius: BorderRadius.circular(3),
          ),
          child: Text(
            tokens[i],
            style: const TextStyle(fontFamily: 'monospace', fontSize: 12, color: AppTokens.brandPrimaryLight),
          ),
        ),
      ));
    } else {
      // Normal Text
      spans.add(TextSpan(text: tokens[i]));
    }
  }

  return SelectableText.rich(
    TextSpan(children: spans, style: Theme.of(context).textTheme.bodyLarge),
  );
}
