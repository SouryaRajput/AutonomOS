import 'dart:io';

/// Native macOS & Cross-platform Directory Picker Service for AutonomOS.
/// Opens the native operating system folder selection panel and returns the selected directory path.
class FolderPickerService {
  FolderPickerService._();

  static Future<String?> pickFolder({String? initialDirectory}) async {
    if (Platform.isMacOS) {
      try {
        final script = initialDirectory != null && initialDirectory.trim().isNotEmpty
            ? 'POSIX path of (choose folder default location POSIX file "${initialDirectory.trim()}" with prompt "Select Project Workspace Folder")'
            : 'POSIX path of (choose folder with prompt "Select Project Workspace Folder")';

        final result = await Process.run('osascript', ['-e', script]);
        if (result.exitCode == 0) {
          final rawPath = (result.stdout as String).trim();
          if (rawPath.isNotEmpty) {
            // Strip trailing slashes
            final cleanPath = rawPath.endsWith('/') && rawPath.length > 1
                ? rawPath.substring(0, rawPath.length - 1)
                : rawPath;
            return cleanPath;
          }
        }
      } catch (_) {}
    } else if (Platform.isLinux) {
      try {
        final result = await Process.run('zenity', ['--file-selection', '--directory', '--title=Select Project Workspace Folder']);
        if (result.exitCode == 0) {
          final p = (result.stdout as String).trim();
          if (p.isNotEmpty) return p;
        }
      } catch (_) {}
    }

    return null;
  }
}
