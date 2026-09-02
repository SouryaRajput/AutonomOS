import 'dart:convert';
import 'dart:io';

/// Deterministic, semantic Project Map generation & incremental auditing engine in Dart.
/// Classifies files, excludes generated artifacts (.next, node_modules), and maintains a rich
/// semantic architectural map (.autonomos/project-map.md) and machine-readable snapshot (.autonomos/last_snapshot.json).
class LocalWorkspaceAuditor {
  LocalWorkspaceAuditor._();

  static String _computeFileHash(List<int> bytes) {
    // 64-bit FNV-1a deterministic hash implementation in pure Dart (zero external dependencies)
    var hash = BigInt.parse('cbf29ce484222325', radix: 16);
    final fnvPrime = BigInt.parse('100000001b3', radix: 16);
    final mask64 = BigInt.parse('ffffffffffffffff', radix: 16);

    for (final b in bytes) {
      hash = (hash ^ BigInt.from(b)) * fnvPrime & mask64;
    }
    return hash.toRadixString(16).padLeft(16, '0');
  }

  static const List<String> ignoredDirs = [
    '.next',
    'node_modules',
    '.git',
    'coverage',
    'dist',
    'build',
    'out',
    '.turbo',
    '.cache',
    '.dart_tool',
    '__pycache__',
    '.pytest_cache',
    '.mypy_cache',
    '.venv',
    'venv',
    'env',
    '.idea',
    '.vscode',
    '.autonomos',
    '.gradle',
  ];

  /// Classifies a relative path into one of the core categories:
  /// source | configuration | asset | generated | metadata | dependency
  static String classifyFile(String relPath) {
    final name = relPath.split('/').last.toLowerCase();
    final ext = name.contains('.') ? '.${name.split('.').last}' : '';

    if (relPath.startsWith('.next/') ||
        relPath.startsWith('dist/') ||
        relPath.startsWith('build/') ||
        relPath.startsWith('out/') ||
        relPath.startsWith('.turbo/')) {
      return 'generated';
    }

    if (relPath.startsWith('node_modules/') || relPath.startsWith('.dart_tool/')) {
      return 'dependency';
    }

    if (name == 'package.json' ||
        name == 'pubspec.yaml' ||
        name == 'requirements.txt' ||
        name == 'pyproject.toml' ||
        name == 'cargo.toml' ||
        name == 'go.mod' ||
        name.startsWith('next.config.') ||
        name.startsWith('tsconfig.') ||
        name.startsWith('tailwind.config.') ||
        name.startsWith('postcss.config.') ||
        name.startsWith('vite.config.') ||
        name == '.eslintrc.json' ||
        name == 'components.json' ||
        name == 'dockerfile' ||
        name.startsWith('docker-compose')) {
      return 'configuration';
    }

    if (name == 'readme.md' ||
        name == 'license' ||
        name == 'changelog.md' ||
        name == 'contributing.md' ||
        ext == '.md' ||
        ext == '.txt') {
      return 'metadata';
    }

    if (relPath.startsWith('public/') ||
        relPath.startsWith('assets/') ||
        relPath.startsWith('static/') ||
        ext == '.png' ||
        ext == '.jpg' ||
        ext == '.jpeg' ||
        ext == '.svg' ||
        ext == '.ico' ||
        ext == '.webp' ||
        ext == '.glb' ||
        ext == '.gltf' ||
        ext == '.hdr' ||
        ext == '.woff' ||
        ext == '.woff2' ||
        ext == '.ttf') {
      return 'asset';
    }

    if (ext == '.tsx' ||
        ext == '.ts' ||
        ext == '.jsx' ||
        ext == '.js' ||
        ext == '.dart' ||
        ext == '.py' ||
        ext == '.css' ||
        ext == '.scss' ||
        ext == '.html' ||
        ext == '.rs' ||
        ext == '.go') {
      return 'source';
    }

    return 'source';
  }

  /// Ensures that .autonomos/project-map.md and .autonomos/last_snapshot.json actually exist on disk.
  /// If missing: performs full semantic audit, writes files, and verifies existence.
  /// If present: performs incremental change detection ignoring generated files, and updates if needed.
  static Future<Map<String, dynamic>> auditWorkspace(String rootPath, {String? requestedObjective}) async {
    final rootDir = Directory(rootPath);
    if (!rootDir.existsSync()) {
      return {'error': 'Directory does not exist: $rootPath'};
    }

    final metaDir = Directory('$rootPath/.autonomos');
    if (!metaDir.existsSync()) {
      metaDir.createSync(recursive: true);
    }

    final mapMdFile = File('$rootPath/.autonomos/project-map.md');
    final mapJsonFile = File('$rootPath/.autonomos/project_map.json');
    final snapshotFile = File('$rootPath/.autonomos/last_snapshot.json');

    final isInitialized = mapMdFile.existsSync() && snapshotFile.existsSync();

    if (!isInitialized) {
      return _performFullAudit(rootDir, mapMdFile, mapJsonFile, snapshotFile, requestedObjective: requestedObjective);
    } else {
      return _performIncrementalAudit(rootDir, mapMdFile, mapJsonFile, snapshotFile, requestedObjective: requestedObjective);
    }
  }

  static Future<Map<String, dynamic>> _performFullAudit(
    Directory rootDir,
    File mapMdFile,
    File mapJsonFile,
    File snapshotFile, {
    String? requestedObjective,
  }) async {
    final rootPath = rootDir.path;
    final projectName = rootPath.split(Platform.pathSeparator).where((s) => s.isNotEmpty).last;
    final nowIso = DateTime.now().toUtc().toIso8601String();

    final meaningfulFiles = <String, File>{};

    void scanDir(Directory currentDir, String relPrefix) {
      for (final entity in currentDir.listSync(followLinks: false)) {
        final name = entity.uri.pathSegments.where((s) => s.isNotEmpty).last;
        if (ignoredDirs.contains(name)) continue;

        if (entity is Directory) {
          scanDir(entity, relPrefix.isEmpty ? name : '$relPrefix/$name');
        } else if (entity is File) {
          final relPath = relPrefix.isEmpty ? name : '$relPrefix/$name';
          meaningfulFiles[relPath] = entity;
        }
      }
    }

    scanDir(rootDir, '');

    // 1. Inspect package.json / dependencies / frameworks
    Map<String, dynamic> packageJson = {};
    if (meaningfulFiles.containsKey('package.json')) {
      try {
        packageJson = json.decode(meaningfulFiles['package.json']!.readAsStringSync());
      } catch (_) {}
    }

    final dependencies = <String, String>{};
    final devDependencies = <String, String>{};
    final scripts = <String, String>{};

    if (packageJson['dependencies'] is Map) {
      for (final e in (packageJson['dependencies'] as Map).entries) {
        dependencies[e.key.toString()] = e.value.toString();
      }
    }
    if (packageJson['devDependencies'] is Map) {
      for (final e in (packageJson['devDependencies'] as Map).entries) {
        devDependencies[e.key.toString()] = e.value.toString();
      }
    }
    if (packageJson['scripts'] is Map) {
      for (final e in (packageJson['scripts'] as Map).entries) {
        scripts[e.key.toString()] = e.value.toString();
      }
    }

    // 2. Scan source file imports for verified library usage
    final allImportedModules = <String>{};
    for (final entry in meaningfulFiles.entries) {
      final ext = entry.key.split('.').last.toLowerCase();
      if (['ts', 'tsx', 'js', 'jsx', 'dart', 'py'].contains(ext)) {
        try {
          final content = entry.value.readAsStringSync();
          final importMatches = RegExp(r'''(?:import|from)\s+['"]([^'"]+)['"]''').allMatches(content);
          for (final m in importMatches) {
            final imp = m.group(1) ?? '';
            final clean = imp.startsWith('@') ? imp.split('/').take(2).join('/') : imp.split('/').first;
            allImportedModules.add(clean);
          }
        } catch (_) {}
      }
    }

    // 3. Detect Evidence-Based Technology Stack & Router Type
    final techStack = <String>[];
    String projectType = 'Application Workspace';
    String architecture = 'Modular Component Architecture';

    final isNextJs = dependencies.containsKey('next') || devDependencies.containsKey('next') || meaningfulFiles.keys.any((k) => k.startsWith('src/app/') || k.startsWith('app/'));
    final isReact = dependencies.containsKey('react') || devDependencies.containsKey('react') || meaningfulFiles.keys.any((k) => k.endsWith('.tsx') || k.endsWith('.jsx'));
    final isTailwind = dependencies.containsKey('tailwindcss') || devDependencies.containsKey('tailwindcss') || meaningfulFiles.keys.any((k) => k.contains('tailwind.config'));
    final isTypeScript = devDependencies.containsKey('typescript') || meaningfulFiles.keys.any((k) => k.endsWith('.ts') || k.endsWith('.tsx'));

    final hasInstalled3D = dependencies.containsKey('three') || devDependencies.containsKey('@types/three') || dependencies.containsKey('@react-three/fiber');
    final hasVerified3D = allImportedModules.any((imp) => imp.contains('three') || imp.contains('@react-three'));

    final hasVerifiedMotion = allImportedModules.contains('framer-motion');
    final isFlutter = meaningfulFiles.containsKey('pubspec.yaml');
    final isPython = meaningfulFiles.containsKey('pyproject.toml') || meaningfulFiles.containsKey('requirements.txt');

    if (isNextJs) {
      techStack.add('Next.js');
      if (hasVerified3D) {
        projectType = 'Next.js Web Application with Three.js 3D Rendering';
      } else {
        projectType = meaningfulFiles.keys.any((k) => k.toLowerCase().contains('portfolio') || k.toLowerCase().contains('hero'))
            ? 'Next.js Portfolio Web Application'
            : 'Next.js Web Application';
      }

      final hasAppRouter = meaningfulFiles.keys.any((k) => k.startsWith('src/app/') || k.startsWith('app/'));
      if (hasAppRouter) {
        techStack.add('Next.js App Router');
        architecture = 'Next.js App Router (React Server & Client Components, Global Layouts, Modular UI)';
      } else {
        techStack.add('Next.js Pages Router');
        architecture = 'Next.js Pages Router';
      }
    }
    if (isReact) techStack.add('React');
    if (isTypeScript) techStack.add('TypeScript');
    if (isTailwind) techStack.add('Tailwind CSS');
    if (hasVerified3D) techStack.add('Three.js / 3D Graphics');
    if (hasVerifiedMotion) techStack.add('Framer Motion (Animations)');
    if (isFlutter) {
      techStack.add('Flutter / Dart');
      projectType = 'Flutter Cross-Platform Application';
    }
    if (isPython) {
      techStack.add('Python');
      projectType = 'Python Application / Backend';
    }

    // 4. Strict Framework-Aware Route Discovery & Entry Points
    final entryPoints = <String>[];
    final routes = <String, String>{};
    final majorComponents = <String>[];
    final configFiles = <String>[];
    final assetFiles = <String>[];

    for (final rel in meaningfulFiles.keys) {
      final cls = classifyFile(rel);

      if (cls == 'configuration') configFiles.add(rel);
      if (cls == 'asset') assetFiles.add(rel);

      if (rel == 'src/app/layout.tsx' || rel == 'src/app/layout.jsx' || rel == 'app/layout.tsx' || rel == 'app/layout.jsx') {
        entryPoints.add('$rel (Root Layout)');
      } else if (rel == 'src/app/page.tsx' || rel == 'src/app/page.jsx' || rel == 'app/page.tsx' || rel == 'app/page.jsx') {
        entryPoints.add('$rel (Root Page)');
        routes['/'] = rel;
      } else if (rel == 'src/app/globals.css' || rel == 'app/globals.css') {
        entryPoints.add('$rel (Global Styles)');
      } else if (rel == 'lib/main.dart' || rel == 'main.py' || rel == 'src/index.ts' || rel == 'src/index.js') {
        entryPoints.add(rel);
      }

      // App router nested pages: e.g. src/app/about/page.tsx -> /about, src/app/projects/[slug]/page.tsx -> /projects/[slug]
      if ((rel.startsWith('src/app/') || rel.startsWith('app/')) &&
          (rel.endsWith('/page.tsx') || rel.endsWith('/page.jsx') || rel.endsWith('/page.js') || rel.endsWith('/page.ts'))) {
        final rawSeg = rel.replaceFirst('src/app/', '').replaceFirst('app/', '').replaceAll(RegExp(r'/page\.(tsx|jsx|js|ts)$'), '');
        final cleanSegments = rawSeg.split('/').where((s) => s.isNotEmpty && !(s.startsWith('(') && s.endsWith(')'))).toList();
        final routePath = cleanSegments.isEmpty ? '/' : '/${cleanSegments.join('/')}';
        routes[routePath] = rel;
      } else if ((rel.startsWith('src/app/') || rel.startsWith('app/')) &&
          (rel.endsWith('/route.ts') || rel.endsWith('/route.js'))) {
        final rawSeg = rel.replaceFirst('src/app/', '').replaceFirst('app/', '').replaceAll(RegExp(r'/route\.(ts|js)$'), '');
        final cleanSegments = rawSeg.split('/').where((s) => s.isNotEmpty && !(s.startsWith('(') && s.endsWith(')'))).toList();
        final routePath = cleanSegments.isEmpty ? '/' : '/${cleanSegments.join('/')}';
        routes[routePath] = rel;
      } else if ((rel.startsWith('pages/') || rel.startsWith('src/pages/')) &&
          (rel.endsWith('.tsx') || rel.endsWith('.jsx') || rel.endsWith('.js') || rel.endsWith('.ts'))) {
        final seg = rel.replaceFirst('src/pages/', '').replaceFirst('pages/', '').replaceAll(RegExp(r'\.(tsx|jsx|js|ts)$'), '');
        if (seg != '_app' && seg != '_document' && seg != '_error') {
          if (seg == 'index') {
            routes['/'] = rel;
          } else if (seg.endsWith('/index')) {
            routes['/${seg.substring(0, seg.length - 6)}'] = rel;
          } else {
            routes['/$seg'] = rel;
          }
        }
      }

      // Components
      if ((rel.startsWith('src/components/') || rel.startsWith('components/') || rel.startsWith('src/ui/') || rel.startsWith('ui/')) &&
          (rel.endsWith('.tsx') || rel.endsWith('.jsx') || rel.endsWith('.ts') || rel.endsWith('.js'))) {
        majorComponents.add(rel);
      }
    }

    // 5. Compute deterministic hashes for Snapshot
    final snapshotData = <String, dynamic>{};
    final fileRecords = <String, dynamic>{};

    for (final entry in meaningfulFiles.entries) {
      final rel = entry.key;
      final file = entry.value;
      try {
        final bytes = file.readAsBytesSync();
        final hash = _computeFileHash(bytes);
        final stat = file.statSync();

        snapshotData[rel] = {
          'hash': hash,
          'size': stat.size,
          'mtime': stat.modified.millisecondsSinceEpoch / 1000,
          'category': classifyFile(rel),
        };

        fileRecords[rel] = {
          'path': rel,
          'name': rel.split('/').last,
          'size': stat.size,
          'category': classifyFile(rel),
          'hash': hash,
          'last_audited': nowIso,
        };
      } catch (_) {}
    }

    // 6. Generate Semantic Architectural Project Map Markdown
    final md = StringBuffer();
    md.writeln('# Project Map: $projectName');
    md.writeln('');
    md.writeln('> Semantic architectural understanding of the workspace generated by AutonomOS Manager.');
    md.writeln('');

    md.writeln('## Identity');
    md.writeln('- **Project Name**: `$projectName`');
    md.writeln('- **Observed Project Type**: $projectType');
    md.writeln('- **Root Path**: `$rootPath`');
    md.writeln('- **Primary Architecture**: $architecture');
    md.writeln('- **Tracked Meaningful Files**: ${meaningfulFiles.length}');
    md.writeln('');

    md.writeln('## Current Technology');
    if (techStack.isNotEmpty) {
      for (final t in techStack) {
        md.writeln('- `$t`');
      }
    } else {
      md.writeln('- Standard Web / Software Stack');
    }
    final verifiedLibs = dependencies.keys.where((k) => allImportedModules.contains(k)).toList();
    if (verifiedLibs.isNotEmpty) {
      md.writeln('- **Verified Used Libraries**: ${verifiedLibs.map((v) => '`$v`').join(', ')}');
    }
    if (hasInstalled3D && !hasVerified3D) {
      md.writeln('- **Installed (Not yet imported in source code)**: Three.js / 3D rendering packages are present in `package.json` but not yet imported in active source files.');
    }
    md.writeln('');

    md.writeln('## Routes');
    if (routes.isNotEmpty) {
      for (final r in routes.entries) {
        md.writeln('- Route `${r.key}` -> `${r.value}`');
      }
    } else {
      md.writeln('- Standard Single Page / Application Entry');
    }
    md.writeln('');

    md.writeln('## Entry Points');
    if (entryPoints.isNotEmpty) {
      for (final ep in entryPoints) {
        md.writeln('- `$ep`');
      }
    } else {
      md.writeln('- None detected');
    }
    md.writeln('');

    md.writeln('## Architecture');
    md.writeln('The workspace is currently structured as a modern $projectType.');
    if (isNextJs) {
      md.writeln('It leverages Next.js App Router with React Server and Client Components, structured styling via Tailwind CSS, and modular UI components.');
    }
    md.writeln('');

    md.writeln('## Components');
    if (majorComponents.isNotEmpty) {
      for (final c in majorComponents.take(20)) {
        md.writeln('- `$c`');
      }
      if (majorComponents.length > 20) {
        md.writeln('- *(and ${majorComponents.length - 20} more component files)*');
      }
    } else {
      md.writeln('- Central page components in entry points');
    }
    md.writeln('');

    md.writeln('## Directory Structure');
    final topDirs = <String>{};
    for (final k in meaningfulFiles.keys) {
      final parts = k.split('/');
      if (parts.length > 1) {
        topDirs.add('${parts[0]}/');
      }
    }
    md.writeln('- **Source Directories**: ${topDirs.map((d) => '`$d`').join(', ')}');
    for (final d in topDirs.take(10)) {
      if (d == 'src/') {
        md.writeln('- `src/`: Core application source code, components, and routes');
      } else if (d == 'public/') {
        md.writeln('- `public/`: Static web assets, textures, models, and images');
      } else {
        md.writeln('- `$d`: Directory module');
      }
    }
    md.writeln('');

    md.writeln('## Assets');
    if (assetFiles.isNotEmpty) {
      for (final a in assetFiles.take(15)) {
        md.writeln('- `$a`');
      }
      if (assetFiles.length > 15) {
        md.writeln('- *(and ${assetFiles.length - 15} more static assets)*');
      }
    } else {
      md.writeln('- Static assets in `public/`');
    }
    md.writeln('');

    md.writeln('## Dependencies');
    if (dependencies.isNotEmpty) {
      md.writeln('### Core Runtime');
      for (final d in dependencies.entries.take(15)) {
        md.writeln('- `${d.key}`: `${d.value}`');
      }
    }
    if (devDependencies.isNotEmpty) {
      md.writeln('### Build & Development');
      for (final d in devDependencies.entries.take(10)) {
        md.writeln('- `${d.key}`: `${d.value}`');
      }
    }
    md.writeln('');

    md.writeln('## Configuration');
    if (configFiles.isNotEmpty) {
      for (final cfg in configFiles) {
        md.writeln('- `$cfg`');
      }
    } else {
      md.writeln('- Standard default configuration');
    }
    md.writeln('');

    md.writeln('## Runtime / Build Commands');
    if (scripts.isNotEmpty) {
      for (final s in scripts.entries) {
        md.writeln('- `npm run ${s.key}`: `${s.value}`');
      }
    } else {
      md.writeln('- `npm run dev`: Local Development');
      md.writeln('- `npm run build`: Production Build');
    }
    md.writeln('');

    md.writeln('## Important Relationships');
    if (isNextJs) {
      md.writeln('- Application layout (`src/app/layout.tsx`) wraps all pages and imports global styling (`src/app/globals.css`).');
      md.writeln('- Root page (`src/app/page.tsx`) renders the primary view and composes UI components.');
    } else {
      md.writeln('- Main entry point orchestrates core subsystems.');
    }
    md.writeln('');

    md.writeln('## Generated / Ignored Areas');
    md.writeln('- `.next/`: Next.js build cache and compiled server/client artifacts (excluded from Project Map).');
    md.writeln('- `node_modules/`: Vendor dependencies (managed by package manager).');
    md.writeln('- `.git/`: Version control metadata.');
    md.writeln('');

    md.writeln('## Requested Transformation');
    if (requestedObjective != null && requestedObjective.trim().isNotEmpty) {
      md.writeln('- **User Requested Objective**: "$requestedObjective"');
      md.writeln('- **Status**: PENDING IMPLEMENTATION (Workers Disabled / Paused)');
      md.writeln('- **Notice**: This requested transformation represents intended future work and is NOT part of current observed repository state.');
    } else {
      md.writeln('- No pending transformation requested.');
    }
    md.writeln('');

    md.writeln('## Evidence');
    md.writeln('- **Last Audited**: `$nowIso`');
    md.writeln('- **Meaningful File Count**: ${meaningfulFiles.length}');
    md.writeln('- **Snapshot File**: `.autonomos/last_snapshot.json`');
    md.writeln('- **Verification Confidence**: 100% deterministic filesystem inspection');

    final mdContent = md.toString();

    // 6. Write to Disk and Verify
    mapMdFile.writeAsStringSync(mdContent, flush: true);
    mapJsonFile.writeAsStringSync(json.encode({
      'version': '2.0',
      'project_name': projectName,
      'project_type': projectType,
      'root_path': rootPath,
      'last_audited': nowIso,
      'total_meaningful_files': meaningfulFiles.length,
      'tech_stack': techStack,
      'entry_points': entryPoints,
      'routes': routes,
      'components': majorComponents,
      'dependencies': dependencies,
      'devDependencies': devDependencies,
      'scripts': scripts,
      'files': fileRecords,
    }), flush: true);

    snapshotFile.writeAsStringSync(json.encode({
      'version': '2.0',
      'timestamp': nowIso,
      'file_count': meaningfulFiles.length,
      'files': snapshotData,
    }), flush: true);

    final verified = mapMdFile.existsSync() && snapshotFile.existsSync();

    return {
      'status': 'INITIALIZED',
      'project_name': projectName,
      'total_files': meaningfulFiles.length,
      'project_map_created': verified,
      'map_path': mapMdFile.path,
      'markdown': mdContent,
    };
  }

  static Future<Map<String, dynamic>> _performIncrementalAudit(
    Directory rootDir,
    File mapMdFile,
    File mapJsonFile,
    File snapshotFile, {
    String? requestedObjective,
  }) async {
    final rootPath = rootDir.path;
    final projectName = rootPath.split(Platform.pathSeparator).where((s) => s.isNotEmpty).last;
    final nowIso = DateTime.now().toUtc().toIso8601String();

    Map<String, dynamic> lastSnapshot = {};
    try {
      lastSnapshot = json.decode(snapshotFile.readAsStringSync())['files'] as Map<String, dynamic>? ?? {};
    } catch (_) {}

    final currentFiles = <String, File>{};

    void scanDir(Directory currentDir, String relPrefix) {
      for (final entity in currentDir.listSync(followLinks: false)) {
        final name = entity.uri.pathSegments.where((s) => s.isNotEmpty).last;
        if (ignoredDirs.contains(name)) continue;

        if (entity is Directory) {
          scanDir(entity, relPrefix.isEmpty ? name : '$relPrefix/$name');
        } else if (entity is File) {
          final relPath = relPrefix.isEmpty ? name : '$relPrefix/$name';
          currentFiles[relPath] = entity;
        }
      }
    }

    scanDir(rootDir, '');

    final added = <String>[];
    final modified = <String>[];
    final deleted = <String>[];
    final currentSnapshotData = <String, dynamic>{};

    for (final entry in currentFiles.entries) {
      final rel = entry.key;
      final file = entry.value;
      try {
        final bytes = file.readAsBytesSync();
        final hash = _computeFileHash(bytes);
        final stat = file.statSync();

        currentSnapshotData[rel] = {
          'hash': hash,
          'size': stat.size,
          'mtime': stat.modified.millisecondsSinceEpoch / 1000,
          'category': classifyFile(rel),
        };

        if (!lastSnapshot.containsKey(rel)) {
          added.add(rel);
        } else {
          final oldHash = lastSnapshot[rel]?['hash'];
          if (oldHash != hash) {
            modified.add(rel);
          }
        }
      } catch (_) {}
    }

    for (final oldKey in lastSnapshot.keys) {
      if (!currentFiles.containsKey(oldKey)) {
        deleted.add(oldKey);
      }
    }

    final hasChanges = added.isNotEmpty || modified.isNotEmpty || deleted.isNotEmpty;

    if (hasChanges) {
      // Re-run full semantic update when meaningful files changed
      return _performFullAudit(rootDir, mapMdFile, mapJsonFile, snapshotFile, requestedObjective: requestedObjective);
    } else {
      return {
        'status': 'UP_TO_DATE',
        'project_name': projectName,
        'total_files': currentFiles.length,
        'project_map_created': true,
        'map_path': mapMdFile.path,
      };
    }
  }
}
