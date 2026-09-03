import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../../core/tokens/tokens.dart';
import '../../models/execution_activity.dart';

/// Compact, production-grade activity presentation card.
/// Provides live execution visibility (current action, completed steps, commands, files, workers, metrics)
/// without exposing raw internal payloads or flooding the chat transcript.
class ExecutionActivityCard extends StatefulWidget {
  final ExecutionActivity activity;
  final bool initialExpanded;

  const ExecutionActivityCard({
    super.key,
    required this.activity,
    this.initialExpanded = false,
  });

  @override
  State<ExecutionActivityCard> createState() => _ExecutionActivityCardState();
}

class _ExecutionActivityCardState extends State<ExecutionActivityCard> {
  late bool _isExpanded;

  @override
  void initState() {
    super.initState();
    _isExpanded = widget.initialExpanded;
  }

  Color _getStatusColor(ActivityStatus status, bool isDark) {
    switch (status) {
      case ActivityStatus.running:
        return const Color(0xFF38BDF8); // Cyan/blue
      case ActivityStatus.waiting:
        return const Color(0xFFFBBF24); // Amber
      case ActivityStatus.stalled:
        return const Color(0xFFF97316); // Orange
      case ActivityStatus.completed:
        return const Color(0xFF34D399); // Green
      case ActivityStatus.failed:
        return const Color(0xFFF87171); // Red
      case ActivityStatus.cancelled:
        return const Color(0xFF94A3B8); // Gray
      case ActivityStatus.pending:
        return isDark ? const Color(0xFF64748B) : const Color(0xFF94A3B8);
    }
  }

  String _getStatusLabel(ActivityStatus status) {
    switch (status) {
      case ActivityStatus.running:
        return 'Running';
      case ActivityStatus.waiting:
        return 'Waiting';
      case ActivityStatus.stalled:
        return 'Stalled';
      case ActivityStatus.completed:
        return 'Completed';
      case ActivityStatus.failed:
        return 'Failed';
      case ActivityStatus.cancelled:
        return 'Cancelled';
      case ActivityStatus.pending:
        return 'Pending';
    }
  }

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final act = widget.activity;
    final statusColor = _getStatusColor(act.status, isDark);

    final commandsCount = act.commands.length;
    final filesCount = act.filesRead.length + act.filesChanged.length;
    final workersCount = act.workers.length;
    final sourcesCount = (act.metrics['sources_collected'] as num?)?.toInt() ?? 0;
    final questionsAnswered = act.metrics['questions_answered']?.toString() ?? '';

    return Container(
      margin: const EdgeInsets.symmetric(vertical: AppTokens.space8),
      decoration: BoxDecoration(
        color: isDark ? const Color(0xFF131722) : const Color(0xFFF8FAFC),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(
          color: act.status == ActivityStatus.failed
              ? const Color(0xFFEF4444).withOpacity(0.5)
              : (isDark ? const Color(0xFF262D40) : const Color(0xFFE2E8F0)),
          width: 1,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 1. Top Header Row: Worker Identity + Live Status + Toggle
          InkWell(
            onTap: () => setState(() => _isExpanded = !_isExpanded),
            borderRadius: BorderRadius.circular(10),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: AppTokens.space12, vertical: AppTokens.space10),
              child: Row(
                children: [
                  // Status Indicator Dot
                  Container(
                    width: 8,
                    height: 8,
                    decoration: BoxDecoration(
                      color: statusColor,
                      shape: BoxShape.circle,
                      boxShadow: act.status == ActivityStatus.running && act.isLive
                          ? [BoxShadow(color: statusColor.withOpacity(0.6), blurRadius: 4, spreadRadius: 1)]
                          : null,
                    ),
                  ),
                  const SizedBox(width: AppTokens.space8),

                  // Worker Title (e.g. Researcher — Running)
                  Text(
                    '${act.workerType} — ${_getStatusLabel(act.status)}',
                    style: TextStyle(
                      fontSize: 12.5,
                      fontWeight: FontWeight.w600,
                      color: isDark ? const Color(0xFFE2E8F0) : const Color(0xFF1E293B),
                    ),
                  ),

                  if (act.isLive && act.status == ActivityStatus.running) ...[
                    const SizedBox(width: AppTokens.space6),
                    SizedBox(
                      width: 10,
                      height: 10,
                      child: CircularProgressIndicator(
                        strokeWidth: 1.5,
                        valueColor: AlwaysStoppedAnimation<Color>(statusColor),
                      ),
                    ),
                  ],

                  const Spacer(),

                  // Quick summary pill badges
                  if (commandsCount > 0)
                    _buildPill('Cmds: $commandsCount', isDark),
                  if (filesCount > 0) ...[
                    const SizedBox(width: 4),
                    _buildPill('Files: $filesCount', isDark),
                  ],
                  if (workersCount > 1) ...[
                    const SizedBox(width: 4),
                    _buildPill('Workers: $workersCount', isDark),
                  ],

                  const SizedBox(width: AppTokens.space8),
                  Icon(
                    _isExpanded ? Icons.keyboard_arrow_up : Icons.keyboard_arrow_down,
                    size: 16,
                    color: isDark ? const Color(0xFF64748B) : const Color(0xFF94A3B8),
                  ),
                ],
              ),
            ),
          ),

          const Divider(height: 1, thickness: 1, color: Color(0x15FFFFFF)),

          // 2. Main Content Body: Current Action + Completed Steps
          Padding(
            padding: const EdgeInsets.fromLTRB(AppTokens.space12, AppTokens.space8, AppTokens.space12, AppTokens.space10),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Current Action Row
                if (act.currentAction.isNotEmpty && act.status != ActivityStatus.completed)
                  Padding(
                    padding: const EdgeInsets.only(bottom: AppTokens.space6),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Current: ',
                          style: TextStyle(
                            fontSize: 11.5,
                            fontWeight: FontWeight.w600,
                            color: isDark ? const Color(0xFF94A3B8) : const Color(0xFF64748B),
                          ),
                        ),
                        Expanded(
                          child: Text(
                            act.currentAction,
                            style: TextStyle(
                              fontSize: 12,
                              color: statusColor,
                              fontWeight: FontWeight.w500,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),

                // Error Summary Banner (if failed)
                if (act.errorSummary != null && act.errorSummary!.isNotEmpty)
                  Container(
                    margin: const EdgeInsets.only(top: 4, bottom: 6),
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
                    decoration: BoxDecoration(
                      color: const Color(0xFFEF4444).withOpacity(0.12),
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(color: const Color(0xFFEF4444).withOpacity(0.3)),
                    ),
                    child: Row(
                      children: [
                        const Icon(Icons.error_outline, size: 14, color: Color(0xFFEF4444)),
                        const SizedBox(width: 6),
                        Expanded(
                          child: Text(
                            act.errorSummary!,
                            style: const TextStyle(fontSize: 11.5, color: Color(0xFFFCA5A5)),
                          ),
                        ),
                      ],
                    ),
                  ),

                // Completed Steps Summary (Live & finished)
                if (act.completedActions.isNotEmpty)
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: act.completedActions.take(_isExpanded ? 100 : 3).map((step) {
                      return Padding(
                        padding: const EdgeInsets.symmetric(vertical: 1.5),
                        child: Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              step.startsWith('✓') ? '✓' : (step.startsWith('❌') ? '❌' : (step.startsWith('⚠️') ? '⚠️' : '•')),
                              style: TextStyle(
                                fontSize: 11.5,
                                color: step.startsWith('❌')
                                    ? const Color(0xFFEF4444)
                                    : (step.startsWith('⚠️') ? const Color(0xFFFBBF24) : const Color(0xFF34D399)),
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                            const SizedBox(width: 6),
                            Expanded(
                              child: Text(
                                step.replaceFirst(RegExp(r'^[✓❌⚠️•]\s*'), ''),
                                style: TextStyle(
                                  fontSize: 11.5,
                                  color: isDark ? const Color(0xFFCBD5E1) : const Color(0xFF334155),
                                ),
                              ),
                            ),
                          ],
                        ),
                      );
                    }).toList(),
                  ),

                // Research statistics pill line
                if (sourcesCount > 0 || questionsAnswered.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 6),
                    child: Wrap(
                      spacing: 8,
                      runSpacing: 4,
                      children: [
                        if (sourcesCount > 0)
                          _buildStatBadge('Sources', sourcesCount.toString(), isDark),
                        if ((act.metrics['evidence_items'] as num?) != null)
                          _buildStatBadge('Evidence', act.metrics['evidence_items'].toString(), isDark),
                        if (questionsAnswered.isNotEmpty)
                          _buildStatBadge('Questions', questionsAnswered, isDark),
                      ],
                    ),
                  ),
              ],
            ),
          ),

          // 3. Expandable Deep Inspection Drawer (Commands, Files Read/Changed, Worker Details)
          if (_isExpanded) ...[
            const Divider(height: 1, thickness: 1, color: Color(0x15FFFFFF)),
            Padding(
              padding: const EdgeInsets.all(AppTokens.space12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // Commands Executed
                  if (act.commands.isNotEmpty) ...[
                    Text(
                      'COMMANDS EXECUTED',
                      style: TextStyle(
                        fontSize: 10,
                        fontWeight: FontWeight.bold,
                        letterSpacing: 0.5,
                        color: isDark ? const Color(0xFF64748B) : const Color(0xFF94A3B8),
                      ),
                    ),
                    const SizedBox(height: 4),
                    ...act.commands.map((cmd) => _buildCommandRow(cmd, isDark)),
                    const SizedBox(height: AppTokens.space10),
                  ],

                  // Files Activity (Read & Changed)
                  if (act.filesRead.isNotEmpty || act.filesChanged.isNotEmpty) ...[
                    Text(
                      'FILES ACTIVITY',
                      style: TextStyle(
                        fontSize: 10,
                        fontWeight: FontWeight.bold,
                        letterSpacing: 0.5,
                        color: isDark ? const Color(0xFF64748B) : const Color(0xFF94A3B8),
                      ),
                    ),
                    const SizedBox(height: 4),
                    if (act.filesChanged.isNotEmpty)
                      ...act.filesChanged.map((fc) => _buildFileChangeRow(fc, isDark)),
                    if (act.filesRead.isNotEmpty)
                      Wrap(
                        spacing: 6,
                        runSpacing: 4,
                        children: act.filesRead.take(20).map((f) {
                          return Container(
                            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                            decoration: BoxDecoration(
                              color: isDark ? const Color(0xFF1E2433) : const Color(0xFFE2E8F0),
                              borderRadius: BorderRadius.circular(4),
                            ),
                            child: Text(
                              f,
                              style: TextStyle(
                                fontSize: 10.5,
                                fontFamily: 'monospace',
                                color: isDark ? const Color(0xFF94A3B8) : const Color(0xFF475569),
                              ),
                            ),
                          );
                        }).toList(),
                      ),
                    const SizedBox(height: AppTokens.space10),
                  ],

                  // Active Workers & Crawlers
                  if (act.workers.isNotEmpty) ...[
                    Text(
                      'WORKERS & CRAWLERS',
                      style: TextStyle(
                        fontSize: 10,
                        fontWeight: FontWeight.bold,
                        letterSpacing: 0.5,
                        color: isDark ? const Color(0xFF64748B) : const Color(0xFF94A3B8),
                      ),
                    ),
                    const SizedBox(height: 4),
                    ...act.workers.map((w) => _buildWorkerRow(w, isDark)),
                  ],
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildPill(String text, bool isDark) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: isDark ? const Color(0xFF1E2433) : const Color(0xFFE2E8F0),
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(
        text,
        style: TextStyle(
          fontSize: 10.5,
          fontFamily: 'monospace',
          color: isDark ? const Color(0xFF94A3B8) : const Color(0xFF475569),
        ),
      ),
    );
  }

  Widget _buildStatBadge(String label, String value, bool isDark) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: isDark ? const Color(0xFF1E2433) : const Color(0xFFEDF2F7),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: isDark ? const Color(0xFF2D3748) : const Color(0xFFE2E8F0)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            '$label: ',
            style: TextStyle(fontSize: 10.5, color: isDark ? const Color(0xFF94A3B8) : const Color(0xFF64748B)),
          ),
          Text(
            value,
            style: TextStyle(
              fontSize: 10.5,
              fontWeight: FontWeight.bold,
              color: isDark ? const Color(0xFF38BDF8) : const Color(0xFF0284C7),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildCommandRow(CommandLineItem cmd, bool isDark) {
    return Container(
      margin: const EdgeInsets.only(bottom: 3),
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: isDark ? const Color(0xFF0D1117) : const Color(0xFFF1F5F9),
        borderRadius: BorderRadius.circular(4),
      ),
      child: Row(
        children: [
          Text(
            '\$',
            style: TextStyle(
              fontSize: 11,
              fontFamily: 'monospace',
              color: isDark ? const Color(0xFF38BDF8) : const Color(0xFF0284C7),
              fontWeight: FontWeight.bold,
            ),
          ),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              cmd.command,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                fontSize: 11,
                fontFamily: 'monospace',
                color: isDark ? const Color(0xFFE2E8F0) : const Color(0xFF1E293B),
              ),
            ),
          ),
          if (cmd.isRunning)
            const SizedBox(
              width: 8,
              height: 8,
              child: CircularProgressIndicator(strokeWidth: 1.5),
            )
          else if (cmd.exitCode != null)
            Text(
              cmd.exitCode == 0 ? '✓ 0' : 'exit ${cmd.exitCode}',
              style: TextStyle(
                fontSize: 10,
                fontFamily: 'monospace',
                color: cmd.exitCode == 0 ? const Color(0xFF34D399) : const Color(0xFFEF4444),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildFileChangeRow(FileActivityItem item, bool isDark) {
    final isAdd = item.operation == FileOperationType.created;
    final isMod = item.operation == FileOperationType.modified;
    final isDel = item.operation == FileOperationType.deleted;

    final tag = isAdd ? 'A' : (isMod ? 'M' : (isDel ? 'D' : 'R'));
    final tagColor = isAdd
        ? const Color(0xFF34D399)
        : (isMod ? const Color(0xFFFBBF24) : (isDel ? const Color(0xFFEF4444) : const Color(0xFF94A3B8)));

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
            decoration: BoxDecoration(
              color: tagColor.withOpacity(0.15),
              borderRadius: BorderRadius.circular(3),
            ),
            child: Text(
              tag,
              style: TextStyle(fontSize: 10, fontWeight: FontWeight.bold, color: tagColor, fontFamily: 'monospace'),
            ),
          ),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              item.path,
              style: TextStyle(
                fontSize: 11,
                fontFamily: 'monospace',
                color: isDark ? const Color(0xFFE2E8F0) : const Color(0xFF1E293B),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildWorkerRow(WorkerActivityItem worker, bool isDark) {
    final isRunning = worker.status == 'RUNNING';
    final isDone = worker.status == 'COMPLETED';

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2.5),
      child: Row(
        children: [
          Container(
            width: 6,
            height: 6,
            decoration: BoxDecoration(
              color: isRunning
                  ? const Color(0xFF38BDF8)
                  : (isDone ? const Color(0xFF34D399) : const Color(0xFF94A3B8)),
              shape: BoxShape.circle,
            ),
          ),
          const SizedBox(width: 6),
          Text(
            '${worker.name} (${worker.role})',
            style: TextStyle(
              fontSize: 11,
              fontWeight: FontWeight.w600,
              color: isDark ? const Color(0xFFE2E8F0) : const Color(0xFF1E293B),
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              worker.currentAction.isNotEmpty ? worker.currentAction : worker.status,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                fontSize: 10.5,
                color: isDark ? const Color(0xFF94A3B8) : const Color(0xFF64748B),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
