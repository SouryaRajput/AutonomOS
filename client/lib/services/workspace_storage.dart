import 'dart:convert';
import 'dart:io';

/// Local JSON persistence for the active workspace path so the selected project folder survives app restarts.
class WorkspaceStorage {
  WorkspaceStorage._();

  static File _getStorageFile() {
    final home = Platform.environment['HOME'] ?? '.';
    final dir = Directory('$home/.autonomos');
    if (!dir.existsSync()) {
      try {
        dir.createSync(recursive: true);
      } catch (_) {}
    }
    return File('$home/.autonomos/workspace.json');
  }

  static String? loadActiveWorkspace() {
    try {
      final file = _getStorageFile();
      if (file.existsSync()) {
        final content = file.readAsStringSync();
        if (content.trim().isNotEmpty) {
          final data = json.decode(content);
          if (data is Map && data['active_path'] is String) {
            final p = data['active_path'] as String;
            if (Directory(p).existsSync()) {
              return p;
            }
          }
        }
      }
    } catch (_) {}
    return null;
  }

  static void saveActiveWorkspace(String path) {
    try {
      final file = _getStorageFile();
      file.writeAsStringSync(json.encode({'active_path': path}), flush: true);
    } catch (_) {}
  }
}
