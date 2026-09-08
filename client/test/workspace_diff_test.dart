import 'package:flutter_test/flutter_test.dart';
import 'package:autonomos/services/workspace_diff_service.dart';

void main() {
  group('Workspace Diff Service & Path Containment Tests', () {
    test('1. Calculates +1 -0 when one line is added and 0 removed (New single-line file)', () {
      final diff = WorkspaceDiffService.computeDiffForContent(
        filePath: 'index.html',
        oldContent: null,
        newContent: '<!DOCTYPE html>',
      );

      expect(diff.filePath, equals('index.html'));
      expect(diff.additions, equals(1));
      expect(diff.deletions, equals(0));
      expect(diff.isNew, isTrue);
      expect(diff.diffText.contains('+<!DOCTYPE html>'), isTrue);
    });

    test('2. Calculates +1 -0 when one line is inserted into existing file', () {
      const oldText = 'console.log("start");\nconsole.log("end");';
      const newText = 'console.log("start");\nconsole.log("middle");\nconsole.log("end");';

      final diff = WorkspaceDiffService.computeDiffForContent(
        filePath: 'app.js',
        oldContent: oldText,
        newContent: newText,
      );

      expect(diff.filePath, equals('app.js'));
      expect(diff.additions, equals(1));
      expect(diff.deletions, equals(0));
      expect(diff.isNew, isFalse);
      expect(diff.diffText.contains('+console.log("middle");'), isTrue);
    });

    test('3. Calculates +0 -1 when one line is deleted from existing file', () {
      const oldText = 'line 1\nline 2\nline 3';
      const newText = 'line 1\nline 3';

      final diff = WorkspaceDiffService.computeDiffForContent(
        filePath: 'notes.txt',
        oldContent: oldText,
        newContent: newText,
      );

      expect(diff.filePath, equals('notes.txt'));
      expect(diff.additions, equals(0));
      expect(diff.deletions, equals(1));
      expect(diff.diffText.contains('-line 2'), isTrue);
    });

    test('4. Calculates multiple additions and deletions accurately', () {
      const oldText = 'alpha\nbeta\ngamma';
      const newText = 'alpha\nbravo\ncharlie\ngamma';

      final diff = WorkspaceDiffService.computeDiffForContent(
        filePath: 'config.env',
        oldContent: oldText,
        newContent: newText,
      );

      expect(diff.filePath, equals('config.env'));
      expect(diff.additions, equals(2));
      expect(diff.deletions, equals(1));
      expect(diff.diffText.contains('-beta'), isTrue);
      expect(diff.diffText.contains('+bravo'), isTrue);
      expect(diff.diffText.contains('+charlie'), isTrue);
    });

    test('5. Parses unified git diff output correctly into WorkspaceFileDiff', () {
      const gitDiff = '''diff --git a/index.html b/index.html
index e69de29..d95f3ad 100644
--- a/index.html
+++ b/index.html
@@ -1,2 +1,3 @@
 <html>
+  <head><title>Portfolio</title></head>
 </html>
diff --git a/styles.css b/styles.css
new file mode 100644
index 0000000..f923b12
--- /dev/null
+++ b/styles.css
@@ -0,0 +1,2 @@
+body { margin: 0; }
+canvas { display: block; }
''';

      final parsed = WorkspaceDiffService.parseUnifiedDiff(gitDiff);
      expect(parsed.containsKey('index.html'), isTrue);
      expect(parsed['index.html']!.additions, equals(1));
      expect(parsed['index.html']!.deletions, equals(0));

      expect(parsed.containsKey('styles.css'), isTrue);
      expect(parsed['styles.css']!.additions, equals(2));
      expect(parsed['styles.css']!.deletions, equals(0));
      expect(parsed['styles.css']!.isNew, isTrue);
    });

    test('6. WorkspaceDiffService session tracking aggregates totals across files', () {
      final service = WorkspaceDiffService();

      expect(service.totalAdditions, equals(0));
      expect(service.totalDeletions, equals(0));

      // Add file 1: 1 line added, 0 removed
      service.recordFileChange(
        relativePath: 'index.html',
        oldContent: null,
        newContent: '<canvas id="bg"></canvas>',
      );

      expect(service.totalAdditions, equals(1));
      expect(service.totalDeletions, equals(0));
      expect(service.fileDiffs.length, equals(1));

      // Add file 2: 3 lines added, 1 line deleted
      service.recordFileChange(
        relativePath: 'styles.css',
        oldContent: 'body { color: black; }',
        newContent: 'body {\n  color: white;\n  background: #000;\n}',
      );

      expect(service.totalAdditions, equals(5));
      expect(service.totalDeletions, equals(1));
      expect(service.fileDiffs.length, equals(2));

      // Clear session changes resets counters
      service.clearSessionChanges();
      expect(service.totalAdditions, equals(0));
      expect(service.totalDeletions, equals(0));
      expect(service.fileDiffs.isEmpty, isTrue);
    });

    test('7. Workspace path sanitization enforces relative paths within selected workspace', () {
      const activeWorkspace = '/Users/shirsh/Downloads/Programming/Portfolio website';

      // Simple relative path
      expect(
        WorkspaceDiffService.sanitizeRelativePath('index.html', activeWorkspace),
        equals('index.html'),
      );

      // Subfolder relative path
      expect(
        WorkspaceDiffService.sanitizeRelativePath('src/js/app.js', activeWorkspace),
        equals('src/js/app.js'),
      );

      // Leading ./
      expect(
        WorkspaceDiffService.sanitizeRelativePath('./styles/main.css', activeWorkspace),
        equals('styles/main.css'),
      );

      // Full absolute path matching active workspace is stripped to relative path
      expect(
        WorkspaceDiffService.sanitizeRelativePath(
          '/Users/shirsh/Downloads/Programming/Portfolio website/portfolio_3d.js',
          activeWorkspace,
        ),
        equals('portfolio_3d.js'),
      );

      // Backticks or quotes are stripped
      expect(
        WorkspaceDiffService.sanitizeRelativePath('`index.html`', activeWorkspace),
        equals('index.html'),
      );
    });

    test('8. Workspace path containment rejects directory traversal escaping workspace', () {
      const activeWorkspace = '/Users/shirsh/Downloads/Programming/Portfolio website';

      // Directory traversal attempts are rejected (returns empty string)
      expect(
        WorkspaceDiffService.sanitizeRelativePath('../../etc/passwd', activeWorkspace),
        isEmpty,
      );

      expect(
        WorkspaceDiffService.sanitizeRelativePath('../other_project/secret.key', activeWorkspace),
        isEmpty,
      );
    });
  });
}
