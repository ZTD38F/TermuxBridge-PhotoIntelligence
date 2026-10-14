"""Keep addon release version and runtime version identical.
Author: @dvkrum
"""
import ast
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class ReleaseVersionAlignmentTests(unittest.TestCase):
    def test_runtime_manifest_version_and_metadata(self):
        info=json.loads((ROOT/"addon.json").read_text())
        tree=ast.parse((ROOT/"runtime/photo_config.py").read_text())
        assignments={name.id:stmt.value.value for stmt in tree.body
                     if isinstance(stmt,ast.Assign)
                     for name in stmt.targets
                     if isinstance(name,ast.Name)
                     and isinstance(stmt.value,ast.Constant)}
        self.assertEqual(info["version"],assignments["ADDON_VERSION"])
        self.assertEqual(info["version"],"0.6.6")
        self.assertEqual(info["author"],"@dvkrum")

    def test_database_migration_not_silently_bumped(self):
        tree=ast.parse((ROOT/"runtime/db_migrations.py").read_text())
        versions=[s.value.value for s in tree.body
                  if isinstance(s,ast.Assign) and
                  any(isinstance(t,ast.Name) and t.id=="TARGET_VERSION" for t in s.targets)
                  and isinstance(s.value,ast.Constant)]
        self.assertEqual(versions,[4])

if __name__=="__main__":
    unittest.main()
