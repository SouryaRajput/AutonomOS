import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../models/manager_activity.dart';
import 'activity_drawer.dart';
import 'compact_status_bar.dart';
import 'dynamic_orchestration_graph.dart';
import 'manager_core_visualizer.dart';
import 'streaming_manager_narrative.dart';

/// Living Manager Session View.
/// The redesigned primary autonomous orchestration experience.
/// Presents a living Manager Core, topological workforce graph, progressive reasoning narrative, and deep-dive task drawer.
class LivingManagerSessionView extends StatefulWidget {
  final List<ManagerActivityStep> steps;
  final String? summaryText;
  final String projectName;

  const LivingManagerSessionView({
    super.key,
    required this.steps,
    this.summaryText,
    this.projectName = 'Current Project',
  });

  @override
  State<LivingManagerSessionView> createState() => _LivingManagerSessionViewState();
}

class _LivingManagerSessionViewState extends State<LivingManagerSessionView> {
  void _openTaskDrawer(List<Map<String, dynamic>> tasks) {
    TaskInspectionDrawer.show(
      context,
      tasks: tasks,
      projectName: widget.projectName,
    );
  }

  ManagerCoreState _inferManagerCoreState() {
    if (widget.steps.isEmpty) return ManagerCoreState.interpreting;

    for (final step in widget.steps.reversed) {
      final title = step.title.toLowerCase();
      if (title.contains('paused') || title.contains('inactive') || title.contains('waiting')) {
        return ManagerCoreState.waiting;
      }
      if (title.contains('verif') || title.contains('check')) {
        return ManagerCoreState.verifying;
      }
      if (title.contains('plan') || title.contains('decompos')) {
        return ManagerCoreState.planning;
      }
      if (title.contains('dispatch') || title.contains('assign') || title.contains('coordinat')) {
        return ManagerCoreState.coordinating;
      }
      if (title.contains('execut') || title.contains('run')) {
        return ManagerCoreState.executing;
      }
      if (title.contains('understand') || title.contains('inspect')) {
        return ManagerCoreState.interpreting;
      }
    }

    return ManagerCoreState.waiting;
  }

  List<NarrativePhase> _buildNarrativePhases() {
    final phases = <NarrativePhase>[];

    for (int i = 0; i < widget.steps.length; i++) {
      final step = widget.steps[i];
      final isLatest = i == widget.steps.length - 1;

      String body = step.reason ?? step.decision ?? '';
      if (step.tasks.isNotEmpty && body.isEmpty) {
        body = '${step.tasks.length} task contracts formulated with dependency resolution.';
      } else if (step.selectedContext.isNotEmpty && body.isEmpty) {
        body = '${step.selectedContext.length} contextual files inspected in workspace.';
      }

      phases.add(NarrativePhase(
        title: step.title,
        body: body,
        isLatest: isLatest,
      ));
    }

    return phases;
  }

  List<Map<String, dynamic>> _extractAllTasks() {
    final allTasks = <Map<String, dynamic>>[];
    for (final step in widget.steps) {
      allTasks.addAll(step.tasks);
    }
    return allTasks;
  }

  List<String> _extractSubsystems() {
    final subsystems = <String>{};
    for (final step in widget.steps) {
      for (final task in step.tasks) {
        final title = task['title'] as String? ?? '';
        if (title.contains('3D') || title.contains('Scene') || title.contains('Hero')) {
          subsystems.add('3D Experience');
        } else if (title.contains('Route') || title.contains('Nav') || title.contains('Page')) {
          subsystems.add('Navigation & Routes');
        } else if (title.contains('Component') || title.contains('UI')) {
          subsystems.add('UI Components');
        } else if (title.contains('Test') || title.contains('Verif')) {
          subsystems.add('Verification');
        }
      }
    }
    if (subsystems.isEmpty) {
      return ['Visual Experience', 'UI Integration', 'Verification'];
    }
    return subsystems.toList();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final coreState = _inferManagerCoreState();
    final phases = _buildNarrativePhases();
    final allTasks = _extractAllTasks();
    final subsystems = _extractSubsystems();

    return Container(
      margin: const EdgeInsets.symmetric(vertical: AppTokens.space12),
      decoration: BoxDecoration(
        color: isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
        borderRadius: AppTokens.borderRadiusLg,
        border: Border.all(
          color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // 1. Centerpiece: Living Manager Core Visualizer
          Padding(
            padding: const EdgeInsets.fromLTRB(AppTokens.space20, AppTokens.space24, AppTokens.space20, AppTokens.space16),
            child: ManagerCoreVisualizer(
              state: coreState,
              subtitle: widget.summaryText,
            ),
          ),

          Divider(height: 1, color: isDark ? AppTokens.darkBorder.withOpacity(0.5) : AppTokens.lightBorder.withOpacity(0.5)),

          // 2. Center Stage: Progressive Streaming Narrative
          Padding(
            padding: const EdgeInsets.all(AppTokens.space20),
            child: StreamingManagerNarrative(
              phases: phases,
            ),
          ),

          // 3. Dynamic Orchestration Topology Graph
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: AppTokens.space20),
            child: DynamicOrchestrationGraph(
              subsystems: subsystems,
              onNodeTap: (nodeId) {
                if (allTasks.isNotEmpty) {
                  _openTaskDrawer(allTasks);
                }
              },
            ),
          ),

          const SizedBox(height: AppTokens.space12),

          // 4. Minimal Compact Status Strip
          Padding(
            padding: const EdgeInsets.fromLTRB(AppTokens.space20, 0, AppTokens.space20, AppTokens.space16),
            child: CompactStatusBar(
              projectName: widget.projectName,
              stateName: coreState == ManagerCoreState.waiting ? 'Waiting for Workers' : 'Planning',
              activeWorkers: 0,
              plannedTasks: allTasks.length,
              onInspectTasks: () => _openTaskDrawer(allTasks),
            ),
          ),
        ],
      ),
    );
  }
}
