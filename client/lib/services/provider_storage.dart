import 'dart:convert';
import 'dart:io';

/// Local JSON persistence for user-configured inference providers so settings survive app restarts and reloads.
class ProviderStorage {
  static File _getStorageFile() {
    final home = Platform.environment['HOME'] ?? '.';
    final dir = Directory('$home/.autonomos');
    if (!dir.existsSync()) {
      try {
        dir.createSync(recursive: true);
      } catch (_) {}
    }
    return File('$home/.autonomos/providers.json');
  }

  static List<Map<String, dynamic>> loadProviders() {
    try {
      final file = _getStorageFile();
      if (file.existsSync()) {
        final content = file.readAsStringSync();
        if (content.trim().isNotEmpty) {
          final data = json.decode(content);
          if (data is List) {
            return data.map((e) => Map<String, dynamic>.from(e as Map)).toList();
          }
        }
      }
    } catch (_) {}
    return [];
  }

  static void saveProviders(List<Map<String, dynamic>> providers) {
    try {
      final file = _getStorageFile();
      file.writeAsStringSync(json.encode(providers), flush: true);
    } catch (_) {}
  }
}
