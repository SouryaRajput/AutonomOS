import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../models/evidence.dart';
import '../widgets/custom_card.dart';
import '../widgets/empty_state.dart';
import '../widgets/status_badge.dart';

class EvidenceViewer extends StatelessWidget {
  final List<EvidenceModel> evidenceList;
  final List<EvidenceTraceItem> traces;
  final String? taskTitle;

  const EvidenceViewer({
    super.key,
    required this.evidenceList,
    this.traces = const [],
    this.taskTitle,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    if (evidenceList.isEmpty && traces.isEmpty) {
      return EmptyState(
        icon: Icons.fact_check_outlined,
        title: 'No Authoritative Evidence Recorded',
        message: 'Evidence is generated deterministically during task execution and test verification.',
      );
    }

    return SingleChildScrollView(
      padding: const EdgeInsets.all(AppTokens.space16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (taskTitle != null) ...[
            Text('Evidence Trace: $taskTitle', style: theme.textTheme.titleLarge),
            const SizedBox(height: AppTokens.space4),
            Text(
              'Stage 7 deterministic verification linkages across requirements, code, and test outputs.',
              style: theme.textTheme.bodyMedium,
            ),
            const SizedBox(height: AppTokens.space20),
          ],

          // Trace Matrix (Requirement -> Worker -> Check -> Status -> Evidence)
          if (traces.isNotEmpty) ...[
            Text('Requirement Traceability Matrix', style: theme.textTheme.titleMedium),
            const SizedBox(height: AppTokens.space12),
            ListView.separated(
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              itemCount: traces.length,
              separatorBuilder: (_, __) => const SizedBox(height: AppTokens.space8),
              itemBuilder: (context, index) {
                final trace = traces[index];
                return CustomCard(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          const Icon(Icons.rule, size: 16, color: AppTokens.brandPrimaryLight),
                          const SizedBox(width: AppTokens.space8),
                          Expanded(
                            child: Text(
                              trace.requirement,
                              style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13),
                            ),
                          ),
                          StatusBadge(status: trace.checkStatus, isSmall: true),
                        ],
                      ),
                      const SizedBox(height: AppTokens.space8),
                      Text(trace.checkDescription, style: theme.textTheme.bodyMedium),
                      const SizedBox(height: AppTokens.space8),
                      Container(
                        padding: const EdgeInsets.all(AppTokens.space8),
                        decoration: BoxDecoration(
                          color: isDark ? AppTokens.darkSurface : AppTokens.lightBorder.withOpacity(0.4),
                          borderRadius: AppTokens.borderRadiusSm,
                        ),
                        child: Row(
                          children: [
                            const Icon(Icons.terminal, size: 14, color: AppTokens.darkTextMuted),
                            const SizedBox(width: 6.0),
                            Text('${trace.workerName} • ${trace.checkType}: ', style: theme.textTheme.labelSmall),
                            Expanded(
                              child: Text(
                                trace.evidenceSnippet,
                                style: const TextStyle(fontFamily: 'monospace', fontSize: 11),
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                );
              },
            ),
            const SizedBox(height: AppTokens.space24),
          ],

          // Raw Evidence Records
          Text('Deterministic Evidence Records (${evidenceList.length})', style: theme.textTheme.titleMedium),
          const SizedBox(height: AppTokens.space12),
          ListView.separated(
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            itemCount: evidenceList.length,
            separatorBuilder: (_, __) => const SizedBox(height: AppTokens.space8),
            itemBuilder: (context, index) {
              final ev = evidenceList[index];
              return CustomCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        const Icon(Icons.vpn_key_outlined, size: 14, color: AppTokens.info),
                        const SizedBox(width: 6.0),
                        Text(
                          ev.evidenceType,
                          style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 12, color: AppTokens.info),
                        ),
                        const Spacer(),
                        Text(
                          ev.createdAt.length > 10 ? ev.createdAt.substring(11, 19) : '',
                          style: theme.textTheme.labelSmall,
                        ),
                      ],
                    ),
                    const SizedBox(height: AppTokens.space8),
                    Container(
                      width: double.infinity,
                      padding: const EdgeInsets.all(AppTokens.space12),
                      decoration: BoxDecoration(
                        color: isDark ? const Color(0xFF0F1117) : const Color(0xFFF1F5F9),
                        borderRadius: AppTokens.borderRadiusSm,
                      ),
                      child: SelectableText(
                        ev.data,
                        style: const TextStyle(
                          fontFamily: 'monospace',
                          fontSize: 12,
                          height: 1.4,
                        ),
                      ),
                    ),
                  ],
                ),
              );
            },
          ),
        ],
      ),
    );
  }
}
