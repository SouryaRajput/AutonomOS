import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../widgets/custom_card.dart';

class OnboardingDialog extends StatefulWidget {
  final VoidCallback onComplete;

  const OnboardingDialog({super.key, required this.onComplete});

  @override
  State<OnboardingDialog> createState() => _OnboardingDialogState();
}

class _OnboardingDialogState extends State<OnboardingDialog> {
  int _currentStep = 0;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    return Dialog(
      backgroundColor: Colors.transparent,
      child: Container(
        width: 540,
        padding: const EdgeInsets.all(AppTokens.space24),
        decoration: BoxDecoration(
          color: isDark ? AppTokens.darkBackground : AppTokens.lightBackground,
          borderRadius: AppTokens.borderRadiusMd,
          border: Border.all(color: Theme.of(context).dividerColor),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withOpacity(0.4),
              blurRadius: 24,
              offset: const Offset(0, 8),
            ),
          ],
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Header with step progress
            Row(
              children: [
                const Icon(Icons.auto_awesome, color: AppTokens.brandPrimaryLight, size: 24),
                const SizedBox(width: AppTokens.space8),
                Text('Welcome to AutonomOS', style: theme.textTheme.headlineSmall),
                const Spacer(),
                Text('Step ${_currentStep + 1} of 3', style: const TextStyle(fontSize: 12, color: AppTokens.darkTextMuted)),
              ],
            ),
            const SizedBox(height: AppTokens.space16),

            // Step Content
            if (_currentStep == 0) ...[
              Text('Local-First Autonomous Workforce', style: theme.textTheme.titleMedium),
              const SizedBox(height: AppTokens.space8),
              const Text(
                'AutonomOS empowers you to delegate complex engineering goals to an autonomous team of specialist AI agents (Manager, Researcher, Programmer, Tester).',
                style: TextStyle(height: 1.4, fontSize: 13),
              ),
              const SizedBox(height: AppTokens.space12),
              CustomCard(
                backgroundColor: AppTokens.brandPrimary.withOpacity(0.06),
                borderColor: AppTokens.brandPrimaryLight.withOpacity(0.3),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: const [
                    Text('• Conversational Intent: Simply describe what you want to build.', style: TextStyle(fontSize: 12)),
                    SizedBox(height: 4),
                    Text('• Deterministic Verification: Every code change is verified with evidence.', style: TextStyle(fontSize: 12)),
                    SizedBox(height: 4),
                    Text('• Safety First: Consequential actions require your authorization.', style: TextStyle(fontSize: 12)),
                  ],
                ),
              ),
            ] else if (_currentStep == 1) ...[
              Text('Local Storage & Privacy Guarantee', style: theme.textTheme.titleMedium),
              const SizedBox(height: AppTokens.space8),
              const Text(
                'Your source code, SQLite databases, tasks, and memory artifacts live entirely on your local machine. Nothing is sent to external cloud servers except model inference prompts.',
                style: TextStyle(height: 1.4, fontSize: 13),
              ),
              const SizedBox(height: AppTokens.space12),
              CustomCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: const [
                    Text('• 100% Offline with Ollama: Run models locally with zero data egress.', style: TextStyle(fontSize: 12)),
                    SizedBox(height: 4),
                    Text('• Masked API Keys: Credentials are never logged or stored in plain Markdown.', style: TextStyle(fontSize: 12)),
                  ],
                ),
              ),
            ] else ...[
              Text('Cost & Open Source Transparency', style: theme.textTheme.titleMedium),
              const SizedBox(height: AppTokens.space8),
              const Text(
                'AutonomOS is 100% open source and free to use. External model providers (OpenRouter, Anthropic, OpenAI) charge directly per token; all cost metrics in the app are labeled as estimates.',
                style: TextStyle(height: 1.4, fontSize: 13),
              ),
              const SizedBox(height: AppTokens.space12),
              CustomCard(
                backgroundColor: AppTokens.success.withOpacity(0.06),
                borderColor: AppTokens.success.withOpacity(0.3),
                child: const Text(
                  'You maintain complete governance with configurable budget limits, iteration budgets, and emergency stops.',
                  style: TextStyle(fontSize: 12, height: 1.4),
                ),
              ),
            ],

            const SizedBox(height: AppTokens.space24),

            // Footer Actions
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                if (_currentStep > 0)
                  OutlinedButton(
                    onPressed: () => setState(() => _currentStep--),
                    child: const Text('Back'),
                  )
                else
                  const SizedBox.shrink(),
                ElevatedButton(
                  onPressed: () {
                    if (_currentStep < 2) {
                      setState(() => _currentStep++);
                    } else {
                      Navigator.of(context).pop();
                      widget.onComplete();
                    }
                  },
                  child: Text(_currentStep < 2 ? 'Next' : 'Get Started'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
