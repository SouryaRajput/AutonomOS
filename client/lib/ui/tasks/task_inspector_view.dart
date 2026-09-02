import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../models/artifact.dart';
import '../../models/evidence.dart';
import '../../models/task.dart';
import '../observability/evidence_viewer.dart';
import '../widgets/custom_card.dart';
import '../widgets/status_badge.dart';

class TaskInspectorView extends StatefulWidget {
  final TaskItem task;
  final List<ArtifactModel> artifacts;
  final List<EvidenceModel> evidenceList;
  final VoidCallback? onBack;

  const TaskInspectorView({
    super.key,
    required this.task,
    this.artifacts = const [],
    this.evidenceList = const [],
    this.onBack,
  });

  @override
  State<TaskInspectorView> createState() => _TaskInspectorViewState();
}

class _TaskInspectorViewState extends State<TaskInspectorView> {
  bool _showAdvancedDetails = false;
  int _activeTabIndex = 0;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final task = widget.task;

    return SingleChildScrollView(
      padding: const EdgeInsets.all(AppTokens.space24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Navigation Back & Header
          Row(
            children: [
              if (widget.onBack != null) ...[
                IconButton(
                  icon: const Icon(Icons.arrow_back, size: 20),
                  onPressed: widget.onBack,
                ),
                const SizedBox(width: AppTokens.space8),
              ],
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(task.title, style: theme.textTheme.headlineMedium),
                    const SizedBox(height: 2),
                    Text('Task ID: ${task.id}', style: theme.textTheme.labelSmall),
                  ],
                ),
              ),
              StatusBadge(status: task.status),
            ],
          ),
          const SizedBox(height: AppTokens.space24),

          // Primary Summary Card (Always Visible)
          CustomCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Task Objective', style: theme.textTheme.titleMedium),
                const SizedBox(height: AppTokens.space8),
                Text(
                  task.objective.isNotEmpty ? task.objective : 'No detailed objective description provided.',
                  style: theme.textTheme.bodyLarge,
                ),
                const SizedBox(height: AppTokens.space16),
                Row(
                  children: [
                    _buildDetailChip('Worker', task.assignedWorkerName ?? task.assignedWorker ?? 'Unassigned', Icons.person_outline),
                    const SizedBox(width: AppTokens.space12),
                    _buildDetailChip('Risk Level', task.risk, Icons.shield_outlined),
                    const SizedBox(width: AppTokens.space12),
                    _buildDetailChip('Attempts', '${task.attempts} / ${task.maxAttempts}', Icons.replay),
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(height: AppTokens.space16),

          // Progressive Disclosure Toggle
          InkWell(
            onTap: () => setState(() => _showAdvancedDetails = !_showAdvancedDetails),
            borderRadius: AppTokens.borderRadiusSm,
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: AppTokens.space8),
              child: Row(
                children: [
                  Icon(
                    _showAdvancedDetails ? Icons.expand_less : Icons.expand_more,
                    size: 20,
                    color: AppTokens.brandPrimaryLight,
                  ),
                  const SizedBox(width: 6.0),
                  Text(
                    _showAdvancedDetails ? 'Hide Deep Observability Details' : 'Show Deep Observability Details',
                    style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13, color: AppTokens.brandPrimaryLight),
                  ),
                ],
              ),
            ),
          ),

          // Advanced Deep Observability Section
          if (_showAdvancedDetails) ...[
            const SizedBox(height: AppTokens.space12),

            // Tab Buttons
            Row(
              children: [
                _buildTabButton('Artifacts (${widget.artifacts.length})', 0),
                const SizedBox(width: AppTokens.space8),
                _buildTabButton('Deterministic Evidence (${widget.evidenceList.length})', 1),
                const SizedBox(width: AppTokens.space8),
                _buildTabButton('Dependencies (${task.dependencies.length})', 2),
              ],
            ),
            const SizedBox(height: AppTokens.space16),

            // Tab 0: Artifacts
            if (_activeTabIndex == 0)
              widget.artifacts.isEmpty
                  ? const Text('No artifacts generated by this task.')
                  : ListView.separated(
                      shrinkWrap: true,
                      physics: const NeverScrollableScrollPhysics(),
                      itemCount: widget.artifacts.length,
                      separatorBuilder: (_, __) => const SizedBox(height: AppTokens.space8),
                      itemBuilder: (context, index) {
                        final a = widget.artifacts[index];
                        return CustomCard(
                          child: Row(
                            children: [
                              const Icon(Icons.insert_drive_file_outlined, size: 16, color: AppTokens.brandPrimaryLight),
                              const SizedBox(width: AppTokens.space12),
                              Expanded(
                                child: Text(a.path, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w500)),
                              ),
                              Text(a.kind.value, style: theme.textTheme.labelSmall),
                            ],
                          ),
                        );
                      },
                    ),

            // Tab 1: Evidence Viewer
            if (_activeTabIndex == 1)
              EvidenceViewer(
                evidenceList: widget.evidenceList,
                taskTitle: task.title,
              ),

            // Tab 2: Dependencies
            if (_activeTabIndex == 2)
              task.dependencies.isEmpty
                  ? const Text('No prerequisite task dependencies.')
                  : ListView.separated(
                      shrinkWrap: true,
                      physics: const NeverScrollableScrollPhysics(),
                      itemCount: task.dependencies.length,
                      separatorBuilder: (_, __) => const SizedBox(height: AppTokens.space8),
                      itemBuilder: (context, index) {
                        final dep = task.dependencies[index];
                        return CustomCard(
                          child: Row(
                            children: [
                              const Icon(Icons.link, size: 16, color: AppTokens.info),
                              const SizedBox(width: AppTokens.space12),
                              Text('Prerequisite Task: $dep', style: const TextStyle(fontSize: 13)),
                            ],
                          ),
                        );
                      },
                    ),
          ],
        ],
      ),
    );
  }

  Widget _buildDetailChip(String label, String value, IconData icon) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: AppTokens.space12, vertical: 6.0),
      decoration: BoxDecoration(
        color: Theme.of(context).brightness == Brightness.dark ? AppTokens.darkSurface : AppTokens.lightBorder.withOpacity(0.4),
        borderRadius: AppTokens.borderRadiusSm,
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 14, color: AppTokens.darkTextMuted),
          const SizedBox(width: 6.0),
          Text('$label: ', style: const TextStyle(fontSize: 12, color: AppTokens.darkTextMuted)),
          Text(value, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }

  Widget _buildTabButton(String label, int index) {
    final isSelected = _activeTabIndex == index;
    return ChoiceChip(
      label: Text(label, style: TextStyle(fontSize: 12, fontWeight: isSelected ? FontWeight.w600 : FontWeight.normal)),
      selected: isSelected,
      onSelected: (_) => setState(() => _activeTabIndex = index),
      selectedColor: AppTokens.brandPrimary.withOpacity(0.2),
      side: BorderSide(
        color: isSelected ? AppTokens.brandPrimaryLight : Theme.of(context).dividerColor,
      ),
    );
  }
}
