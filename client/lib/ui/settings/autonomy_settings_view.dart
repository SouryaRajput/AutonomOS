import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../models/policy.dart';
import '../../state/app_state.dart';
import '../widgets/custom_card.dart';

class AutonomySettingsView extends StatefulWidget {
  final AppState appState;

  const AutonomySettingsView({super.key, required this.appState});

  @override
  State<AutonomySettingsView> createState() => _AutonomySettingsViewState();
}

class _AutonomySettingsViewState extends State<AutonomySettingsView> {
  AutonomyPolicyModel? _policy;
  bool _isLoading = false;
  bool _showAdvanced = false;

  String _selectedLevel = 'BALANCED';
  double _maxCost = 10.0;
  int _maxIterations = 15;
  bool _allowDestructive = false;
  bool _allowExternal = true;

  @override
  void initState() {
    super.initState();
    _loadPolicy();
  }

  Future<void> _loadPolicy() async {
    final proj = widget.appState.selectedProject;
    if (proj == null) return;
    setState(() => _isLoading = true);
    try {
      final pol = await widget.appState.apiClient.getPolicy(proj.id);
      if (mounted) {
        setState(() {
          _policy = pol;
          _selectedLevel = pol.autonomyLevel;
          _maxCost = pol.maxCostLimit;
          _maxIterations = pol.maxIterations;
          _isLoading = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  Future<void> _savePolicy(String level) async {
    final proj = widget.appState.selectedProject;
    if (proj == null) return;
    setState(() {
      _selectedLevel = level;
      _isLoading = true;
    });
    try {
      final updated = await widget.appState.apiClient.updatePolicy(
        proj.id,
        autonomyLevel: level,
      );
      if (mounted) {
        setState(() {
          _policy = updated;
          _isLoading = false;
        });
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Autonomy level updated to $level')),
        );
      }
    } catch (e) {
      if (mounted) {
        setState(() => _isLoading = false);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed to update policy: $e')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    if (_isLoading && _policy == null) {
      return const Center(child: CircularProgressIndicator());
    }

    return SingleChildScrollView(
      padding: const EdgeInsets.all(AppTokens.space24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Autonomy & Policy Control', style: theme.textTheme.headlineMedium),
          const SizedBox(height: AppTokens.space4),
          Text(
            'Control the authority, tool permissions, and risk thresholds of your autonomous workforce',
            style: theme.textTheme.bodyMedium,
          ),
          const SizedBox(height: AppTokens.space24),

          // Primary 3-Level Selector
          Text('Workforce Autonomy Level', style: theme.textTheme.titleMedium),
          const SizedBox(height: AppTokens.space12),

          _buildAutonomyLevelCard(
            context,
            level: 'SUPERVISED',
            title: 'Supervised Autonomy',
            badgeText: 'Highest Safety',
            badgeColor: AppTokens.info,
            description: 'Every consequential tool execution, file modification, and external API call requires explicit operator approval before proceeding.',
            isSelected: _selectedLevel == 'SUPERVISED',
          ),
          const SizedBox(height: AppTokens.space12),

          _buildAutonomyLevelCard(
            context,
            level: 'BALANCED',
            title: 'Balanced Autonomy (Recommended)',
            badgeText: 'Default',
            badgeColor: AppTokens.brandPrimaryLight,
            description: 'Read-only inspections, test runs, and diagnostic steps run autonomously. Destructive edits, large file modifications, and external actions pause for operator approval.',
            isSelected: _selectedLevel == 'BALANCED',
          ),
          const SizedBox(height: AppTokens.space12),

          _buildAutonomyLevelCard(
            context,
            level: 'AUTONOMOUS',
            title: 'High Autonomy',
            badgeText: 'Max Speed',
            badgeColor: AppTokens.warning,
            description: 'The workforce executes end-to-end tasks, edits code, and resolves defects autonomously. Pauses only on critical safety deviations or catastrophic test failures.',
            isSelected: _selectedLevel == 'AUTONOMOUS' || _selectedLevel == 'UNATTENDED',
          ),
          const SizedBox(height: AppTokens.space24),

          // Policy Explanation Example Box
          CustomCard(
            backgroundColor: isDark ? AppTokens.darkSurface : AppTokens.lightBorder.withOpacity(0.3),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Icon(Icons.info_outline, size: 16, color: AppTokens.brandPrimaryLight),
                    const SizedBox(width: AppTokens.space8),
                    Text('How Policy Enforcement Works', style: theme.textTheme.titleSmall),
                  ],
                ),
                const SizedBox(height: AppTokens.space8),
                Text(
                  _getPolicyExplanationText(_selectedLevel),
                  style: theme.textTheme.bodyMedium?.copyWith(height: 1.4),
                ),
              ],
            ),
          ),
          const SizedBox(height: AppTokens.space24),

          // Advanced Autonomy Controls Toggle
          InkWell(
            onTap: () => setState(() => _showAdvanced = !_showAdvanced),
            borderRadius: AppTokens.borderRadiusSm,
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: AppTokens.space8),
              child: Row(
                children: [
                  Icon(_showAdvanced ? Icons.expand_less : Icons.expand_more, size: 20, color: AppTokens.brandPrimaryLight),
                  const SizedBox(width: 6.0),
                  Text(
                    _showAdvanced ? 'Hide Advanced Autonomy Thresholds' : 'Show Advanced Autonomy Thresholds',
                    style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13, color: AppTokens.brandPrimaryLight),
                  ),
                ],
              ),
            ),
          ),

          if (_showAdvanced) ...[
            const SizedBox(height: AppTokens.space16),
            CustomCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Resource & Safety Limits', style: theme.textTheme.titleMedium),
                  const SizedBox(height: AppTokens.space16),

                  // Max Cost Limit Slider
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      const Text('Maximum Project Budget Limit'),
                      Text('\$${_maxCost.toStringAsFixed(2)}', style: const TextStyle(fontWeight: FontWeight.bold)),
                    ],
                  ),
                  Slider(
                    value: _maxCost,
                    min: 1.0,
                    max: 50.0,
                    divisions: 49,
                    label: '\$${_maxCost.toStringAsFixed(0)}',
                    onChanged: (val) => setState(() => _maxCost = val),
                  ),
                  const SizedBox(height: AppTokens.space12),

                  // Max Orchestration Cycles
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      const Text('Max Manager Orchestration Cycles'),
                      Text('$_maxIterations cycles', style: const TextStyle(fontWeight: FontWeight.bold)),
                    ],
                  ),
                  Slider(
                    value: _maxIterations.toDouble(),
                    min: 5.0,
                    max: 50.0,
                    divisions: 45,
                    label: '$_maxIterations',
                    onChanged: (val) => setState(() => _maxIterations = val.toInt()),
                  ),
                  const SizedBox(height: AppTokens.space12),

                  // Switch for Destructive File Operations
                  SwitchListTile(
                    title: const Text('Permit Direct File Deletions (rm)'),
                    subtitle: const Text('When disabled, all file deletion requests require operator authorization.'),
                    value: _allowDestructive,
                    onChanged: (val) => setState(() => _allowDestructive = val),
                    contentPadding: EdgeInsets.zero,
                  ),

                  // Switch for External Network Calls
                  SwitchListTile(
                    title: const Text('Permit External Network Calls (http/api)'),
                    subtitle: const Text('When disabled, external web search and remote API calls require authorization.'),
                    value: _allowExternal,
                    onChanged: (val) => setState(() => _allowExternal = val),
                    contentPadding: EdgeInsets.zero,
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildAutonomyLevelCard(
    BuildContext context, {
    required String level,
    required String title,
    required String badgeText,
    required Color badgeColor,
    required String description,
    required bool isSelected,
  }) {
    final theme = Theme.of(context);
    return CustomCard(
      borderColor: isSelected ? AppTokens.brandPrimaryLight : null,
      backgroundColor: isSelected ? AppTokens.brandPrimary.withOpacity(0.08) : null,
      onTap: () => _savePolicy(level),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Radio<String>(
                value: level,
                groupValue: _selectedLevel,
                onChanged: (val) => _savePolicy(val!),
              ),
              const SizedBox(width: AppTokens.space8),
              Expanded(
                child: Text(
                  title,
                  style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.bold),
                ),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: badgeColor.withOpacity(0.15),
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Text(
                  badgeText,
                  style: TextStyle(color: badgeColor, fontSize: 11, fontWeight: FontWeight.bold),
                ),
              ),
            ],
          ),
          Padding(
            padding: const EdgeInsets.only(left: 48, top: 4, right: 12),
            child: Text(description, style: theme.textTheme.bodyMedium),
          ),
        ],
      ),
    );
  }

  String _getPolicyExplanationText(String level) {
    switch (level) {
      case 'SUPERVISED':
        return 'In Supervised mode, the runtime intercepts any action classified as Medium, High, or Critical risk. The operator is prompted via the Approval Center with exact diffs, tool parameters, and justifications before anything is executed.';
      case 'HIGH':
      case 'AUTONOMOUS':
        return 'In High Autonomy mode, the workforce is permitted to modify codebase files, run unit and integration tests, and record verified artifacts independently. Only critical policy invariants (such as scope escape or emergency halt) pause execution.';
      case 'BALANCED':
      default:
        return 'In Balanced mode, benign diagnostic tools and read-only searches run without interruption. Code modifications generate a structured patch that can be reviewed, and external side-effects trigger approval requests with clear risk categorization.';
    }
  }
}
