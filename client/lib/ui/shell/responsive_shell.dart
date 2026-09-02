import 'package:flutter/material.dart';
import '../../state/app_state.dart';
import 'claude_shell.dart';

/// Backward-compatible wrapper delegating directly to ClaudeShell.
class ResponsiveShell extends StatelessWidget {
  final AppState appState;

  const ResponsiveShell({super.key, required this.appState});

  @override
  Widget build(BuildContext context) {
    return ClaudeShell(appState: appState);
  }
}
