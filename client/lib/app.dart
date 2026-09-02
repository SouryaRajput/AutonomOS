import 'package:flutter/material.dart';
import 'core/theme/app_theme.dart';
import 'state/app_state.dart';
import 'ui/shell/claude_shell.dart';

/// Application root. MaterialApp is built ONCE and never rebuilt by appState
/// changes. All reactive UI updates happen inside ClaudeShell's own builders.
/// Only themeMode changes trigger a rebuild at this level.
class AutonomOSAppRoot extends StatefulWidget {
  final AppState appState;

  const AutonomOSAppRoot({super.key, required this.appState});

  @override
  State<AutonomOSAppRoot> createState() => _AutonomOSAppRootState();
}

class _AutonomOSAppRootState extends State<AutonomOSAppRoot> {
  ThemeMode _themeMode = ThemeMode.dark;

  @override
  void initState() {
    super.initState();
    _themeMode = widget.appState.themeMode;
    widget.appState.addListener(_onAppStateChanged);
  }

  @override
  void didUpdateWidget(covariant AutonomOSAppRoot oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.appState != widget.appState) {
      oldWidget.appState.removeListener(_onAppStateChanged);
      widget.appState.addListener(_onAppStateChanged);
      _themeMode = widget.appState.themeMode;
    }
  }

  @override
  void dispose() {
    widget.appState.removeListener(_onAppStateChanged);
    super.dispose();
  }

  void _onAppStateChanged() {
    // Only rebuild MaterialApp when themeMode actually changes.
    // All other state changes are handled by ClaudeShell's AnimatedBuilder.
    if (widget.appState.themeMode != _themeMode) {
      setState(() {
        _themeMode = widget.appState.themeMode;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'AutonomOS',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.lightTheme,
      darkTheme: AppTheme.darkTheme,
      themeMode: _themeMode,
      home: ClaudeShell(appState: widget.appState),
    );
  }
}
