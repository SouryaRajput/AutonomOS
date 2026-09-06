import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';

/// Represents a single file diff within the active workspace.
class WorkspaceFileDiff {
  final String filePath;
  final int additions;
  final int deletions;
  final String diffText;
  final bool isNew;
  final bool isDeleted;

  const WorkspaceFileDiff({
    required this.filePath,
    required this.additions,
    required this.deletions,
    required this.diffText,
    this.isNew = false,
    this.isDeleted = false,
  });

  String get fileName => filePath.split(Platform.pathSeparator).where((s) => s.isNotEmpty).lastOrNull ?? filePath;
}

/// Service that monitors the active workspace path for git and agent code modifications,
/// maintaining real-time lines added (+X) and lines removed (-Y) counters and unified diffs.
class WorkspaceDiffService extends ChangeNotifier {
  String _activePath = '';
  String _branchName = 'main';
  int _totalAdditions = 0;
  int _totalDeletions = 0;
  List<WorkspaceFileDiff> _fileDiffs = [];
  bool _isLoading = false;

  // Persistent record of files created or modified by AutonomOS agents
  final Map<String, WorkspaceFileDiff> _persistedAgentDiffs = {};

  String get activePath => _activePath;
  String get branchName => _branchName;
  int get totalAdditions => _totalAdditions;
  int get totalDeletions => _totalDeletions;
  List<WorkspaceFileDiff> get fileDiffs => List.unmodifiable(_fileDiffs);
  bool get isLoading => _isLoading;
  bool get hasChanges => _fileDiffs.isNotEmpty || _totalAdditions > 0 || _totalDeletions > 0;

  String get shortPath {
    if (_activePath.isEmpty) return 'Workspace';
    return _activePath.split(Platform.pathSeparator).where((s) => s.isNotEmpty).lastOrNull ?? 'Workspace';
  }

  /// Updates the active workspace path and triggers a fresh scan.
  Future<void> updateActivePath(String path, {bool force = false}) async {
    final clean = path.trim();
    if (!force && clean == _activePath && _fileDiffs.isNotEmpty) return;
    _activePath = clean;
    _persistedAgentDiffs.clear();
    _loadPersistedDiffs();
    await refresh();
  }

  /// Forces a full re-scan of the active workspace for created files and git changes.
  Future<void> rescanWorkspace() async {
    _persistedAgentDiffs.clear();
    _autoDiscoverCreatedFiles();
    await refresh();
  }

  /// Records a file created or modified during an agent execution session.
  void recordFileChange({
    required String relativePath,
    required String? oldContent,
    required String newContent,
  }) {
    if (relativePath.isEmpty || _activePath.isEmpty) return;

    WorkspaceFileDiff diff;
    if (oldContent == null || oldContent.isEmpty) {
      diff = generateNewFileDiff(relativePath, newContent);
    } else if (oldContent.trim() == newContent.trim()) {
      // If previously recorded as an added file with positive additions, preserve it
      if (_persistedAgentDiffs.containsKey(relativePath) && _persistedAgentDiffs[relativePath]!.additions > 0) {
        diff = _persistedAgentDiffs[relativePath]!;
      } else {
        // In a non-git workspace or untracked file, the full content represents additions
        diff = generateNewFileDiff(relativePath, newContent);
      }
    } else {
      diff = computeDiffForContent(
        filePath: relativePath,
        oldContent: oldContent,
        newContent: newContent,
      );
    }

    _persistedAgentDiffs[relativePath] = diff;
    _savePersistedDiffs();
    _recomputeDiffs();
    notifyListeners();
  }

  /// Clears recorded agent diffs
  void clearSessionChanges() {
    _persistedAgentDiffs.clear();
    try {
      final file = File('$_activePath/.autonomos/agent_diffs.json');
      if (file.existsSync()) file.deleteSync();
    } catch (_) {}
    _recomputeDiffs();
    notifyListeners();
  }

  /// Refreshes the diff status by inspecting git (if available) and merging agent diffs.
  Future<void> refresh() async {
    if (_activePath.isEmpty || !Directory(_activePath).existsSync()) {
      _branchName = 'main';
      _totalAdditions = 0;
      _totalDeletions = 0;
      _fileDiffs = [];
      notifyListeners();
      return;
    }

    _isLoading = true;
    notifyListeners();

    try {
      // 1. Ensure persisted diffs are loaded
      if (_persistedAgentDiffs.isEmpty) {
        _loadPersistedDiffs();
      }

      // 2. Detect Git branch
      await _detectGitBranch();

      // 3. Scan Git diffs and status
      final gitDiffs = await _scanGitChanges();

      // 4. Recompute and merge
      _recomputeDiffs(gitDiffs: gitDiffs);
    } catch (_) {
      _recomputeDiffs();
    } finally {
      _isLoading = false;
      notifyListeners();
    }
  }

  void _loadPersistedDiffs() {
    if (_activePath.isEmpty) return;
    try {
      final file = File('$_activePath/.autonomos/agent_diffs.json');
      if (file.existsSync()) {
        final raw = file.readAsStringSync();
        final map = json.decode(raw) as Map<String, dynamic>;
        for (final entry in map.entries) {
          final data = entry.value as Map<String, dynamic>;
          final adds = (data['additions'] as num?)?.toInt() ?? 0;
          final dels = (data['deletions'] as num?)?.toInt() ?? 0;
          if (adds > 0 || dels > 0) {
            _persistedAgentDiffs[entry.key] = WorkspaceFileDiff(
              filePath: data['filePath'] as String? ?? entry.key,
              additions: adds,
              deletions: dels,
              diffText: data['diffText'] as String? ?? '',
              isNew: data['isNew'] as bool? ?? true,
              isDeleted: data['isDeleted'] as bool? ?? false,
            );
          }
        }
      }
      if (_persistedAgentDiffs.isEmpty) {
        // Auto-discover existing 3D portfolio / AutonomOS generated files
        _autoDiscoverCreatedFiles();
      }
    } catch (_) {
      _autoDiscoverCreatedFiles();
    }
  }

  void _autoDiscoverCreatedFiles() {
    if (_activePath.isEmpty || !Directory(_activePath).existsSync()) return;

    const candidateFiles = ['index.html', 'portfolio_3d.js', 'styles_3d.css'];
    bool foundAny = false;

    for (final rel in candidateFiles) {
      final f = File('$_activePath/$rel');
      if (f.existsSync()) {
        try {
          final content = f.readAsStringSync();
          if (content.isNotEmpty) {
            _persistedAgentDiffs[rel] = generateNewFileDiff(rel, content);
            foundAny = true;
          }
        } catch (_) {}
      }
    }

    // If still empty and not a git repository, scan top-level project source files
    if (!foundAny && !Directory('$_activePath/.git').existsSync()) {
      try {
        final dir = Directory(_activePath);
        final list = dir.listSync(recursive: false);
        for (final item in list) {
          if (item is File) {
            final name = item.uri.pathSegments.where((s) => s.isNotEmpty).lastOrNull ?? '';
            if (name.startsWith('.') || name.endsWith('.json') || name.endsWith('.lock') || name.endsWith('.map')) {
              continue;
            }
            final content = item.readAsStringSync();
            if (content.isNotEmpty) {
              _persistedAgentDiffs[name] = generateNewFileDiff(name, content);
              foundAny = true;
            }
          }
        }
      } catch (_) {}
    }

    if (foundAny) {
      _savePersistedDiffs();
    }
  }

  void _savePersistedDiffs() {
    if (_activePath.isEmpty) return;
    try {
      final dir = Directory('$_activePath/.autonomos');
      if (!dir.existsSync()) dir.createSync(recursive: true);

      final out = <String, dynamic>{};
      for (final entry in _persistedAgentDiffs.entries) {
        out[entry.key] = {
          'filePath': entry.value.filePath,
          'additions': entry.value.additions,
          'deletions': entry.value.deletions,
          'diffText': entry.value.diffText,
          'isNew': entry.value.isNew,
          'isDeleted': entry.value.isDeleted,
        };
      }
      File('$_activePath/.autonomos/agent_diffs.json')
          .writeAsStringSync(const JsonEncoder.withIndent('  ').convert(out), flush: true);
    } catch (_) {}
  }

  Future<void> _detectGitBranch() async {
    try {
      final res = await Process.run(
        'git',
        ['rev-parse', '--abbrev-ref', 'HEAD'],
        workingDirectory: _activePath,
      );
      if (res.exitCode == 0) {
        final b = res.stdout.toString().trim();
        if (b.isNotEmpty) {
          _branchName = b;
          return;
        }
      }
    } catch (_) {}
    _branchName = 'main';
  }

  Future<Map<String, WorkspaceFileDiff>> _scanGitChanges() async {
    final diffs = <String, WorkspaceFileDiff>{};

    try {
      // Run git diff HEAD first, fallback to git diff
      var diffRes = await Process.run(
        'git',
        ['diff', 'HEAD'],
        workingDirectory: _activePath,
      );
      if (diffRes.exitCode != 0) {
        diffRes = await Process.run(
          'git',
          ['diff'],
          workingDirectory: _activePath,
        );
      }

      if (diffRes.exitCode == 0) {
        final diffOutput = diffRes.stdout.toString();
        final parsed = parseUnifiedDiff(diffOutput);
        diffs.addAll(parsed);
      }

      // Check untracked files via git status --porcelain
      final statusRes = await Process.run(
        'git',
        ['status', '--porcelain'],
        workingDirectory: _activePath,
      );

      if (statusRes.exitCode == 0) {
        final lines = statusRes.stdout.toString().split('\n');
        for (final line in lines) {
          final trimmed = line.trim();
          if (trimmed.startsWith('??')) {
            final rel = trimmed.substring(2).trim().replaceAll('"', '');
            // Skip hidden or system artifacts
            if (rel.startsWith('.git') || rel.startsWith('.autonomos') || rel.startsWith('.DS_Store')) {
              continue;
            }
            if (!diffs.containsKey(rel)) {
              final file = File('$_activePath/$rel');
              if (file.existsSync()) {
                try {
                  final content = file.readAsStringSync();
                  final fileDiff = generateNewFileDiff(rel, content);
                  diffs[rel] = fileDiff;
                } catch (_) {}
              }
            }
          }
        }
      }
    } catch (_) {}

    return diffs;
  }

  void _recomputeDiffs({Map<String, WorkspaceFileDiff>? gitDiffs}) {
    final merged = <String, WorkspaceFileDiff>{};

    // Include git diffs if any
    if (gitDiffs != null) {
      merged.addAll(gitDiffs);
    }

    // Overlay persisted agent diffs
    merged.addAll(_persistedAgentDiffs);

    // Only include files that have actual additions or deletions
    _fileDiffs = merged.values.where((d) => d.additions > 0 || d.deletions > 0).toList();
    _totalAdditions = _fileDiffs.fold<int>(0, (sum, d) => sum + d.additions);
    _totalDeletions = _fileDiffs.fold<int>(0, (sum, d) => sum + d.deletions);
  }

  /// Parses a multi-file unified git diff output into individual WorkspaceFileDiff records
  static Map<String, WorkspaceFileDiff> parseUnifiedDiff(String gitDiffOutput) {
    final result = <String, WorkspaceFileDiff>{};
    if (gitDiffOutput.trim().isEmpty) return result;

    final filePattern = RegExp(r'^diff --git a\/(.+?) b\/(.+?)$', multiLine: true);
    final matches = filePattern.allMatches(gitDiffOutput).toList();

    for (int i = 0; i < matches.length; i++) {
      final match = matches[i];
      final filePath = match.group(2) ?? match.group(1) ?? 'file';
      final start = match.start;
      final end = (i + 1 < matches.length) ? matches[i + 1].start : gitDiffOutput.length;
      final chunk = gitDiffOutput.substring(start, end).trim();

      int adds = 0;
      int dels = 0;
      bool isNew = chunk.contains('new file mode');
      bool isDeleted = chunk.contains('deleted file mode');

      final lines = chunk.split('\n');
      for (final l in lines) {
        if (l.startsWith('+') && !l.startsWith('+++')) adds++;
        if (l.startsWith('-') && !l.startsWith('---')) dels++;
      }

      result[filePath] = WorkspaceFileDiff(
        filePath: filePath,
        additions: adds,
        deletions: dels,
        diffText: chunk,
        isNew: isNew,
        isDeleted: isDeleted,
      );
    }

    return result;
  }

  /// Creates a unified diff for a brand-new file
  static WorkspaceFileDiff generateNewFileDiff(String filePath, String content) {
    final lines = content.isEmpty ? <String>[] : content.split('\n');
    final additions = lines.length;

    final buffer = StringBuffer();
    buffer.writeln('--- /dev/null');
    buffer.writeln('+++ b/$filePath');
    if (lines.isNotEmpty) {
      buffer.writeln('@@ -0,0 +1,$additions @@');
      for (final l in lines) {
        buffer.writeln('+$l');
      }
    }

    return WorkspaceFileDiff(
      filePath: filePath,
      additions: additions,
      deletions: 0,
      diffText: buffer.toString().trim(),
      isNew: true,
      isDeleted: false,
    );
  }

  /// Computes a unified diff between oldContent and newContent
  static WorkspaceFileDiff computeDiffForContent({
    required String filePath,
    required String? oldContent,
    required String newContent,
  }) {
    if (oldContent == null || oldContent.isEmpty) {
      return generateNewFileDiff(filePath, newContent);
    }

    final oldLines = oldContent.split('\n');
    final newLines = newContent.split('\n');

    // Fast path: contents are identical
    if (oldContent == newContent) {
      return WorkspaceFileDiff(
        filePath: filePath,
        additions: 0,
        deletions: 0,
        diffText: '--- a/$filePath\n+++ b/$filePath\n@@ -1,${oldLines.length} +1,${newLines.length} @@\n',
      );
    }

    // Line-by-line diff using LCS approach
    final diffOps = _diffLines(oldLines, newLines);

    int adds = 0;
    int dels = 0;
    final buffer = StringBuffer();
    buffer.writeln('--- a/$filePath');
    buffer.writeln('+++ b/$filePath');
    buffer.writeln('@@ -1,${oldLines.length} +1,${newLines.length} @@');

    for (final op in diffOps) {
      if (op.type == _DiffType.add) {
        adds++;
        buffer.writeln('+${op.line}');
      } else if (op.type == _DiffType.delete) {
        dels++;
        buffer.writeln('-${op.line}');
      } else {
        buffer.writeln(' ${op.line}');
      }
    }

    return WorkspaceFileDiff(
      filePath: filePath,
      additions: adds,
      deletions: dels,
      diffText: buffer.toString().trim(),
      isNew: false,
      isDeleted: false,
    );
  }

  /// Sanitizes and validates a file path ensuring it stays strictly inside the workspace
  static String sanitizeRelativePath(String raw, [String? activePath]) {
    var p = raw.replaceAll('`', '').replaceAll('"', '').replaceAll("'", '').trim();

    // If an absolute path was passed that starts with activePath, strip activePath
    if (activePath != null && activePath.isNotEmpty) {
      final normActive = activePath.replaceAll('\\', '/');
      final normP = p.replaceAll('\\', '/');
      if (normP.startsWith(normActive)) {
        p = normP.substring(normActive.length);
      }
    }

    // Strip leading slashes
    while (p.startsWith('/') || p.startsWith('\\')) {
      p = p.substring(1);
    }
    // Strip leading ./
    if (p.startsWith('./') || p.startsWith('.\\')) {
      p = p.substring(2);
    }
    // Prevent directory traversal
    if (p.contains('..')) {
      return '';
    }
    return p;
  }

  static List<_DiffOp> _diffLines(List<String> a, List<String> b) {
    final m = a.length;
    final n = b.length;
    final lcs = List.generate(m + 1, (_) => List.filled(n + 1, 0));

    for (int i = 0; i < m; i++) {
      for (int j = 0; j < n; j++) {
        if (a[i] == b[j]) {
          lcs[i + 1][j + 1] = lcs[i][j] + 1;
        } else {
          lcs[i + 1][j + 1] = lcs[i][j] > lcs[i][j + 1] ? lcs[i][j] : lcs[i][j + 1];
        }
      }
    }

    final ops = <_DiffOp>[];
    int i = m;
    int j = n;
    while (i > 0 || j > 0) {
      if (i > 0 && j > 0 && a[i - 1] == b[j - 1]) {
        ops.add(_DiffOp(_DiffType.keep, a[i - 1]));
        i--;
        j--;
      } else if (j > 0 && (i == 0 || lcs[i][j - 1] >= lcs[i - 1][j])) {
        ops.add(_DiffOp(_DiffType.add, b[j - 1]));
        j--;
      } else if (i > 0 && (j == 0 || lcs[i][j - 1] < lcs[i - 1][j])) {
        ops.add(_DiffOp(_DiffType.delete, a[i - 1]));
        i--;
      }
    }

    return ops.reversed.toList();
  }
}

enum _DiffType { add, delete, keep }

class _DiffOp {
  final _DiffType type;
  final String line;
  const _DiffOp(this.type, this.line);
}

class _SessionFileChange {
  final String relativePath;
  final String? oldContent;
  final String newContent;
  final DateTime timestamp;

  _SessionFileChange({
    required this.relativePath,
    required this.oldContent,
    required this.newContent,
    required this.timestamp,
  });
}
