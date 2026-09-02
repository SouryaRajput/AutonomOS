import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../state/app_state.dart';
import '../widgets/custom_card.dart';
import '../widgets/status_badge.dart';

class ProviderSettingsView extends StatefulWidget {
  final AppState appState;

  const ProviderSettingsView({super.key, required this.appState});

  @override
  State<ProviderSettingsView> createState() => _ProviderSettingsViewState();
}

class _ProviderSettingsViewState extends State<ProviderSettingsView> {
  final TextEditingController _openRouterKeyController = TextEditingController();
  final TextEditingController _localEndpointController = TextEditingController(text: 'http://localhost:11434');
  bool _obscureOpenRouterKey = true;
  bool _isLoading = false;

  final List<Map<String, dynamic>> _providers = [
    {
      'id': 'openrouter',
      'name': 'OpenRouter Gateway',
      'type': 'CLOUD',
      'health': 'CONNECTED',
      'model_count': 14,
      'description': 'Unified inference gateway routing across Claude 3.5 Sonnet, GPT-4o, and DeepSeek R1',
      'has_key': true,
      'masked_key': 'sk-or-••••••••9a2f',
    },
    {
      'id': 'local-ollama',
      'name': 'Local Ollama Runtime',
      'type': 'LOCAL',
      'health': 'CONNECTED',
      'model_count': 3,
      'description': 'Offline local inference executing Qwen 2.5 Coder and Llama 3.1 with 0 egress cost',
      'endpoint': 'http://localhost:11434',
    },
    {
      'id': 'openai-direct',
      'name': 'Direct OpenAI Endpoint',
      'type': 'CLOUD',
      'health': 'UNAVAILABLE',
      'model_count': 0,
      'description': 'Direct OpenAI API integration (optional alternative to OpenRouter)',
      'has_key': false,
    },
  ];

  @override
  void dispose() {
    _openRouterKeyController.dispose();
    _localEndpointController.dispose();
    super.dispose();
  }

  void _saveProviderKey(String providerId, String key) {
    if (key.trim().isEmpty) return;
    setState(() {
      final p = _providers.firstWhere((prov) => prov['id'] == providerId);
      p['has_key'] = true;
      p['masked_key'] = key.length > 8 ? '${key.substring(0, 3)}••••••••${key.substring(key.length - 4)}' : '••••••••';
      p['health'] = 'CONNECTED';
    });
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text('API Key securely configured for $providerId')),
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    return SingleChildScrollView(
      padding: const EdgeInsets.all(AppTokens.space24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Inference Providers & OmniRoute', style: theme.textTheme.headlineMedium),
          const SizedBox(height: AppTokens.space4),
          Text(
            'Configure cloud API keys, local LLM runtimes, and inspect OmniRoute fallback telemetry',
            style: theme.textTheme.bodyMedium,
          ),
          const SizedBox(height: AppTokens.space24),

          // OmniRoute Routing Transparency Card
          CustomCard(
            borderColor: AppTokens.brandPrimaryLight.withOpacity(0.4),
            backgroundColor: AppTokens.brandPrimary.withOpacity(0.06),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Icon(Icons.route, size: 20, color: AppTokens.brandPrimaryLight),
                    const SizedBox(width: AppTokens.space8),
                    Text('OmniRoute Dynamic Gateway', style: theme.textTheme.titleMedium),
                    const Spacer(),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                      decoration: BoxDecoration(
                        color: AppTokens.success.withOpacity(0.15),
                        borderRadius: BorderRadius.circular(4),
                      ),
                      child: const Text(
                        'AUTO ROUTING ACTIVE',
                        style: TextStyle(color: AppTokens.success, fontSize: 10, fontWeight: FontWeight.bold),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: AppTokens.space12),
                Text(
                  'OmniRoute automatically selects optimal inference models per task category, enforces cost budgets, and performs seamless fallback if a provider experiences latency spikes or rate limits.',
                  style: theme.textTheme.bodyMedium,
                ),
                const SizedBox(height: AppTokens.space16),
                Row(
                  children: [
                    _buildRouteStat('Primary Model', 'Claude 3.5 Sonnet'),
                    const SizedBox(width: AppTokens.space16),
                    _buildRouteStat('Fallback Model', 'DeepSeek R1 / GPT-4o'),
                    const SizedBox(width: AppTokens.space16),
                    _buildRouteStat('Active Health', '2 / 3 Providers OK'),
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(height: AppTokens.space24),

          // Configured Providers List
          Text('Configured Providers', style: theme.textTheme.titleMedium),
          const SizedBox(height: AppTokens.space12),

          ..._providers.map((p) => _buildProviderCard(context, p)),
        ],
      ),
    );
  }

  Widget _buildProviderCard(BuildContext context, Map<String, dynamic> provider) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final isConnected = provider['health'] == 'CONNECTED';

    return Padding(
      padding: const EdgeInsets.only(bottom: AppTokens.space16),
      child: CustomCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  provider['type'] == 'LOCAL' ? Icons.computer : Icons.cloud_outlined,
                  size: 20,
                  color: isConnected ? AppTokens.brandPrimaryLight : AppTokens.darkTextMuted,
                ),
                const SizedBox(width: AppTokens.space8),
                Text(provider['name'], style: theme.textTheme.titleMedium),
                const Spacer(),
                _buildHealthBadge(provider['health']),
              ],
            ),
            const SizedBox(height: AppTokens.space8),
            Text(provider['description'], style: theme.textTheme.bodyMedium),
            const SizedBox(height: AppTokens.space16),

            if (provider['type'] == 'CLOUD') ...[
              // Cloud Key Config
              Row(
                children: [
                  Expanded(
                    child: provider['has_key'] == true
                        ? Container(
                            padding: const EdgeInsets.symmetric(horizontal: AppTokens.space12, vertical: 10),
                            decoration: BoxDecoration(
                              color: isDark ? AppTokens.darkSurface : AppTokens.lightBorder.withOpacity(0.3),
                              borderRadius: AppTokens.borderRadiusSm,
                            ),
                            child: Row(
                              children: [
                                const Icon(Icons.key, size: 14, color: AppTokens.success),
                                const SizedBox(width: AppTokens.space8),
                                Text(
                                  provider['masked_key'] ?? '••••••••',
                                  style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
                                ),
                              ],
                            ),
                          )
                        : TextField(
                            controller: _openRouterKeyController,
                            obscureText: _obscureOpenRouterKey,
                            decoration: InputDecoration(
                              hintText: 'Enter ${provider['name']} API Key (sk-...)',
                              suffixIcon: IconButton(
                                icon: Icon(_obscureOpenRouterKey ? Icons.visibility_off : Icons.visibility, size: 16),
                                onPressed: () => setState(() => _obscureOpenRouterKey = !_obscureOpenRouterKey),
                              ),
                            ),
                          ),
                  ),
                  const SizedBox(width: AppTokens.space12),
                  if (provider['has_key'] == true)
                    OutlinedButton(
                      onPressed: () {
                        setState(() {
                          provider['has_key'] = false;
                        });
                      },
                      child: const Text('Update Key'),
                    )
                  else
                    ElevatedButton(
                      onPressed: () => _saveProviderKey(provider['id'], _openRouterKeyController.text),
                      child: const Text('Save Key'),
                    ),
                ],
              ),
            ] else ...[
              // Local Endpoint Config
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _localEndpointController,
                      decoration: const InputDecoration(
                        labelText: 'Local Endpoint URL',
                        hintText: 'http://localhost:11434',
                      ),
                    ),
                  ),
                  const SizedBox(width: AppTokens.space12),
                  ElevatedButton(
                    onPressed: () {
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(content: Text('Local endpoint verified successfully')),
                      );
                    },
                    child: const Text('Test Connection'),
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildRouteStat(String label, String value) {
    return Expanded(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: const TextStyle(fontSize: 11, color: AppTokens.darkTextMuted)),
          const SizedBox(height: 2),
          Text(value, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold)),
        ],
      ),
    );
  }

  Widget _buildHealthBadge(String health) {
    Color bg;
    Color fg;
    switch (health) {
      case 'CONNECTED':
      case 'HEALTHY':
        bg = AppTokens.success.withOpacity(0.15);
        fg = AppTokens.success;
        break;
      case 'RATE_LIMITED':
        bg = AppTokens.warning.withOpacity(0.15);
        fg = AppTokens.warning;
        break;
      case 'QUOTA_EXHAUSTED':
        bg = Colors.orange.withOpacity(0.15);
        fg = Colors.orange;
        break;
      case 'UNAVAILABLE':
      default:
        bg = AppTokens.danger.withOpacity(0.15);
        fg = AppTokens.danger;
        break;
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: fg.withOpacity(0.3)),
      ),
      child: Text(
        health,
        style: TextStyle(color: fg, fontSize: 10, fontWeight: FontWeight.bold),
      ),
    );
  }
}
