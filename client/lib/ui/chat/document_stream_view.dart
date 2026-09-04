import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../../core/tokens/tokens.dart';
import '../../core/utils/message_sanitizer.dart';
import '../../models/conversation.dart';
import '../../models/execution_activity.dart';
import '../../models/manager_activity.dart';
import 'execution_activity_card.dart';
import 'minimalist_manager_header.dart';

/// Continuous Agent Document Stream canvas inspired by Cursor & Claude Code.
/// Renders crisp agent prose, compact tool summaries, grouped action lists, and execution activity cards.
class DocumentStreamView extends StatelessWidget {
  final List<ChatMessage> messages;
  final bool isSending;
  final String activeStage;
  final ExecutionActivity? currentActivity;
  final ScrollController scrollController;

  const DocumentStreamView({
    super.key,
    required this.messages,
    required this.isSending,
    this.activeStage = 'Understanding intent and checking workspace...',
    this.currentActivity,
    required this.scrollController,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    if (messages.isEmpty && !isSending) {
      return _buildEmptyCanvas(context, isDark);
    }

    return ListView.builder(
      controller: scrollController,
      padding: const EdgeInsets.symmetric(horizontal: AppTokens.space32, vertical: AppTokens.space24),
      itemCount: messages.length + (isSending ? 1 : 0),
      itemBuilder: (context, index) {
        if (index < messages.length) {
          final msg = messages[index];
          if (msg.messageType == MessageType.userMessage) {
            return _buildUserTurn(context, msg, isDark);
          } else {
            return _buildAssistantDocumentTurn(context, msg, isDark);
          }
        } else {
          return _buildThinkingRow(context, isDark);
        }
      },
    );
  }

  // --- User Turn (Compact Premium Bubble — Only User Turn is in a Bubble) ---
  Widget _buildUserTurn(BuildContext context, ChatMessage msg, bool isDark) {
    return Container(
      margin: const EdgeInsets.only(top: AppTokens.space16, bottom: AppTokens.space16),
      alignment: Alignment.centerRight,
      child: Container(
        constraints: const BoxConstraints(maxWidth: 580),
        padding: const EdgeInsets.symmetric(horizontal: AppTokens.space14, vertical: AppTokens.space10),
        decoration: BoxDecoration(
          color: isDark ? const Color(0xFF1E2433) : const Color(0xFFF1F5F9),
          borderRadius: const BorderRadius.only(
            topLeft: Radius.circular(14),
            topRight: Radius.circular(14),
            bottomLeft: Radius.circular(14),
            bottomRight: Radius.circular(4),
          ),
          border: Border.all(
            color: isDark ? const Color(0xFF334155).withOpacity(0.6) : const Color(0xFFE2E8F0),
            width: 1,
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            SelectableText(
              msg.content,
              style: TextStyle(
                fontSize: 13.5,
                fontWeight: FontWeight.w400,
                color: isDark ? const Color(0xFFF8FAFC) : const Color(0xFF0F172A),
                height: 1.4,
              ),
            ),
            if (msg.timestamp.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  _formatTime(msg.timestamp),
                  style: TextStyle(
                    fontSize: 10,
                    fontFamily: 'monospace',
                    color: isDark ? const Color(0xFF64748B) : const Color(0xFF94A3B8),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  // --- Assistant / Manager Turn (Continuous Minimalist Conversational Prose + Attached Activity Card) ---
  Widget _buildAssistantDocumentTurn(BuildContext context, ChatMessage msg, bool isDark) {
    final extracted = MessageSanitizer.extractUserFacingNarrative(msg.content);
    final rawContent = extracted.userFacingNarrative;
    final blocks = _parseParagraphsAndTools(rawContent);

    ExecutionActivity? activity;
    if (msg.metadata.containsKey('activity') && msg.metadata['activity'] is Map<String, dynamic>) {
      try {
        activity = ExecutionActivity.fromJson(msg.metadata['activity'] as Map<String, dynamic>);
      } catch (_) {}
    }

    return Container(
      margin: const EdgeInsets.only(bottom: AppTokens.space20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (activity != null)
            Padding(
              padding: const EdgeInsets.only(bottom: AppTokens.space12),
              child: ExecutionActivityCard(
                activity: activity,
                initialExpanded: false,
              ),
            ),
          ...blocks.map((block) {
            switch (block.type) {
              case _BlockType.heading1:
                return _Heading1View(text: block.content, isDark: isDark);
              case _BlockType.heading2:
                return _Heading2View(text: block.content, isDark: isDark);
              case _BlockType.heading3:
                return _Heading3View(text: block.content, isDark: isDark);
              case _BlockType.divider:
                return _DividerView(isDark: isDark);
              case _BlockType.table:
                return _MarkdownTableView(
                  headers: block.tableHeaders,
                  rows: block.tableRows,
                  isDark: isDark,
                );
              case _BlockType.bulletList:
                return _BulletListView(items: block.items, isDark: isDark);
              case _BlockType.orderedList:
                return _OrderedListView(items: block.items, isDark: isDark);
              case _BlockType.callout:
                return _CalloutBoxView(
                  text: block.content,
                  isDark: isDark,
                  title: block.title.isNotEmpty ? block.title : null,
                );
              case _BlockType.managerActivity:
                return _MarkdownParagraph(text: block.content, isDark: isDark);
              case _BlockType.taskContract:
                return _TaskContractCard(rawContent: block.content, isDark: isDark);
              case _BlockType.toolRun:
                return _CollapsibleToolRow(
                  title: block.title,
                  details: block.content,
                  addedLines: block.addedLines,
                  removedLines: block.removedLines,
                  isDark: isDark,
                );
              case _BlockType.actionGroup:
                return _ActionGroupCard(items: block.items, isDark: isDark);
              case _BlockType.workingTreeDiff:
                return _WorkingTreeDiffView(
                  branch: block.title.isNotEmpty ? block.title : 'main',
                  diffFiles: block.diffFiles,
                  isDark: isDark,
                );
              case _BlockType.codeBlock:
                return _CodeFenceView(code: block.content, language: block.title, isDark: isDark);
              case _BlockType.statusPill:
                return _StatusAnnotation(text: block.content, isDark: isDark);
              case _BlockType.paragraph:
                return _MarkdownParagraph(text: block.content, isDark: isDark);
            }
          }).toList(),
        ],
      ),
    );
  }

  // --- Real-Time In-Flight State (Clean Activity Card or Animated Header) ---
  Widget _buildThinkingRow(BuildContext context, bool isDark) {
    if (currentActivity != null) {
      return Container(
        margin: const EdgeInsets.only(top: AppTokens.space6, bottom: AppTokens.space16),
        child: ExecutionActivityCard(
          activity: currentActivity!,
          initialExpanded: false,
        ),
      );
    }

    return Container(
      margin: const EdgeInsets.only(top: AppTokens.space10, bottom: AppTokens.space20),
      child: MinimalistManagerStateHeader(
        activeStateText: activeStage.isNotEmpty ? activeStage : 'Inspecting workspace…',
        isLive: true,
      ),
    );
  }

  Widget _buildEmptyCanvas(BuildContext context, bool isDark) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            padding: const EdgeInsets.all(AppTokens.space16),
            decoration: BoxDecoration(
              color: isDark ? AppTokens.darkElevated : AppTokens.lightSurface,
              shape: BoxShape.circle,
              border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
            ),
            child: const Icon(Icons.auto_awesome, size: 28, color: AppTokens.brandPrimary),
          ),
          const SizedBox(height: AppTokens.space16),
          const Text(
            'AutonomOS Workforce Manager',
            style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: AppTokens.space6),
          const Text(
            'Describe your engineering goal to observe deterministic auditing, task decomposition, and worker contracts.',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 13, color: AppTokens.darkTextMuted),
          ),
        ],
      ),
    );
  }

  String _formatTime(String iso) {
    try {
      final dt = DateTime.parse(iso).toLocal();
      return '${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
    } catch (_) {
      return '';
    }
  }

  List<_ParsedBlock> _parseParagraphsAndTools(String text) {
    final blocks = <_ParsedBlock>[];
    final lines = text.split('\n');

    int i = 0;
    while (i < lines.length) {
      final line = lines[i];
      final trimmed = line.trim();

      // 1. Skip blank lines
      if (trimmed.isEmpty) {
        i++;
        continue;
      }

      // 2. Fenced code block: ```lang
      if (trimmed.startsWith('```')) {
        final codeLang = trimmed.replaceFirst('```', '').trim();
        final codeBuffer = StringBuffer();
        i++;
        while (i < lines.length && !lines[i].trim().startsWith('```')) {
          codeBuffer.writeln(lines[i]);
          i++;
        }
        if (i < lines.length && lines[i].trim().startsWith('```')) {
          i++;
        }
        final c = codeBuffer.toString().trim();
        if (MessageSanitizer.isInternalToolJson(c)) {
          // Internal tool call in code block: hide from normal chat transcript
        } else if (c.contains('TASK CONTRACT:')) {
          blocks.add(_ParsedBlock(type: _BlockType.taskContract, title: codeLang, content: c));
        } else if (codeLang == 'diff' || c.startsWith('diff --git') || (c.contains('@@') && (c.contains('\n-') || c.contains('\n+')))) {
          blocks.add(_parseWorkingTreeDiff(c));
        } else {
          blocks.add(_ParsedBlock(type: _BlockType.codeBlock, title: codeLang, content: c));
        }
        continue;
      }

      // 3. Task contract
      if (trimmed.startsWith('--- TASK CONTRACT:')) {
        blocks.add(_ParsedBlock(type: _BlockType.taskContract, title: '', content: trimmed));
        i++;
        continue;
      }

      // 4. Working tree diff
      if (trimmed.contains('📁 main → working tree') || trimmed.contains('working tree')) {
        blocks.add(_parseWorkingTreeDiff(trimmed));
        i++;
        continue;
      }

      // 5. Divider (---, ***, ___)
      if (RegExp(r'^(\-{3,}|\*{3,}|_{3,})$').hasMatch(trimmed)) {
        blocks.add(_ParsedBlock(type: _BlockType.divider, title: '', content: ''));
        i++;
        continue;
      }

      // 6. Headings (#, ##, ###)
      if (trimmed.startsWith('# ')) {
        blocks.add(_ParsedBlock(type: _BlockType.heading1, title: '', content: trimmed.substring(2).trim()));
        i++;
        continue;
      }
      if (trimmed.startsWith('## ')) {
        blocks.add(_ParsedBlock(type: _BlockType.heading2, title: '', content: trimmed.substring(3).trim()));
        i++;
        continue;
      }
      if (trimmed.startsWith('### ')) {
        blocks.add(_ParsedBlock(type: _BlockType.heading3, title: '', content: trimmed.substring(4).trim()));
        i++;
        continue;
      }

      // 7. Markdown Tables
      if (trimmed.startsWith('|') && trimmed.contains('|') && trimmed.length > 2) {
        final isDelimiter = RegExp(r'^\s*\|?\s*[-:]+[-| :]*\|?\s*$').hasMatch(trimmed);
        final hasNextDelimiter = (i + 1 < lines.length) &&
            RegExp(r'^\s*\|?\s*[-:]+[-| :]*\|?\s*$').hasMatch(lines[i + 1].trim());

        if (hasNextDelimiter || isDelimiter) {
          final tableLines = <String>[];
          while (i < lines.length && lines[i].trim().startsWith('|')) {
            tableLines.add(lines[i].trim());
            i++;
          }

          if (tableLines.isNotEmpty) {
            List<String> headers = [];
            int startRow = 0;
            if (isDelimiter) {
              startRow = 1;
            } else if (hasNextDelimiter) {
              headers = _parseTableRow(tableLines[0]);
              startRow = 2;
            }

            final rows = <List<String>>[];
            for (var r = startRow; r < tableLines.length; r++) {
              if (RegExp(r'^\s*\|?\s*[-:]+[-| :]*\|?\s*$').hasMatch(tableLines[r])) continue;
              final rowCells = _parseTableRow(tableLines[r]);
              if (rowCells.isNotEmpty) {
                rows.add(rowCells);
              }
            }

            blocks.add(_ParsedBlock(
              type: _BlockType.table,
              title: '',
              content: '',
              tableHeaders: headers,
              tableRows: rows,
            ));
            continue;
          }
        }
      }

      // 8. Call to Action / Callout Box
      if (trimmed.startsWith('**Would you like me to proceed') ||
          trimmed.startsWith('**Would you like') ||
          trimmed.startsWith('**Call to Action**')) {
        final calloutBuffer = StringBuffer(trimmed);
        i++;
        while (i < lines.length &&
               lines[i].trim().isNotEmpty &&
               !lines[i].trim().startsWith('#') &&
               !lines[i].trim().startsWith('```') &&
               !RegExp(r'^(\-{3,}|\*{3,}|_{3,})$').hasMatch(lines[i].trim())) {
          calloutBuffer.writeln(lines[i]);
          i++;
        }
        blocks.add(_ParsedBlock(
          type: _BlockType.callout,
          title: 'Action Proposed',
          content: calloutBuffer.toString().trim(),
        ));
        continue;
      }

      // 9. Blockquote / Quote
      if (trimmed.startsWith('>')) {
        final quoteBuffer = StringBuffer();
        while (i < lines.length && lines[i].trim().startsWith('>')) {
          quoteBuffer.writeln(lines[i].trim().replaceFirst(RegExp(r'^>\s*'), ''));
          i++;
        }
        blocks.add(_ParsedBlock(
          type: _BlockType.callout,
          title: 'Note',
          content: quoteBuffer.toString().trim(),
        ));
        continue;
      }

      // 10. Bullet List (- item, * item, + item)
      if (RegExp(r'^\s*[-*+]\s+').hasMatch(line)) {
        final items = <String>[];
        while (i < lines.length && RegExp(r'^\s*[-*+]\s+').hasMatch(lines[i])) {
          items.add(lines[i].trim().replaceFirst(RegExp(r'^[-*+]\s+'), ''));
          i++;
        }
        blocks.add(_ParsedBlock(type: _BlockType.bulletList, title: '', content: '', items: items));
        continue;
      }

      // 11. Numbered List (1. item, 2. item)
      if (RegExp(r'^\s*\d+\.\s+').hasMatch(line)) {
        final items = <String>[];
        while (i < lines.length && RegExp(r'^\s*\d+\.\s+').hasMatch(lines[i])) {
          items.add(lines[i].trim().replaceFirst(RegExp(r'^\d+\.\s+'), ''));
          i++;
        }
        blocks.add(_ParsedBlock(type: _BlockType.orderedList, title: '', content: '', items: items));
        continue;
      }

      // 12. Action lines: Read ..., Ran ..., Checked ...
      if (trimmed.startsWith('Read ') || trimmed.startsWith('Ran ') || trimmed.startsWith('Checked ') || trimmed.startsWith('Updated ') || trimmed.startsWith('Typechecked ') || trimmed.startsWith('Linted ') || trimmed.startsWith('Found ')) {
        final actionLines = <String>[trimmed];
        i++;
        while (i < lines.length && (lines[i].trim().startsWith('Typechecked ') || lines[i].trim().startsWith('Linted ') || lines[i].trim().startsWith('Found ') || lines[i].trim().startsWith('Read ') || lines[i].trim().startsWith('Ran '))) {
          actionLines.add(lines[i].trim());
          i++;
        }
        if (actionLines.length > 1) {
          blocks.add(_ParsedBlock(type: _BlockType.actionGroup, title: '', content: '', items: actionLines));
        } else {
          blocks.add(_ParsedBlock(type: _BlockType.toolRun, title: trimmed, content: ''));
        }
        continue;
      }

      // 13. Status Pills
      if (trimmed.startsWith('Compacted conversation') || trimmed.startsWith('Diagnosis is clear') || trimmed.startsWith('Workers DISABLED') || trimmed.startsWith('Workers disabled')) {
        blocks.add(_ParsedBlock(type: _BlockType.statusPill, title: '', content: trimmed));
        i++;
        continue;
      }

      // 14. Regular paragraph
      final paraBuffer = StringBuffer(line);
      i++;
      while (i < lines.length) {
        final nextTrimmed = lines[i].trim();
        if (nextTrimmed.isEmpty ||
            nextTrimmed.startsWith('```') ||
            nextTrimmed.startsWith('#') ||
            (nextTrimmed.startsWith('|') && nextTrimmed.contains('|')) ||
            RegExp(r'^(\-{3,}|\*{3,}|_{3,})$').hasMatch(nextTrimmed) ||
            RegExp(r'^\s*[-*+]\s+').hasMatch(lines[i]) ||
            RegExp(r'^\s*\d+\.\s+').hasMatch(lines[i]) ||
            nextTrimmed.startsWith('>')) {
          break;
        }
        paraBuffer.writeln(lines[i]);
        i++;
      }

      final p = paraBuffer.toString().trim();
      if (p.isNotEmpty) {
        blocks.add(_ParsedBlock(type: _BlockType.paragraph, title: '', content: p));
      }
    }

    return blocks.isNotEmpty ? blocks : [_ParsedBlock(type: _BlockType.paragraph, title: '', content: text)];
  }

  static List<String> _parseTableRow(String line) {
    var trimmed = line.trim();
    if (trimmed.startsWith('|')) trimmed = trimmed.substring(1);
    if (trimmed.endsWith('|')) trimmed = trimmed.substring(0, trimmed.length - 1);
    return trimmed.split('|').map((c) => c.trim()).toList();
  }

  static _ParsedBlock _parseWorkingTreeDiff(String raw) {
    final diffFiles = <_DiffFileModel>[];
    final lines = raw.split('\n');

    String currentFile = 'modified_file.py';
    int additions = 0;
    int deletions = 0;
    final diffLines = <String>[];

    for (final line in lines) {
      if (line.contains('test_') || line.contains('.py') || line.contains('.dart') || line.contains('.ts') || line.contains('.tsx')) {
        final match = RegExp(r'([\w./\-]+)\s+\+(\d+)\s+-(\d+)').firstMatch(line);
        if (match != null) {
          if (diffLines.isNotEmpty) {
            diffFiles.add(_DiffFileModel(
              filename: currentFile,
              additions: additions,
              deletions: deletions,
              diffLines: List.from(diffLines),
            ));
            diffLines.clear();
          }
          currentFile = match.group(1) ?? 'file';
          additions = int.tryParse(match.group(2) ?? '0') ?? 0;
          deletions = int.tryParse(match.group(3) ?? '0') ?? 0;
          continue;
        }
      }

      if (line.startsWith('+') && !line.startsWith('+++')) additions++;
      if (line.startsWith('-') && !line.startsWith('---')) deletions++;
      diffLines.add(line);
    }

    if (diffLines.isNotEmpty || diffFiles.isEmpty) {
      diffFiles.add(_DiffFileModel(
        filename: currentFile,
        additions: additions,
        deletions: deletions,
        diffLines: diffLines,
        isExpanded: true,
      ));
    }

    return _ParsedBlock(
      type: _BlockType.workingTreeDiff,
      title: 'main → working tree',
      content: raw,
      diffFiles: diffFiles,
    );
  }

  static List<ManagerActivityStep> _parseManagerActivitySteps(String text) {
    final steps = <ManagerActivityStep>[];
    final lines = text.split('\n');

    ManagerActivityStep? currentStep;
    String? currentDecision;
    String? currentReason;
    final currentContext = <String>[];
    final currentTasks = <Map<String, dynamic>>[];
    String? currentPrompt;

    void flushStep() {
      if (currentStep != null) {
        steps.add(currentStep!.copyWith(
          decision: currentDecision,
          reason: currentReason,
          selectedContext: List.from(currentContext),
          tasks: List.from(currentTasks),
          workerPrompt: currentPrompt,
        ));
        currentDecision = null;
        currentReason = null;
        currentContext.clear();
        currentTasks.clear();
        currentPrompt = null;
      }
    }

    for (final line in lines) {
      final trimmed = line.trim();
      if (trimmed.startsWith('● ') || trimmed.startsWith('✓ ') || trimmed.startsWith('⏸ ') || trimmed.startsWith('⚠️ ')) {
        flushStep();
        ActivityEventStatus status = ActivityEventStatus.completed;
        if (trimmed.startsWith('● ')) status = ActivityEventStatus.inProgress;
        if (trimmed.startsWith('✓ ')) status = ActivityEventStatus.completed;
        if (trimmed.startsWith('⏸ ')) status = ActivityEventStatus.disabled;
        if (trimmed.startsWith('⚠️ ')) status = ActivityEventStatus.warning;

        final title = trimmed.substring(2).trim();
        currentStep = ManagerActivityStep(
          id: 'step-${steps.length + 1}',
          title: title,
          status: status,
        );
      } else if (trimmed.startsWith('Decision:')) {
        currentDecision = trimmed.substring('Decision:'.length).trim().replaceAll('"', '');
      } else if (trimmed.startsWith('Reason:')) {
        currentReason = trimmed.substring('Reason:'.length).trim().replaceAll('"', '');
      } else if (trimmed.startsWith('Context selected:') || trimmed.startsWith('Selected Context:')) {
        // context items
      } else if (trimmed.startsWith('Planned TASK-') || trimmed.startsWith('- Planned TASK-') || trimmed.startsWith('TASK-')) {
        final item = trimmed.replaceFirst('- ', '').replaceAll('`', '').trim();
        final taskIdMatch = RegExp(r'TASK-\d+').firstMatch(item);
        final taskId = taskIdMatch?.group(0) ?? 'TASK';
        currentTasks.add({
          'title': item,
          'task_id': taskId,
          'worker_type': item.contains('Programmer') ? 'Programmer' : (item.contains('Tester') ? 'Tester' : 'Researcher'),
        });
      } else if (trimmed.startsWith('- `') || (trimmed.startsWith('- ') && currentStep != null)) {
        final item = trimmed.replaceFirst('- ', '').replaceAll('`', '').trim();
        if (item.contains('TASK-') || item.contains('Programmer') || item.contains('Tester') || item.contains('Researcher')) {
          currentTasks.add({
            'title': item,
            'task_id': item.split(' ').first,
            'worker_type': item.contains('Programmer') ? 'Programmer' : (item.contains('Tester') ? 'Tester' : 'Researcher'),
          });
        } else if (item.contains('.') || item.contains('/')) {
          currentContext.add(item);
        }
      } else if (trimmed.startsWith('Worker Prompt:')) {
        currentPrompt = trimmed.substring('Worker Prompt:'.length).trim();
      }
    }
    flushStep();

    if (steps.isEmpty && text.isNotEmpty) {
      steps.add(ManagerActivityStep(
        id: 'step-1',
        title: text.split('\n').first,
        status: ActivityEventStatus.completed,
      ));
    }

    return steps;
  }
}

enum _BlockType {
  paragraph,
  heading1,
  heading2,
  heading3,
  divider,
  table,
  bulletList,
  orderedList,
  callout,
  codeBlock,
  toolRun,
  actionGroup,
  workingTreeDiff,
  statusPill,
  taskContract,
  managerActivity,
}

class _ParsedBlock {
  final _BlockType type;
  final String title;
  final String content;
  final int addedLines;
  final int removedLines;
  final List<String> items;
  final List<_DiffFileModel> diffFiles;
  final List<String> tableHeaders;
  final List<List<String>> tableRows;

  _ParsedBlock({
    required this.type,
    required this.title,
    required this.content,
    this.addedLines = 0,
    this.removedLines = 0,
    this.items = const [],
    this.diffFiles = const [],
    this.tableHeaders = const [],
    this.tableRows = const [],
  });
}

class _DiffFileModel {
  final String filename;
  final int additions;
  final int deletions;
  final List<String> diffLines;
  bool isExpanded;

  _DiffFileModel({
    required this.filename,
    required this.additions,
    required this.deletions,
    required this.diffLines,
    this.isExpanded = false,
  });
}

// --- Action Group Card (Inspiration 2) ---
class _ActionGroupCard extends StatelessWidget {
  final List<String> items;
  final bool isDark;

  const _ActionGroupCard({required this.items, required this.isDark});

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.symmetric(vertical: AppTokens.space8),
      decoration: BoxDecoration(
        color: isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
        borderRadius: AppTokens.borderRadiusMd,
        border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
      ),
      child: Column(
        children: items.asMap().entries.map((entry) {
          final idx = entry.key;
          final item = entry.value;
          final isLast = idx == items.length - 1;

          return Column(
            children: [
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: AppTokens.space14, vertical: AppTokens.space10),
                child: Row(
                  children: [
                    Expanded(
                      child: Text(
                        item,
                        style: TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.w400,
                          color: isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary,
                        ),
                      ),
                    ),
                    Icon(
                      Icons.chevron_right,
                      size: 15,
                      color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
                    ),
                  ],
                ),
              ),
              if (!isLast)
                Divider(
                  height: 1,
                  color: isDark ? AppTokens.darkBorder.withOpacity(0.6) : AppTokens.lightBorder,
                ),
            ],
          );
        }).toList(),
      ),
    );
  }
}

// --- Working Tree & Unified Diff Viewer (Inspiration 3) ---
class _WorkingTreeDiffView extends StatefulWidget {
  final String branch;
  final List<_DiffFileModel> diffFiles;
  final bool isDark;

  const _WorkingTreeDiffView({
    required this.branch,
    required this.diffFiles,
    required this.isDark,
  });

  @override
  State<_WorkingTreeDiffView> createState() => _WorkingTreeDiffViewState();
}

class _WorkingTreeDiffViewState extends State<_WorkingTreeDiffView> {
  late List<_DiffFileModel> _files;

  @override
  void initState() {
    super.initState();
    _files = List.from(widget.diffFiles);
    if (_files.isNotEmpty && !_files.any((f) => f.isExpanded)) {
      _files.last.isExpanded = true;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.symmetric(vertical: AppTokens.space12),
      decoration: BoxDecoration(
        color: widget.isDark ? const Color(0xFF14161C) : Colors.white,
        borderRadius: AppTokens.borderRadiusMd,
        border: Border.all(color: widget.isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // Header: 📁 main → working tree
          Container(
            padding: const EdgeInsets.symmetric(horizontal: AppTokens.space14, vertical: AppTokens.space10),
            decoration: BoxDecoration(
              color: widget.isDark ? const Color(0xFF181A22) : const Color(0xFFF6F8FA),
              borderRadius: const BorderRadius.vertical(top: Radius.circular(AppTokens.radiusMd)),
              border: Border(bottom: BorderSide(color: widget.isDark ? AppTokens.darkBorder : AppTokens.lightBorder)),
            ),
            child: Row(
              children: [
                const Icon(Icons.folder_open_outlined, size: 15, color: AppTokens.darkTextMuted),
                const SizedBox(width: AppTokens.space8),
                Text(
                  widget.branch,
                  style: TextStyle(
                    fontSize: 12.5,
                    fontWeight: FontWeight.w600,
                    color: widget.isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
                  ),
                ),
                const Spacer(),
                const Icon(Icons.fullscreen_outlined, size: 15, color: AppTokens.darkTextMuted),
                const SizedBox(width: AppTokens.space8),
                const Icon(Icons.close, size: 14, color: AppTokens.darkTextMuted),
              ],
            ),
          ),

          // File items list
          ..._files.map((file) => _buildFileDiffItem(file)),
        ],
      ),
    );
  }

  Widget _buildFileDiffItem(_DiffFileModel file) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        InkWell(
          onTap: () {
            setState(() {
              file.isExpanded = !file.isExpanded;
            });
          },
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: AppTokens.space14, vertical: AppTokens.space8),
            decoration: BoxDecoration(
              color: file.isExpanded
                  ? (widget.isDark ? const Color(0xFF1E2230).withOpacity(0.5) : const Color(0xFFEEF2FF))
                  : Colors.transparent,
              border: file.isExpanded
                  ? Border.all(color: AppTokens.brandSecondary.withOpacity(0.4))
                  : null,
            ),
            child: Row(
              children: [
                Icon(
                  file.isExpanded ? Icons.keyboard_arrow_down : Icons.chevron_right,
                  size: 15,
                  color: widget.isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
                ),
                const SizedBox(width: 6),
                Text(
                  file.filename,
                  style: TextStyle(
                    fontSize: 12.5,
                    fontFamily: 'monospace',
                    fontWeight: FontWeight.w500,
                    color: widget.isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
                  ),
                ),
                const SizedBox(width: AppTokens.space8),
                Text(
                  '+${file.additions}',
                  style: const TextStyle(fontSize: 11.5, fontWeight: FontWeight.w600, color: AppTokens.diffAdded),
                ),
                const SizedBox(width: 4),
                Text(
                  '-${file.deletions}',
                  style: const TextStyle(fontSize: 11.5, fontWeight: FontWeight.w600, color: AppTokens.diffRemoved),
                ),
              ],
            ),
          ),
        ),

        // Expanded Unified Diff Content with Line Numbers
        if (file.isExpanded)
          Container(
            padding: const EdgeInsets.symmetric(vertical: 6),
            color: widget.isDark ? const Color(0xFF0F1117) : const Color(0xFFF8FAFC),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: file.diffLines.asMap().entries.map((entry) {
                final lineIdx = entry.key + 1;
                final line = entry.value;
                final isAdd = line.startsWith('+') && !line.startsWith('+++');
                final isDel = line.startsWith('-') && !line.startsWith('---');

                Color textColor = widget.isDark ? const Color(0xFFCBD5E1) : const Color(0xFF334155);
                Color? lineBg;
                if (isAdd) {
                  textColor = AppTokens.diffAdded;
                  lineBg = AppTokens.diffAdded.withOpacity(0.12);
                } else if (isDel) {
                  textColor = const Color(0xFFF87171);
                  lineBg = AppTokens.diffRemoved.withOpacity(0.12);
                }

                return Container(
                  color: lineBg,
                  padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 1.5),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      SizedBox(
                        width: 32,
                        child: Text(
                          '$lineIdx',
                          style: TextStyle(
                            fontSize: 11,
                            fontFamily: 'monospace',
                            color: isDel ? const Color(0xFFF87171) : AppTokens.darkTextMuted,
                          ),
                        ),
                      ),
                      SizedBox(
                        width: 14,
                        child: Text(
                          isAdd ? '+' : (isDel ? '-' : ' '),
                          style: TextStyle(
                            fontSize: 11.5,
                            fontWeight: FontWeight.bold,
                            fontFamily: 'monospace',
                            color: textColor,
                          ),
                        ),
                      ),
                      Expanded(
                        child: Text(
                          isAdd || isDel ? line.substring(1) : line,
                          style: TextStyle(
                            fontSize: 12,
                            fontFamily: 'monospace',
                            color: textColor,
                            height: 1.35,
                          ),
                        ),
                      ),
                    ],
                  ),
                );
              }).toList(),
            ),
          ),
      ],
    );
  }
}

// --- Interactive Collapsible Tool Execution Row (Inspirations 1 & 4) ---
class _CollapsibleToolRow extends StatefulWidget {
  final String title;
  final String details;
  final int addedLines;
  final int removedLines;
  final bool isDark;

  const _CollapsibleToolRow({
    required this.title,
    required this.details,
    required this.addedLines,
    required this.removedLines,
    required this.isDark,
  });

  @override
  State<_CollapsibleToolRow> createState() => _CollapsibleToolRowState();
}

class _CollapsibleToolRowState extends State<_CollapsibleToolRow> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final hasDetails = widget.details.isNotEmpty;

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: InkWell(
        onTap: hasDetails ? () => setState(() => _expanded = !_expanded) : null,
        borderRadius: AppTokens.borderRadiusSm,
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 3, horizontal: 2),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  _buildTitleSpan(widget.title),
                  if (widget.addedLines > 0 || widget.removedLines > 0) ...[
                    const SizedBox(width: AppTokens.space8),
                    if (widget.addedLines > 0)
                      Text(
                        '+${widget.addedLines}',
                        style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.bold, color: AppTokens.diffAdded),
                      ),
                    if (widget.removedLines > 0) ...[
                      const SizedBox(width: 4),
                      Text(
                        '-${widget.removedLines}',
                        style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.bold, color: AppTokens.diffRemoved),
                      ),
                    ],
                  ],
                  const SizedBox(width: 6),
                  Icon(
                    _expanded ? Icons.keyboard_arrow_down : Icons.chevron_right,
                    size: 15,
                    color: widget.isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
                  ),
                ],
              ),
              if (_expanded && hasDetails) ...[
                const SizedBox(height: AppTokens.space6),
                Container(
                  padding: const EdgeInsets.all(AppTokens.space10),
                  decoration: BoxDecoration(
                    color: widget.isDark ? AppTokens.darkSurface : AppTokens.lightBorder,
                    borderRadius: AppTokens.borderRadiusSm,
                    border: Border.all(color: widget.isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
                  ),
                  child: SelectableText(
                    widget.details,
                    style: const TextStyle(fontSize: 12, fontFamily: 'monospace', height: 1.4),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildTitleSpan(String title) {
    final spans = <InlineSpan>[];
    final parts = title.split('used');

    if (parts.length > 1) {
      spans.add(TextSpan(text: parts[0]));
      spans.add(const TextSpan(
        text: 'used',
        style: TextStyle(color: Color(0xFFF87171), fontWeight: FontWeight.w500),
      ));
      spans.add(TextSpan(text: parts.sublist(1).join('used')));
    } else {
      spans.add(TextSpan(text: title));
    }

    return Text.rich(
      TextSpan(
        children: spans,
        style: TextStyle(
          fontSize: 13,
          fontWeight: FontWeight.w400,
          color: widget.isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary,
        ),
      ),
    );
  }
}

// --- Task Contract Card ---
class _TaskContractCard extends StatefulWidget {
  final String rawContent;
  final bool isDark;

  const _TaskContractCard({required this.rawContent, required this.isDark});

  @override
  State<_TaskContractCard> createState() => _TaskContractCardState();
}

class _TaskContractCardState extends State<_TaskContractCard> {
  bool _expanded = true;

  @override
  Widget build(BuildContext context) {
    final taskIdMatch = RegExp(r'TASK CONTRACT:\s*([^\n-]+)').firstMatch(widget.rawContent);
    final taskId = taskIdMatch?.group(1)?.trim() ?? 'TASK CONTRACT';

    final workerMatch = RegExp(r'Assigned Worker:\s*([^\n]+)').firstMatch(widget.rawContent);
    final workerName = workerMatch?.group(1)?.trim() ?? 'Specialist Worker';

    final isResearcher = workerName.toLowerCase().contains('researcher');
    final isProgrammer = workerName.toLowerCase().contains('programmer');
    final isTester = workerName.toLowerCase().contains('tester');

    final workerColor = isProgrammer
        ? AppTokens.brandSecondary
        : isResearcher
            ? AppTokens.brandPrimary
            : isTester
                ? AppTokens.purple
                : AppTokens.darkTextMuted;

    return Container(
      margin: const EdgeInsets.symmetric(vertical: AppTokens.space8),
      decoration: BoxDecoration(
        color: widget.isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
        borderRadius: AppTokens.borderRadiusMd,
        border: Border.all(
          color: widget.isDark ? AppTokens.darkBorder : AppTokens.lightBorder,
          width: 1.2,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          InkWell(
            onTap: () => setState(() => _expanded = !_expanded),
            borderRadius: AppTokens.borderRadiusMd,
            child: Padding(
              padding: const EdgeInsets.all(AppTokens.space12),
              child: Row(
                children: [
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: AppTokens.space8, vertical: 3),
                    decoration: BoxDecoration(
                      color: workerColor.withOpacity(0.15),
                      borderRadius: AppTokens.borderRadiusXs,
                      border: Border.all(color: workerColor.withOpacity(0.4)),
                    ),
                    child: Text(
                      '$taskId • $workerName',
                      style: TextStyle(
                        fontSize: 11.5,
                        fontWeight: FontWeight.bold,
                        fontFamily: 'monospace',
                        color: workerColor,
                      ),
                    ),
                  ),
                  const SizedBox(width: AppTokens.space8),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: AppTokens.space8, vertical: 2),
                    decoration: BoxDecoration(
                      color: AppTokens.warning.withOpacity(0.12),
                      borderRadius: AppTokens.borderRadiusXs,
                    ),
                    child: const Text(
                      'PLANNED (Dry-Run)',
                      style: TextStyle(
                        fontSize: 10,
                        fontWeight: FontWeight.bold,
                        color: AppTokens.warning,
                      ),
                    ),
                  ),
                  const Spacer(),
                  IconButton(
                    icon: const Icon(Icons.copy_outlined, size: 14),
                    tooltip: 'Copy contract',
                    visualDensity: VisualDensity.compact,
                    onPressed: () {
                      Clipboard.setData(ClipboardData(text: widget.rawContent));
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(content: Text('Task contract copied to clipboard'), duration: Duration(seconds: 2)),
                      );
                    },
                  ),
                  Icon(
                    _expanded ? Icons.keyboard_arrow_up : Icons.keyboard_arrow_down,
                    size: 16,
                    color: widget.isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
                  ),
                ],
              ),
            ),
          ),
          if (_expanded) ...[
            const Divider(height: 1),
            Padding(
              padding: const EdgeInsets.all(AppTokens.space14),
              child: SelectableText(
                widget.rawContent,
                style: TextStyle(
                  fontFamily: 'monospace',
                  fontSize: 12,
                  height: 1.45,
                  color: widget.isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

// --- Code Fence View ---
class _CodeFenceView extends StatelessWidget {
  final String code;
  final String language;
  final bool isDark;

  const _CodeFenceView({
    required this.code,
    required this.language,
    required this.isDark,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.symmetric(vertical: AppTokens.space8),
      decoration: BoxDecoration(
        color: isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
        borderRadius: AppTokens.borderRadiusMd,
        border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: AppTokens.space12, vertical: AppTokens.space4),
            decoration: BoxDecoration(
              border: Border(bottom: BorderSide(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder)),
            ),
            child: Row(
              children: [
                Text(
                  language.isNotEmpty ? language : 'code',
                  style: const TextStyle(fontSize: 11, fontFamily: 'monospace', color: AppTokens.darkTextMuted),
                ),
                const Spacer(),
                IconButton(
                  icon: const Icon(Icons.copy_outlined, size: 13),
                  tooltip: 'Copy code snippet',
                  visualDensity: VisualDensity.compact,
                  onPressed: () {
                    Clipboard.setData(ClipboardData(text: code));
                    ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(content: Text('Code snippet copied to clipboard'), duration: Duration(seconds: 2)),
                    );
                  },
                ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(AppTokens.space12),
            child: SelectableText(
              code,
              style: TextStyle(
                fontFamily: 'monospace',
                fontSize: 12.5,
                height: 1.45,
                color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// --- Status Annotation ---
class _StatusAnnotation extends StatelessWidget {
  final String text;
  final bool isDark;

  const _StatusAnnotation({required this.text, required this.isDark});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppTokens.space6),
      child: SelectableText(
        text,
        style: TextStyle(
          fontSize: 12.5,
          fontWeight: FontWeight.w400,
          color: isDark ? AppTokens.darkTextMuted : AppTokens.lightTextMuted,
        ),
      ),
    );
  }
}

// --- Markdown Paragraph Renderer with Inline Monospace Highlight ---
class _MarkdownParagraph extends StatelessWidget {
  final String text;
  final bool isDark;

  const _MarkdownParagraph({required this.text, required this.isDark});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: AppTokens.space10),
      child: _InlineMarkdownText(text: text, isDark: isDark),
    );
  }
}

// --- Rich Inline Markdown Text Renderer ---
class _InlineMarkdownText extends StatelessWidget {
  final String text;
  final bool isDark;
  final double fontSize;
  final FontWeight fontWeight;
  final double height;

  const _InlineMarkdownText({
    required this.text,
    required this.isDark,
    this.fontSize = 13.5,
    this.fontWeight = FontWeight.normal,
    this.height = 1.55,
  });

  @override
  Widget build(BuildContext context) {
    final spans = <InlineSpan>[];

    final regex = RegExp(
      r'(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)]+\)|\b(?:[a-zA-Z0-9_\-\.\/]+)\.(?:tsx|ts|jsx|js|py|dart|json|md|yaml|yml|css|scss|html)(?::\d+)?\b)',
    );

    int lastIndex = 0;
    final defaultColor = isDark ? const Color(0xFFE2E8F0) : const Color(0xFF0F172A);

    for (final match in regex.allMatches(text)) {
      if (match.start > lastIndex) {
        spans.add(TextSpan(
          text: text.substring(lastIndex, match.start),
          style: TextStyle(
            fontSize: fontSize,
            fontWeight: fontWeight,
            height: height,
            color: defaultColor,
          ),
        ));
      }

      final raw = match.group(0) ?? '';

      if (raw.startsWith('`') && raw.endsWith('`') && raw.length >= 2) {
        final code = raw.substring(1, raw.length - 1);
        spans.add(WidgetSpan(
          alignment: PlaceholderAlignment.middle,
          child: Container(
            margin: const EdgeInsets.symmetric(horizontal: 2),
            padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1.5),
            decoration: BoxDecoration(
              color: isDark ? const Color(0xFF1E293B) : const Color(0xFFF1F5F9),
              borderRadius: BorderRadius.circular(4),
              border: Border.all(color: isDark ? const Color(0xFF334155) : const Color(0xFFCBD5E1)),
            ),
            child: Text(
              code,
              style: TextStyle(
                fontSize: fontSize - 1,
                fontFamily: 'monospace',
                fontWeight: FontWeight.w500,
                color: isDark ? const Color(0xFF93C5FD) : const Color(0xFF1D4ED8),
              ),
            ),
          ),
        ));
      } else if (raw.startsWith('**') && raw.endsWith('**') && raw.length >= 4) {
        final boldContent = raw.substring(2, raw.length - 2).trim();
        spans.add(TextSpan(
          text: boldContent,
          style: TextStyle(
            fontSize: fontSize,
            fontWeight: FontWeight.w700,
            height: height,
            color: isDark ? const Color(0xFFF8FAFC) : const Color(0xFF0F172A),
          ),
        ));
      } else if (raw.startsWith('*') && raw.endsWith('*') && raw.length >= 2) {
        final italicContent = raw.substring(1, raw.length - 1).trim();
        spans.add(TextSpan(
          text: italicContent,
          style: TextStyle(
            fontSize: fontSize,
            fontStyle: FontStyle.italic,
            height: height,
            color: defaultColor,
          ),
        ));
      } else if (raw.startsWith('[') && raw.contains('](') && raw.endsWith(')')) {
        final linkMatch = RegExp(r'\[([^\]]+)\]\(([^)]+)\)').firstMatch(raw);
        if (linkMatch != null) {
          final label = linkMatch.group(1) ?? '';
          spans.add(TextSpan(
            text: label,
            style: TextStyle(
              fontSize: fontSize,
              fontWeight: FontWeight.w600,
              color: isDark ? const Color(0xFF38BDF8) : const Color(0xFF0284C7),
              decoration: TextDecoration.underline,
            ),
          ));
        }
      } else {
        // File path badge
        spans.add(WidgetSpan(
          alignment: PlaceholderAlignment.middle,
          child: Container(
            margin: const EdgeInsets.symmetric(horizontal: 2),
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1.5),
            decoration: BoxDecoration(
              color: isDark ? const Color(0xFF1E293B) : const Color(0xFFF1F5F9),
              borderRadius: BorderRadius.circular(4),
              border: Border.all(color: isDark ? const Color(0xFF334155) : const Color(0xFFCBD5E1)),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(
                  Icons.insert_drive_file_outlined,
                  size: 11,
                  color: isDark ? const Color(0xFF94A3B8) : const Color(0xFF64748B),
                ),
                const SizedBox(width: 3),
                Text(
                  raw,
                  style: TextStyle(
                    fontSize: fontSize - 1.5,
                    fontFamily: 'monospace',
                    color: isDark ? const Color(0xFF93C5FD) : const Color(0xFF1D4ED8),
                  ),
                ),
              ],
            ),
          ),
        ));
      }

      lastIndex = match.end;
    }

    if (lastIndex < text.length) {
      spans.add(TextSpan(
        text: text.substring(lastIndex),
        style: TextStyle(
          fontSize: fontSize,
          fontWeight: fontWeight,
          height: height,
          color: defaultColor,
        ),
      ));
    }

    return SelectableText.rich(
      TextSpan(children: spans),
    );
  }
}

// --- Heading 1 (Large Title) ---
class _Heading1View extends StatelessWidget {
  final String text;
  final bool isDark;

  const _Heading1View({required this.text, required this.isDark});

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(top: AppTokens.space20, bottom: AppTokens.space10),
      padding: const EdgeInsets.only(bottom: AppTokens.space8),
      decoration: BoxDecoration(
        border: Border(
          bottom: BorderSide(
            color: isDark ? const Color(0xFF334155) : const Color(0xFFE2E8F0),
            width: 1.5,
          ),
        ),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Container(
            padding: const EdgeInsets.all(5),
            margin: const EdgeInsets.only(right: 10),
            decoration: BoxDecoration(
              color: isDark ? const Color(0xFF1E293B) : const Color(0xFFF1F5F9),
              borderRadius: BorderRadius.circular(6),
            ),
            child: Icon(
              Icons.auto_awesome,
              size: 16,
              color: isDark ? const Color(0xFF38BDF8) : const Color(0xFF0284C7),
            ),
          ),
          Expanded(
            child: _InlineMarkdownText(
              text: text,
              isDark: isDark,
              fontSize: 18,
              fontWeight: FontWeight.w700,
            ),
          ),
        ],
      ),
    );
  }
}

// --- Heading 2 (Section Title with Indicator) ---
class _Heading2View extends StatelessWidget {
  final String text;
  final bool isDark;

  const _Heading2View({required this.text, required this.isDark});

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(top: AppTokens.space16, bottom: AppTokens.space8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Container(
            width: 3.5,
            height: 18,
            margin: const EdgeInsets.only(right: 8),
            decoration: BoxDecoration(
              color: isDark ? const Color(0xFF38BDF8) : const Color(0xFF0284C7),
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          Expanded(
            child: _InlineMarkdownText(
              text: text,
              isDark: isDark,
              fontSize: 15.5,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}

// --- Heading 3 (Subsection Title) ---
class _Heading3View extends StatelessWidget {
  final String text;
  final bool isDark;

  const _Heading3View({required this.text, required this.isDark});

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(top: AppTokens.space12, bottom: AppTokens.space6),
      child: _InlineMarkdownText(
        text: text,
        isDark: isDark,
        fontSize: 14,
        fontWeight: FontWeight.w600,
      ),
    );
  }
}

// --- Clean Divider ---
class _DividerView extends StatelessWidget {
  final bool isDark;

  const _DividerView({required this.isDark});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppTokens.space12),
      child: Divider(
        height: 1,
        thickness: 1,
        color: isDark ? const Color(0xFF334155) : const Color(0xFFE2E8F0),
      ),
    );
  }
}

// --- Structured Markdown Data Table ---
class _MarkdownTableView extends StatelessWidget {
  final List<String> headers;
  final List<List<String>> rows;
  final bool isDark;

  const _MarkdownTableView({
    required this.headers,
    required this.rows,
    required this.isDark,
  });

  @override
  Widget build(BuildContext context) {
    if (headers.isEmpty && rows.isEmpty) return const SizedBox.shrink();

    final borderColor = isDark ? const Color(0xFF334155) : const Color(0xFFE2E8F0);
    final headerBg = isDark ? const Color(0xFF1E293B) : const Color(0xFFF1F5F9);
    final tableBg = isDark ? const Color(0xFF0F172A) : Colors.white;
    final zebraBg = isDark ? const Color(0xFF151E2E) : const Color(0xFFF8FAFC);

    final columnCount = headers.isNotEmpty
        ? headers.length
        : (rows.isNotEmpty ? rows.first.length : 0);

    double getColWidth(int idx) {
      if (idx >= headers.length) {
        if (idx == 0) return 50;
        return 220;
      }
      final name = headers[idx].toLowerCase().trim();
      if (name == '#' || name == 'id') return 50;
      if (name == 'ice' || name == 'score' || name == 'effort') return 75;
      if (name.contains('file') || name == 'ticket' || name == 'owner') return 180;
      if (name == 'issue' || name == 'initiative') return 260;
      if (name == 'fix' || name == 'verification' || name == 'dependencies') return 300;
      return 200;
    }

    final totalColWidth = List.generate(columnCount, (i) => getColWidth(i)).fold(0.0, (a, b) => a + b);
    final totalTableWidth = totalColWidth + 28.0;

    return Container(
      margin: const EdgeInsets.symmetric(vertical: AppTokens.space12),
      decoration: BoxDecoration(
        color: tableBg,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: borderColor, width: 1),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(isDark ? 0.2 : 0.05),
            blurRadius: 4,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      clipBehavior: Clip.antiAlias,
      child: LayoutBuilder(
        builder: (context, constraints) {
          final hasBoundedWidth = constraints.hasBoundedWidth && constraints.maxWidth > 0 && constraints.maxWidth.isFinite;
          final double effectiveWidth = (hasBoundedWidth && constraints.maxWidth > totalTableWidth)
              ? constraints.maxWidth
              : totalTableWidth;
          final double scale = (hasBoundedWidth && constraints.maxWidth > totalTableWidth && totalColWidth > 0)
              ? (constraints.maxWidth - 28.0) / totalColWidth
              : 1.0;

          return SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: SizedBox(
              width: effectiveWidth,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (headers.isNotEmpty) ...[
                    Container(
                      color: headerBg,
                      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                      child: Row(
                        children: headers.asMap().entries.map((entry) {
                          final idx = entry.key;
                          final headerText = entry.value;

                          return Container(
                            width: getColWidth(idx) * scale,
                            padding: const EdgeInsets.only(right: 12),
                            child: Text(
                              headerText.toUpperCase(),
                              style: TextStyle(
                                fontSize: 11,
                                fontWeight: FontWeight.w700,
                                letterSpacing: 0.6,
                                color: isDark ? const Color(0xFF94A3B8) : const Color(0xFF475569),
                              ),
                            ),
                          );
                        }).toList(),
                      ),
                    ),
                    Divider(height: 1, thickness: 1, color: borderColor),
                  ],
                  ...rows.asMap().entries.map((rowEntry) {
                    final rowIdx = rowEntry.key;
                    final row = rowEntry.value;
                    final isZebra = rowIdx % 2 == 1;

                    return Container(
                      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                      decoration: BoxDecoration(
                        color: isZebra ? zebraBg : tableBg,
                        border: Border(
                          bottom: rowIdx < rows.length - 1
                              ? BorderSide(color: borderColor.withOpacity(0.5), width: 1)
                              : BorderSide.none,
                        ),
                      ),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: List.generate(columnCount, (colIdx) {
                          final cellText = colIdx < row.length ? row[colIdx] : '';

                          return Container(
                            width: getColWidth(colIdx) * scale,
                            padding: const EdgeInsets.only(right: 12),
                            child: _InlineMarkdownText(
                              text: cellText,
                              isDark: isDark,
                              fontSize: 12.5,
                            ),
                          );
                        }),
                      ),
                    );
                  }),
                ],
              ),
            ),
          );
        },
      ),
    );
  }
}

// --- Bullet List with Custom Dots ---
class _BulletListView extends StatelessWidget {
  final List<String> items;
  final bool isDark;

  const _BulletListView({required this.items, required this.isDark});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppTokens.space4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: items.map((item) {
          return Padding(
            padding: const EdgeInsets.only(bottom: AppTokens.space6),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  margin: const EdgeInsets.only(top: 7, right: 10),
                  width: 5,
                  height: 5,
                  decoration: BoxDecoration(
                    color: isDark ? const Color(0xFF38BDF8) : const Color(0xFF0284C7),
                    shape: BoxShape.circle,
                  ),
                ),
                Expanded(
                  child: _InlineMarkdownText(text: item, isDark: isDark),
                ),
              ],
            ),
          );
        }).toList(),
      ),
    );
  }
}

// --- Numbered List with Custom Badges ---
class _OrderedListView extends StatelessWidget {
  final List<String> items;
  final bool isDark;

  const _OrderedListView({required this.items, required this.isDark});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppTokens.space4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: items.asMap().entries.map((entry) {
          final idx = entry.key;
          final item = entry.value;

          return Padding(
            padding: const EdgeInsets.only(bottom: AppTokens.space6),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  margin: const EdgeInsets.only(top: 2, right: 8),
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                  decoration: BoxDecoration(
                    color: isDark ? const Color(0xFF1E293B) : const Color(0xFFE2E8F0),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Text(
                    '${idx + 1}',
                    style: TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.bold,
                      fontFamily: 'monospace',
                      color: isDark ? const Color(0xFF38BDF8) : const Color(0xFF0284C7),
                    ),
                  ),
                ),
                Expanded(
                  child: _InlineMarkdownText(text: item, isDark: isDark),
                ),
              ],
            ),
          );
        }).toList(),
      ),
    );
  }
}

// --- Callout Box / Action Proposal Box ---
class _CalloutBoxView extends StatelessWidget {
  final String text;
  final bool isDark;
  final String? title;
  final IconData? icon;

  const _CalloutBoxView({
    required this.text,
    required this.isDark,
    this.title,
    this.icon,
  });

  @override
  Widget build(BuildContext context) {
    final accentColor = isDark ? const Color(0xFF38BDF8) : const Color(0xFF0284C7);
    final borderColor = isDark ? const Color(0xFF334155) : const Color(0xFFE2E8F0);
    final bgColor = isDark ? const Color(0xFF1E2433) : const Color(0xFFF8FAFC);

    return Container(
      margin: const EdgeInsets.symmetric(vertical: AppTokens.space10),
      decoration: BoxDecoration(
        color: bgColor,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: borderColor),
      ),
      clipBehavior: Clip.antiAlias,
      child: Stack(
        children: [
          Positioned(
            left: 0,
            top: 0,
            bottom: 0,
            child: Container(
              width: 4,
              color: accentColor,
            ),
          ),
          Padding(
            padding: const EdgeInsets.only(
              left: 18,
              top: 14,
              right: 14,
              bottom: 14,
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (title != null) ...[
                  Row(
                    children: [
                      Icon(
                        icon ?? Icons.help_outline_rounded,
                        size: 16,
                        color: accentColor,
                      ),
                      const SizedBox(width: 8),
                      Text(
                        title!,
                        style: TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.bold,
                          color: isDark ? const Color(0xFFF8FAFC) : const Color(0xFF0F172A),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                ],
                _InlineMarkdownText(text: text, isDark: isDark),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
