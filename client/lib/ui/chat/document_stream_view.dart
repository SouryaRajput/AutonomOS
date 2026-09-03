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

  // --- Assistant / Manager Turn (Continuous Minimalist Conversational Prose) ---
  Widget _buildAssistantDocumentTurn(BuildContext context, ChatMessage msg, bool isDark) {
    final extracted = MessageSanitizer.extractUserFacingNarrative(msg.content);
    final rawContent = extracted.userFacingNarrative;
    final blocks = _parseParagraphsAndTools(rawContent);

    return Container(
      margin: const EdgeInsets.only(bottom: AppTokens.space20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: blocks.map((block) {
          switch (block.type) {
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

    bool inCodeBlock = false;
    String codeLang = '';
    final codeBuffer = StringBuffer();
    final textBuffer = StringBuffer();

    void flushText() {
      if (textBuffer.isNotEmpty) {
        final t = textBuffer.toString().trim();
        if (t.isNotEmpty) {
          if (MessageSanitizer.isInternalToolJson(t)) {
            // Internal tool invocation: hide from normal chat transcript
          } else if (t.startsWith('--- TASK CONTRACT:')) {
            blocks.add(_ParsedBlock(type: _BlockType.taskContract, title: '', content: t));
          } else if (t.contains('📁 main → working tree') || t.contains('working tree')) {
            blocks.add(_parseWorkingTreeDiff(t));
          } else if (t.toLowerCase().contains('context selected:') || t.toLowerCase().startsWith('context selected:')) {
            // Strip raw context file dump — replace with clean conversational line
            blocks.add(_ParsedBlock(
              type: _BlockType.paragraph,
              title: '',
              content: 'I inspected the workspace and verified the active repository structure.',
            ));
          } else if (t.contains('● ') || (t.contains('✓ ') && (t.contains('Project') || t.contains('TASK') || t.contains('files') || t.contains('Map')))) {
            final cleanLines = t.split('\n')
                .where((l) => !l.contains('Decision:') && !l.contains('Reason:') && !l.contains('Planned TASK-') && !l.toLowerCase().contains('context selected:'))
                .map((l) {
                  final trimmed = l.trim();
                  if (trimmed.startsWith('✓ Project understood')) return 'I inspected the workspace and mapped the repository architecture.';
                  if (trimmed.startsWith('✓ Work plan prepared')) return '✓ Plan prepared — tasks defined across implementation and verification.';
                  if (trimmed.startsWith('⏸ Workers')) return "I'm waiting for a Programmer to become available before execution can begin.";
                  return trimmed;
                })
                .where((l) => l.isNotEmpty && !l.startsWith('- src/') && !l.startsWith('- public/') && !l.startsWith('- package.json'))
                .join('\n\n');
            if (cleanLines.isNotEmpty) {
              blocks.add(_ParsedBlock(type: _BlockType.paragraph, title: '', content: cleanLines));
            }
          } else if (t.startsWith('Read ') || t.startsWith('Ran ') || t.startsWith('Checked ') || t.startsWith('Updated ') || t.startsWith('Typechecked ') || t.startsWith('Linted ') || t.startsWith('Found ')) {
            // Check if multiple single-line actions form a grouped action list (Image 2)
            final actionLines = t.split('\n').where((l) => l.trim().isNotEmpty).toList();
            if (actionLines.length > 1 && actionLines.every((l) => l.trim().startsWith('Typechecked ') || l.trim().startsWith('Linted ') || l.trim().startsWith('Found ') || l.trim().startsWith('Read ') || l.trim().startsWith('Ran '))) {
              blocks.add(_ParsedBlock(
                type: _BlockType.actionGroup,
                title: '',
                content: '',
                items: actionLines.map((l) => l.trim().replaceAll(RegExp(r'\s*>$'), '')).toList(),
              ));
            } else {
              int added = 0;
              int removed = 0;
              final match = RegExp(r'\+(\d+)\s+-(\d+)').firstMatch(t);
              if (match != null) {
                added = int.tryParse(match.group(1) ?? '0') ?? 0;
                removed = int.tryParse(match.group(2) ?? '0') ?? 0;
              }
              final cleanTitle = t.replaceAll(RegExp(r'\+(\d+)\s+-(\d+)'), '').replaceAll(RegExp(r'\s*>$'), '').trim();
              blocks.add(_ParsedBlock(
                type: _BlockType.toolRun,
                title: cleanTitle,
                content: '',
                addedLines: added,
                removedLines: removed,
              ));
            }
          } else if (t.startsWith('Compacted conversation') || t.startsWith('Diagnosis is clear') || t.startsWith('Workers DISABLED') || t.startsWith('Workers disabled')) {
            blocks.add(_ParsedBlock(type: _BlockType.statusPill, title: '', content: t));
          } else {
            blocks.add(_ParsedBlock(type: _BlockType.paragraph, title: '', content: t));
          }
        }
        textBuffer.clear();
      }
    }

    for (final line in lines) {
      if (line.trim().startsWith('```')) {
        if (inCodeBlock) {
          inCodeBlock = false;
          final c = codeBuffer.toString().trim();
          if (MessageSanitizer.isInternalToolJson(c)) {
            // Internal tool call in code block: hide from normal chat transcript
          } else if (c.contains('TASK CONTRACT:')) {
            blocks.add(_ParsedBlock(
              type: _BlockType.taskContract,
              title: codeLang,
              content: c,
            ));
          } else if (codeLang == 'diff' || c.startsWith('diff --git') || (c.contains('@@') && (c.contains('\n-') || c.contains('\n+')))) {
            blocks.add(_parseWorkingTreeDiff(c));
          } else {
            blocks.add(_ParsedBlock(
              type: _BlockType.codeBlock,
              title: codeLang,
              content: c,
            ));
          }
          codeBuffer.clear();
          codeLang = '';
        } else {
          flushText();
          inCodeBlock = true;
          codeLang = line.trim().replaceFirst('```', '').trim();
        }
      } else if (inCodeBlock) {
        codeBuffer.writeln(line);
      } else {
        if (line.trim().isEmpty) {
          flushText();
        } else {
          textBuffer.writeln(line);
        }
      }
    }

    if (inCodeBlock) {
      final c = codeBuffer.toString().trim();
      if (!MessageSanitizer.isInternalToolJson(c)) {
        if (c.contains('TASK CONTRACT:')) {
          blocks.add(_ParsedBlock(type: _BlockType.taskContract, title: codeLang, content: c));
        } else {
          blocks.add(_ParsedBlock(type: _BlockType.codeBlock, title: codeLang, content: c));
        }
      }
    }
    flushText();

    return blocks.isNotEmpty ? blocks : [_ParsedBlock(type: _BlockType.paragraph, title: '', content: text)];
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

enum _BlockType { paragraph, codeBlock, toolRun, actionGroup, workingTreeDiff, statusPill, taskContract, managerActivity }

class _ParsedBlock {
  final _BlockType type;
  final String title;
  final String content;
  final int addedLines;
  final int removedLines;
  final List<String> items;
  final List<_DiffFileModel> diffFiles;

  _ParsedBlock({
    required this.type,
    required this.title,
    required this.content,
    this.addedLines = 0,
    this.removedLines = 0,
    this.items = const [],
    this.diffFiles = const [],
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
    final spans = <InlineSpan>[];
    final regex = RegExp(r'(`[^`]+`|\b\w+\.(?:tsx|ts|py|dart|json|md|yaml|yml)(?::\d+)?\b)');
    int lastIndex = 0;

    for (final match in regex.allMatches(text)) {
      if (match.start > lastIndex) {
        spans.add(TextSpan(
          text: text.substring(lastIndex, match.start),
          style: TextStyle(
            fontSize: 13.5,
            height: 1.55,
            color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
          ),
        ));
      }

      final rawMatched = match.group(1) ?? '';
      final cleanCode = rawMatched.startsWith('`') && rawMatched.endsWith('`')
          ? rawMatched.substring(1, rawMatched.length - 1)
          : rawMatched;

      spans.add(WidgetSpan(
        alignment: PlaceholderAlignment.middle,
        child: Container(
          margin: const EdgeInsets.symmetric(horizontal: 2),
          padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
          decoration: BoxDecoration(
            color: isDark ? AppTokens.darkElevated : AppTokens.lightBorder,
            borderRadius: AppTokens.borderRadiusXs,
            border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
          ),
          child: Text(
            cleanCode,
            style: TextStyle(
              fontSize: 12,
              fontFamily: 'monospace',
              color: isDark ? const Color(0xFF93C5FD) : const Color(0xFF1D4ED8),
            ),
          ),
        ),
      ));

      lastIndex = match.end;
    }

    if (lastIndex < text.length) {
      spans.add(TextSpan(
        text: text.substring(lastIndex),
        style: TextStyle(
          fontSize: 13.5,
          height: 1.55,
          color: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
        ),
      ));
    }

    return Padding(
      padding: const EdgeInsets.only(bottom: AppTokens.space10),
      child: Text.rich(
        TextSpan(children: spans),
      ),
    );
  }
}
