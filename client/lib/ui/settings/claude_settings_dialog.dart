import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../state/app_state.dart';
import '../../services/token_storage.dart';
import '../chat/token_usage_dialog.dart';

/// Centered Multi-Tab Settings Modal with Custom Providers, Token Usage, Permissions, and Safety.
class ClaudeSettingsDialog extends StatefulWidget {
  final AppState appState;

  const ClaudeSettingsDialog({super.key, required this.appState});

  @override
  State<ClaudeSettingsDialog> createState() => _ClaudeSettingsDialogState();
}

class _ClaudeSettingsDialogState extends State<ClaudeSettingsDialog> {
  int _selectedTab = 0;

  // New Provider Form Controllers
  final _nameController = TextEditingController();
  final _urlController = TextEditingController();
  final _keyController = TextEditingController();
  final _modelController = TextEditingController();
  bool _isAddingProvider = false;
  bool _hideNewKey = true;

  // Inline editing state for existing providers
  String? _editingProviderId;
  final _editNameController = TextEditingController();
  final _editKeyController = TextEditingController();
  final _editUrlController = TextEditingController();
  final _editModelController = TextEditingController();
  bool _hideEditKey = true;

  // Permission toggles
  bool _allowFileEdits = true;
  bool _allowTerminalCommands = true;
  bool _allowNetworkCalls = false;

  @override
  void dispose() {
    _nameController.dispose();
    _urlController.dispose();
    _keyController.dispose();
    _modelController.dispose();
    _editNameController.dispose();
    _editKeyController.dispose();
    _editUrlController.dispose();
    _editModelController.dispose();
    super.dispose();
  }

  void _startEditingProvider(Map<String, dynamic> prov) {
    setState(() {
      _editingProviderId = prov['id'];
      _editNameController.text = prov['name'] ?? '';
      _editKeyController.text = prov['apiKey'] ?? '';
      _editUrlController.text = prov['baseUrl'] ?? '';
      _editModelController.text = prov['model'] ?? '';
      _hideEditKey = true;
    });
  }

  void _saveEditingProvider(String providerId) {
    widget.appState.updateProvider(
      providerId,
      name: _editNameController.text.trim(),
      apiKey: _editKeyController.text.trim(),
      baseUrl: _editUrlController.text.trim(),
      model: _editModelController.text.trim(),
    );
    setState(() => _editingProviderId = null);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    final tabs = [
      {'icon': Icons.hub_outlined, 'label': 'Inference Providers'},
      {'icon': Icons.analytics_outlined, 'label': 'Token Usage'},
      {'icon': Icons.shield_outlined, 'label': 'Permissions'},
      {'icon': Icons.storage_outlined, 'label': 'Safety & Storage'},
    ];

    return Dialog(
      backgroundColor: Colors.transparent,
      insetPadding: const EdgeInsets.symmetric(horizontal: 24, vertical: 36),
      child: Container(
        width: 820,
        height: 600,
        decoration: BoxDecoration(
          color: isDark ? AppTokens.darkSurface : AppTokens.lightSurface,
          borderRadius: AppTokens.borderRadiusLg,
          border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withOpacity(0.4),
              blurRadius: 32,
              offset: const Offset(0, 12),
            ),
          ],
        ),
        child: Column(
          children: [
            // Modal Header
            Container(
              padding: const EdgeInsets.symmetric(horizontal: AppTokens.space20, vertical: AppTokens.space14),
              decoration: BoxDecoration(
                border: Border(
                  bottom: BorderSide(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
                ),
              ),
              child: Row(
                children: [
                  const Icon(Icons.settings_outlined, size: 20, color: AppTokens.brandPrimary),
                  const SizedBox(width: AppTokens.space10),
                  Text('AutonomOS Settings', style: theme.textTheme.titleMedium),
                  const Spacer(),
                  IconButton(
                    icon: const Icon(Icons.close, size: 18),
                    onPressed: () => Navigator.of(context).pop(),
                  ),
                ],
              ),
            ),

            // Main Content Area: Left Tabs + Right Pane
            Expanded(
              child: Row(
                children: [
                  // Left Tab Navigation
                  Container(
                    width: 200,
                    decoration: BoxDecoration(
                      color: isDark ? AppTokens.darkSidebar : AppTokens.lightSidebar,
                      border: Border(
                        right: BorderSide(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
                      ),
                    ),
                    child: ListView.builder(
                      padding: const EdgeInsets.symmetric(vertical: AppTokens.space8, horizontal: AppTokens.space6),
                      itemCount: tabs.length,
                      itemBuilder: (context, idx) {
                        final tab = tabs[idx];
                        final isSelected = _selectedTab == idx;
                        return Padding(
                          padding: const EdgeInsets.only(bottom: AppTokens.space4),
                          child: ListTile(
                            dense: true,
                            shape: const RoundedRectangleBorder(borderRadius: AppTokens.borderRadiusSm),
                            selected: isSelected,
                            selectedTileColor: isDark ? AppTokens.darkElevated : AppTokens.lightBorder,
                            leading: Icon(
                              tab['icon'] as IconData,
                              size: 16,
                              color: isSelected ? AppTokens.brandPrimary : (isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary),
                            ),
                            title: Text(
                              tab['label'] as String,
                              style: TextStyle(
                                fontSize: 13,
                                fontWeight: isSelected ? FontWeight.w600 : FontWeight.normal,
                                color: isSelected
                                    ? (isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary)
                                    : (isDark ? AppTokens.darkTextSecondary : AppTokens.lightTextSecondary),
                              ),
                            ),
                            onTap: () => setState(() => _selectedTab = idx),
                          ),
                        );
                      },
                    ),
                  ),

                  // Right Pane
                  Expanded(
                    child: Padding(
                      padding: const EdgeInsets.all(AppTokens.space20),
                      child: _buildRightPane(context),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildRightPane(BuildContext context) {
    switch (_selectedTab) {
      case 0:
        return _buildProvidersTab(context);
      case 1:
        return _buildTokenUsageTab(context);
      case 2:
        return _buildPermissionsTab(context);
      case 3:
        return _buildSafetyStorageTab(context);
      default:
        return const SizedBox();
    }
  }

  // --- 1. Custom Providers Tab ---
  Widget _buildProvidersTab(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final providers = widget.appState.customProviders;

    return SingleChildScrollView(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text('Inference Providers', style: theme.textTheme.titleMedium),
              const Spacer(),
              ElevatedButton.icon(
                style: ElevatedButton.styleFrom(
                  backgroundColor: AppTokens.brandPrimary,
                  foregroundColor: Colors.white,
                  padding: const EdgeInsets.symmetric(horizontal: AppTokens.space12, vertical: AppTokens.space8),
                  shape: const RoundedRectangleBorder(borderRadius: AppTokens.borderRadiusSm),
                ),
                icon: Icon(_isAddingProvider ? Icons.close : Icons.add, size: 14),
                label: Text(_isAddingProvider ? 'Cancel' : 'Add Inference Provider', style: const TextStyle(fontSize: 12)),
                onPressed: () => setState(() => _isAddingProvider = !_isAddingProvider),
              ),
            ],
          ),
          const SizedBox(height: AppTokens.space4),
          const Text(
            'Connect your custom OpenAI-compatible API endpoint (Base URL + API Key + Model ID). The system will use ONLY the selected provider.',
            style: TextStyle(fontSize: 12, color: AppTokens.darkTextMuted),
          ),
          const SizedBox(height: AppTokens.space16),

          // Add Custom Provider Form
          if (_isAddingProvider) ...[
            Container(
              padding: const EdgeInsets.all(AppTokens.space16),
              margin: const EdgeInsets.only(bottom: AppTokens.space16),
              decoration: BoxDecoration(
                color: isDark ? AppTokens.darkElevated : AppTokens.lightBorderMuted,
                borderRadius: AppTokens.borderRadiusMd,
                border: Border.all(color: AppTokens.brandPrimary.withOpacity(0.5)),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Add New Inference Provider', style: theme.textTheme.titleSmall),
                  const SizedBox(height: AppTokens.space12),
                  Row(
                    children: [
                      Expanded(
                        child: TextField(
                          controller: _nameController,
                          style: const TextStyle(fontSize: 13),
                          decoration: const InputDecoration(
                            labelText: 'Provider Name',
                            hintText: 'e.g. Kira, DeepSeek, My LLM',
                          ),
                        ),
                      ),
                      const SizedBox(width: AppTokens.space12),
                      Expanded(
                        child: TextField(
                          controller: _modelController,
                          style: const TextStyle(fontSize: 13, fontFamily: 'monospace'),
                          decoration: const InputDecoration(
                            labelText: 'Model Name / ID',
                            hintText: 'e.g. glm-5.3, llama-3.3, gpt-4o',
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: AppTokens.space12),
                  TextField(
                    controller: _urlController,
                    style: const TextStyle(fontSize: 13, fontFamily: 'monospace'),
                    decoration: const InputDecoration(
                      labelText: 'Base URL',
                      hintText: 'https://kiraai.vn/api/v1 or https://api.openai.com/v1',
                    ),
                  ),
                  const SizedBox(height: AppTokens.space12),
                  TextField(
                    controller: _keyController,
                    obscureText: _hideNewKey,
                    style: const TextStyle(fontSize: 13, fontFamily: 'monospace'),
                    decoration: InputDecoration(
                      labelText: 'API Key',
                      hintText: 'Paste your API key here',
                      suffixIcon: IconButton(
                        icon: Icon(_hideNewKey ? Icons.visibility_off : Icons.visibility, size: 16),
                        onPressed: () => setState(() => _hideNewKey = !_hideNewKey),
                      ),
                    ),
                  ),
                  const SizedBox(height: AppTokens.space16),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.end,
                    children: [
                      TextButton(
                        onPressed: () => setState(() => _isAddingProvider = false),
                        child: const Text('Cancel'),
                      ),
                      const SizedBox(width: AppTokens.space8),
                      ElevatedButton(
                        style: ElevatedButton.styleFrom(
                          backgroundColor: AppTokens.brandPrimary,
                          foregroundColor: Colors.white,
                          shape: const RoundedRectangleBorder(borderRadius: AppTokens.borderRadiusSm),
                        ),
                        onPressed: () {
                          if (_nameController.text.trim().isNotEmpty && _urlController.text.trim().isNotEmpty) {
                            widget.appState.addCustomProvider(
                              name: _nameController.text,
                              baseUrl: _urlController.text,
                              apiKey: _keyController.text,
                              model: _modelController.text,
                              isDefault: true,
                            );
                            _nameController.clear();
                            _urlController.clear();
                            _keyController.clear();
                            _modelController.clear();
                            setState(() => _isAddingProvider = false);
                          }
                        },
                        child: const Text('Save & Use This'),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ],

          // Empty State if no providers are added yet
          if (providers.isEmpty && !_isAddingProvider) ...[
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(AppTokens.space32),
              decoration: BoxDecoration(
                color: isDark ? AppTokens.darkElevated : AppTokens.lightSurface,
                borderRadius: AppTokens.borderRadiusMd,
                border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
              ),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Icon(Icons.hub_outlined, size: 40, color: AppTokens.brandPrimary),
                  const SizedBox(height: AppTokens.space12),
                  Text('No Inference Providers Added', style: theme.textTheme.titleMedium),
                  const SizedBox(height: AppTokens.space6),
                  const Text(
                    'Add your API endpoint (Base URL, API Key, and Model ID) to begin chatting with the autonomous workforce.',
                    textAlign: TextAlign.center,
                    style: TextStyle(fontSize: 12.5, color: AppTokens.darkTextMuted, height: 1.4),
                  ),
                  const SizedBox(height: AppTokens.space16),
                  ElevatedButton.icon(
                    style: ElevatedButton.styleFrom(
                      backgroundColor: AppTokens.brandPrimary,
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(horizontal: AppTokens.space16, vertical: AppTokens.space10),
                      shape: const RoundedRectangleBorder(borderRadius: AppTokens.borderRadiusSm),
                    ),
                    icon: const Icon(Icons.add, size: 16),
                    label: const Text('Add Inference Provider', style: TextStyle(fontSize: 13, fontWeight: FontWeight.bold)),
                    onPressed: () => setState(() => _isAddingProvider = true),
                  ),
                ],
              ),
            ),
          ],

          // Configured Providers List
          ...providers.map((prov) {
            final pid = prov['id'] as String;
            final isDefault = prov['isDefault'] == true;
            final isEditing = _editingProviderId == pid;
            final hasKey = (prov['apiKey'] as String? ?? '').isNotEmpty;

            return Container(
              margin: const EdgeInsets.only(bottom: AppTokens.space12),
              padding: const EdgeInsets.all(AppTokens.space14),
              decoration: BoxDecoration(
                color: isDark ? AppTokens.darkElevated : AppTokens.lightSurface,
                borderRadius: AppTokens.borderRadiusMd,
                border: Border.all(
                  color: isDefault
                      ? AppTokens.brandPrimary.withOpacity(0.8)
                      : (isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
                  width: isDefault ? 1.5 : 1.0,
                ),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // Provider Header Row
                  Row(
                    children: [
                      Container(
                        width: 8,
                        height: 8,
                        decoration: BoxDecoration(
                          color: isDefault ? AppTokens.success : AppTokens.darkTextMuted,
                          shape: BoxShape.circle,
                        ),
                      ),
                      const SizedBox(width: AppTokens.space10),
                      Text(prov['name'] ?? '', style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
                      const SizedBox(width: AppTokens.space8),
                      if (isDefault)
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                          decoration: BoxDecoration(
                            color: AppTokens.brandPrimary.withOpacity(0.15),
                            borderRadius: AppTokens.borderRadiusXs,
                          ),
                          child: const Text('In Use', style: TextStyle(fontSize: 11, color: AppTokens.brandPrimary, fontWeight: FontWeight.bold)),
                        ),
                      const Spacer(),
                      if (!isDefault)
                        ElevatedButton(
                          style: ElevatedButton.styleFrom(
                            backgroundColor: isDark ? AppTokens.darkSurface : AppTokens.lightBorder,
                            foregroundColor: isDark ? AppTokens.darkTextPrimary : AppTokens.lightTextPrimary,
                            elevation: 0,
                            padding: const EdgeInsets.symmetric(horizontal: AppTokens.space12, vertical: AppTokens.space6),
                            shape: const RoundedRectangleBorder(borderRadius: AppTokens.borderRadiusSm),
                          ),
                          onPressed: () => widget.appState.useProvider(pid),
                          child: const Text('Use this', style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold)),
                        ),
                      const SizedBox(width: AppTokens.space6),
                      IconButton(
                        icon: Icon(isEditing ? Icons.expand_less : Icons.edit_outlined, size: 16),
                        tooltip: isEditing ? 'Close' : 'Edit details',
                        onPressed: () {
                          if (isEditing) {
                            setState(() => _editingProviderId = null);
                          } else {
                            _startEditingProvider(prov);
                          }
                        },
                      ),
                      IconButton(
                        icon: const Icon(Icons.delete_outline, size: 16, color: AppTokens.danger),
                        tooltip: 'Delete provider',
                        onPressed: () => widget.appState.deleteProvider(pid),
                      ),
                    ],
                  ),

                  // Base URL & Model summary
                  Padding(
                    padding: const EdgeInsets.only(left: AppTokens.space18, top: 2),
                    child: Text(
                      '${prov['baseUrl']} • ${prov['model']}',
                      style: const TextStyle(fontSize: 11.5, fontFamily: 'monospace', color: AppTokens.darkTextMuted),
                    ),
                  ),

                  // Inline Editor Form when expanded
                  if (isEditing) ...[
                    const SizedBox(height: AppTokens.space14),
                    const Divider(height: 1),
                    const SizedBox(height: AppTokens.space12),

                    TextField(
                      controller: _editNameController,
                      style: const TextStyle(fontSize: 13),
                      decoration: const InputDecoration(labelText: 'Provider Name', isDense: true),
                    ),
                    const SizedBox(height: AppTokens.space10),
                    Row(
                      children: [
                        Expanded(
                          child: TextField(
                            controller: _editUrlController,
                            style: const TextStyle(fontSize: 12.5, fontFamily: 'monospace'),
                            decoration: const InputDecoration(labelText: 'Base URL', isDense: true),
                          ),
                        ),
                        const SizedBox(width: AppTokens.space10),
                        Expanded(
                          child: TextField(
                            controller: _editModelController,
                            style: const TextStyle(fontSize: 12.5, fontFamily: 'monospace'),
                            decoration: const InputDecoration(labelText: 'Model ID', isDense: true),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: AppTokens.space10),
                    TextField(
                      controller: _editKeyController,
                      obscureText: _hideEditKey,
                      style: const TextStyle(fontSize: 12.5, fontFamily: 'monospace'),
                      decoration: InputDecoration(
                        labelText: 'API Key',
                        hintText: 'Enter secret API key',
                        isDense: true,
                        suffixIcon: IconButton(
                          icon: Icon(_hideEditKey ? Icons.visibility_off : Icons.visibility, size: 16),
                          onPressed: () => setState(() => _hideEditKey = !_hideEditKey),
                        ),
                      ),
                    ),
                    const SizedBox(height: AppTokens.space12),
                    Align(
                      alignment: Alignment.centerRight,
                      child: ElevatedButton(
                        style: ElevatedButton.styleFrom(
                          backgroundColor: AppTokens.brandPrimary,
                          foregroundColor: Colors.white,
                          padding: const EdgeInsets.symmetric(horizontal: AppTokens.space14, vertical: AppTokens.space8),
                          shape: const RoundedRectangleBorder(borderRadius: AppTokens.borderRadiusSm),
                        ),
                        onPressed: () => _saveEditingProvider(pid),
                        child: const Text('Save Changes', style: TextStyle(fontSize: 12)),
                      ),
                    ),
                  ],
                ],
              ),
            );
          }),
        ],
      ),
    );
  }

  // --- 2. Token Usage Tab ---
  Widget _buildTokenUsageTab(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    final todayKey = TokenStorage.todayKey();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Token & Inference Telemetry', style: theme.textTheme.titleMedium),
        const SizedBox(height: AppTokens.space4),
        const Text(
          'Live token tracking across your active model and workforce cycles.',
          style: TextStyle(fontSize: 12, color: AppTokens.darkTextMuted),
        ),
        const SizedBox(height: AppTokens.space20),

        Text(
          "TODAY'S USAGE ($todayKey)",
          style: const TextStyle(fontSize: 11, fontWeight: FontWeight.bold, letterSpacing: 0.5, color: AppTokens.darkTextMuted),
        ),
        const SizedBox(height: AppTokens.space8),
        Row(
          children: [
            _buildTokenCard('Today Total', TokenUsageDialog.formatNumber(widget.appState.tokensToday), Icons.data_usage_rounded, isDark),
            const SizedBox(width: AppTokens.space12),
            _buildTokenCard('Today Input', TokenUsageDialog.formatNumber(widget.appState.promptTokensToday), Icons.arrow_downward, isDark),
            const SizedBox(width: AppTokens.space12),
            _buildTokenCard('Today Output', TokenUsageDialog.formatNumber(widget.appState.completionTokensToday), Icons.arrow_upward, isDark),
          ],
        ),
        const SizedBox(height: AppTokens.space20),

        const Text(
          'LIFETIME USAGE',
          style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, letterSpacing: 0.5, color: AppTokens.darkTextMuted),
        ),
        const SizedBox(height: AppTokens.space8),
        Row(
          children: [
            _buildTokenCard('Lifetime Total', TokenUsageDialog.formatNumber(widget.appState.lifetimeTokens), Icons.token_outlined, isDark),
            const SizedBox(width: AppTokens.space12),
            _buildTokenCard('Lifetime Input', TokenUsageDialog.formatNumber(widget.appState.lifetimePromptTokens), Icons.arrow_downward, isDark),
            const SizedBox(width: AppTokens.space12),
            _buildTokenCard('Lifetime Output', TokenUsageDialog.formatNumber(widget.appState.lifetimeCompletionTokens), Icons.arrow_upward, isDark),
          ],
        ),
        const SizedBox(height: AppTokens.space24),

        Container(
          padding: const EdgeInsets.all(AppTokens.space16),
          decoration: BoxDecoration(
            color: isDark ? AppTokens.darkElevated : AppTokens.lightBorderMuted,
            borderRadius: AppTokens.borderRadiusMd,
            border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  const Icon(Icons.info_outline, size: 16, color: AppTokens.brandPrimary),
                  const SizedBox(width: AppTokens.space8),
                  Text('Direct API Telemetry', style: theme.textTheme.titleSmall),
                ],
              ),
              const SizedBox(height: AppTokens.space8),
              const Text(
                'AutonomOS connects directly to your own configured inference API. No middleman servers or markups. All tokens are tracked locally.',
                style: TextStyle(fontSize: 12, height: 1.4),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildTokenCard(String title, String value, IconData icon, bool isDark) {
    return Expanded(
      child: Container(
        padding: const EdgeInsets.all(AppTokens.space14),
        decoration: BoxDecoration(
          color: isDark ? AppTokens.darkElevated : AppTokens.lightSurface,
          borderRadius: AppTokens.borderRadiusMd,
          border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(icon, size: 16, color: AppTokens.brandPrimary),
            const SizedBox(height: AppTokens.space8),
            Text(title, style: const TextStyle(fontSize: 11, color: AppTokens.darkTextMuted)),
            const SizedBox(height: 2),
            Text(value, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold, fontFamily: 'monospace')),
          ],
        ),
      ),
    );
  }

  // --- 3. Permissions Tab ---
  Widget _buildPermissionsTab(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    return SingleChildScrollView(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Autonomy & Tool Permissions', style: theme.textTheme.titleMedium),
          const SizedBox(height: AppTokens.space4),
          const Text(
            'Control what tools the specialist AI workers are permitted to execute autonomously.',
            style: TextStyle(fontSize: 12, color: AppTokens.darkTextMuted),
          ),
          const SizedBox(height: AppTokens.space16),

          SwitchListTile(
            title: const Text('Allow Code Edits & File Writing', style: TextStyle(fontSize: 13)),
            subtitle: const Text('Programmer worker can modify source code files within workspace scope', style: TextStyle(fontSize: 11, color: AppTokens.darkTextMuted)),
            value: _allowFileEdits,
            activeColor: AppTokens.brandPrimary,
            onChanged: (v) => setState(() => _allowFileEdits = v),
          ),
          const Divider(),
          SwitchListTile(
            title: const Text('Allow Terminal / Shell Execution', style: TextStyle(fontSize: 13)),
            subtitle: const Text('Tester worker can run unit tests (e.g. pytest, flutter test)', style: TextStyle(fontSize: 11, color: AppTokens.darkTextMuted)),
            value: _allowTerminalCommands,
            activeColor: AppTokens.brandPrimary,
            onChanged: (v) => setState(() => _allowTerminalCommands = v),
          ),
          const Divider(),
          SwitchListTile(
            title: const Text('Allow External Network Calls', style: TextStyle(fontSize: 13)),
            subtitle: const Text('Researcher worker can fetch external URLs or documentation', style: TextStyle(fontSize: 11, color: AppTokens.darkTextMuted)),
            value: _allowNetworkCalls,
            activeColor: AppTokens.brandPrimary,
            onChanged: (v) => setState(() => _allowNetworkCalls = v),
          ),
        ],
      ),
    );
  }

  // --- 4. Safety & Storage Tab ---
  Widget _buildSafetyStorageTab(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Safety & Local Storage', style: theme.textTheme.titleMedium),
        const SizedBox(height: AppTokens.space4),
        const Text(
          'Inspect local database paths, storage usage, and emergency halt status.',
          style: TextStyle(fontSize: 12, color: AppTokens.darkTextMuted),
        ),
        const SizedBox(height: AppTokens.space16),

        Container(
          padding: const EdgeInsets.all(AppTokens.space14),
          decoration: BoxDecoration(
            color: isDark ? AppTokens.darkElevated : AppTokens.lightSurface,
            borderRadius: AppTokens.borderRadiusMd,
            border: Border.all(color: isDark ? AppTokens.darkBorder : AppTokens.lightBorder),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('Active Workspace Path', style: TextStyle(fontSize: 11, color: AppTokens.darkTextMuted)),
              const SizedBox(height: 2),
              Text(widget.appState.activeWorkingPath, style: const TextStyle(fontSize: 12, fontFamily: 'monospace', fontWeight: FontWeight.bold)),
              const SizedBox(height: AppTokens.space12),
              const Text('Embedded SQLite Database', style: TextStyle(fontSize: 11, color: AppTokens.darkTextMuted)),
              const SizedBox(height: 2),
              const Text('autonomos.db (Local First)', style: TextStyle(fontSize: 12, fontFamily: 'monospace', fontWeight: FontWeight.bold)),
            ],
          ),
        ),
        const SizedBox(height: AppTokens.space20),

        ElevatedButton.icon(
          style: ElevatedButton.styleFrom(
            backgroundColor: widget.appState.isEmergencyStopped ? AppTokens.success : AppTokens.danger,
            foregroundColor: Colors.white,
            shape: const RoundedRectangleBorder(borderRadius: AppTokens.borderRadiusSm),
          ),
          icon: Icon(widget.appState.isEmergencyStopped ? Icons.play_arrow : Icons.stop, size: 16),
          label: Text(widget.appState.isEmergencyStopped ? 'Resume Workforce Execution' : 'Trigger Emergency Stop'),
          onPressed: () {
            if (widget.appState.isEmergencyStopped) {
              widget.appState.clearEmergencyHalt();
            } else {
              widget.appState.activateEmergencyStop('Operator manual stop via Settings');
            }
          },
        ),
      ],
    );
  }
}
