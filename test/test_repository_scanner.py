import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from core.workspace.filesystem import ControlledWorkspaceFS
from core.workspace.scanner import RepositoryScanner, RepositoryIndex, FileMetadata


class TestRepositoryScanner(unittest.TestCase):
    """
    Focused unit tests for the Manager's deterministic repository scanner.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="autonomos_test_scanner_")
        self.workspace_root = Path(self.temp_dir) / "test_repo"
        self.workspace_root.mkdir()
        self.fs = ControlledWorkspaceFS(self.workspace_root)
        self.scanner = RepositoryScanner(self.fs)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def _populate_mock_repository(self):
        """Creates a realistic multi-language repository layout."""
        # 1. Manifests & Configs
        self.fs.create_file("package.json", json.dumps({
            "name": "my-web-app",
            "main": "src/index.ts",
            "dependencies": {"react": "^18.2.0", "next": "^14.0.0", "three": "^0.160.0"}
        }))
        self.fs.create_file("pyproject.toml", '[tool.poetry.dependencies]\nfastapi = "^0.100.0"')
        self.fs.create_file("tsconfig.json", '{"compilerOptions": {}}')
        self.fs.create_file("Makefile", "build:\n\techo building")

        # 2. Source Code
        self.fs.create_file("src/index.ts", """
import React from 'react';
import { Scene } from 'three';

export interface AppProps {
  title: string;
}

export function mainApp(props: AppProps) {
  // TODO: connect 3D canvas
  return props.title;
}
""")

        self.fs.create_file("backend/app.py", """
# FastAPI application entrypoint
from fastapi import FastAPI
import os

class AuthManager:
    def login(self, username: str):
        pass

app = FastAPI()

def start_server():
    # FIXME: add SSL certificate check
    pass
""")

        # 3. Tests
        self.fs.create_file("tests/test_auth.py", """
import unittest
class TestAuth(unittest.TestCase):
    def test_login(self):
        pass
""")
        self.fs.create_file("src/__tests__/app.spec.ts", "describe('App', () => {});")

        # 4. Documentation
        self.fs.create_file("README.md", "# My Project\nDocumentation here.")
        self.fs.create_file("docs/architecture.md", "# Architecture Guide")

        # 5. Scripts
        self.fs.create_file("scripts/deploy.sh", "#!/bin/bash\necho deploy")

        # 6. Assets
        self.fs.create_file("assets/styles.css", "body { margin: 0; }")
        self.fs.create_file("config.yaml", "env: production")

        # 7. Ignored directories (must NOT be scanned)
        (self.workspace_root / "node_modules" / "react").mkdir(parents=True, exist_ok=True)
        (self.workspace_root / "node_modules" / "react" / "index.js").write_text("module.exports = {}")

        (self.workspace_root / "build" / "static").mkdir(parents=True, exist_ok=True)
        (self.workspace_root / "build" / "static" / "bundle.js").write_text("var x = 1;")

        (self.workspace_root / "__pycache__").mkdir(parents=True, exist_ok=True)
        (self.workspace_root / "__pycache__" / "app.cpython-311.pyc").write_bytes(b"\x00\x01\x02")

        (self.workspace_root / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
        (self.workspace_root / ".venv" / "bin" / "python").write_text("#!/bin/sh")

    def test_deterministic_scan_inventory(self):
        """Verify that scanner builds complete deterministic inventory of all files & directories."""
        self._populate_mock_repository()

        index = self.scanner.scan_repository()

        self.assertIsInstance(index, RepositoryIndex)
        self.assertGreaterEqual(index.total_files, 10)
        self.assertGreaterEqual(index.total_directories, 5)
        self.assertGreater(index.total_size_bytes, 0)

        # Ensure ignored build dirs are not in files
        for path in index.files.keys():
            self.assertFalse(path.startswith("node_modules/"))
            self.assertFalse(path.startswith("build/"))
            self.assertFalse(path.startswith("__pycache__/"))
            self.assertFalse(path.startswith(".venv/"))

    def test_file_categorization_and_metadata(self):
        """Verify accurate file categorization and symbol/import extraction."""
        self._populate_mock_repository()

        index = self.scanner.scan_repository()

        # Check manifest
        pkg_meta = index.files.get("package.json")
        self.assertIsNotNone(pkg_meta)
        self.assertEqual(pkg_meta.category, "manifest_config")

        # Check source file (TypeScript)
        ts_meta = index.files.get("src/index.ts")
        self.assertIsNotNone(ts_meta)
        self.assertEqual(ts_meta.category, "source")
        self.assertIn("interface AppProps", ts_meta.symbols)
        self.assertIn("fn mainApp", ts_meta.symbols)
        self.assertIn("react", ts_meta.imports)
        self.assertIn("three", ts_meta.imports)
        self.assertTrue(any("TODO" in td for td in ts_meta.todos))

        # Check source file (Python)
        py_meta = index.files.get("backend/app.py")
        self.assertIsNotNone(py_meta)
        self.assertEqual(py_meta.category, "source")
        self.assertIn("class AuthManager", py_meta.symbols)
        self.assertIn("def start_server", py_meta.symbols)
        self.assertIn("fastapi", py_meta.imports)
        self.assertTrue(any("FIXME" in td for td in py_meta.todos))

        # Check test file
        test_meta = index.files.get("tests/test_auth.py")
        self.assertIsNotNone(test_meta)
        self.assertEqual(test_meta.category, "test")

        # Check doc and script
        doc_meta = index.files.get("README.md")
        self.assertEqual(doc_meta.category, "documentation")

        script_meta = index.files.get("scripts/deploy.sh")
        self.assertEqual(script_meta.category, "script")

    def test_directory_classification_and_entrypoints(self):
        """Verify identification of source dirs, test dirs, doc dirs, and entry points."""
        self._populate_mock_repository()

        index = self.scanner.scan_repository()

        self.assertIn("src", index.source_directories)
        self.assertIn("backend", index.source_directories)
        self.assertIn("tests", index.test_directories)
        self.assertIn("docs", index.doc_directories)
        self.assertIn("scripts", index.script_directories)

        # Tech stack & entry points
        self.assertIn("React", index.tech_stack["frameworks"])
        self.assertIn("Next.js", index.tech_stack["frameworks"])
        self.assertIn("FastAPI", index.tech_stack["frameworks"])
        self.assertIn("backend/app.py", index.entry_points)
        self.assertIn("src/index.ts", index.entry_points)

    def test_graceful_handling_of_malformed_or_unreadable_files(self):
        """Verify scanner never crashes on syntax errors or unusual characters."""
        # Create a Python file with severe syntax error
        self.fs.create_file("bad_syntax.py", "def broken_func( ::: syntax error !@#$%^&*()")
        # Create a binary-like file with invalid utf-8 bytes
        bad_bytes_path = self.workspace_root / "corrupted.py"
        bad_bytes_path.write_bytes(b"\xff\xfe\x00\x00\x80\x90\xff")

        index = self.scanner.scan_repository()

        self.assertIn("bad_syntax.py", index.files)
        self.assertIn("corrupted.py", index.files)
        # Verify scan finished cleanly
        self.assertGreater(index.total_files, 0)

    def test_repository_index_serialization_and_querying(self):
        """Verify RepositoryIndex JSON serialization and query_files filtering."""
        self._populate_mock_repository()

        index = self.scanner.scan_repository()
        data = index.to_dict()

        restored = RepositoryIndex.from_dict(data)
        self.assertEqual(restored.total_files, index.total_files)
        self.assertEqual(restored.workspace_root, index.workspace_root)

        # Query files by category
        source_files = restored.query_files(category="source")
        self.assertTrue(all(f.category == "source" for f in source_files))

        # Query files by extension
        py_files = restored.query_files(extension=".py")
        self.assertTrue(all(f.extension == ".py" for f in py_files))

        # Query files by path prefix
        src_files = restored.query_files(path_prefix="src/")
        self.assertTrue(all(f.path.startswith("src/") for f in src_files))


if __name__ == "__main__":
    unittest.main()
