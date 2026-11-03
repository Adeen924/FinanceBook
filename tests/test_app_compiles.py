"""Every app module must compile (and with Python 3.12's grammar — the frozen
launcher's runtime), and the tax-tables data file must be valid JSON."""
import ast
import glob
import json
import os
import unittest

import tests


class CompileTests(unittest.TestCase):
    def test_all_modules_parse_for_python_312(self):
        files = glob.glob(os.path.join(tests.APP_DIR, "**", "*.py"), recursive=True)
        self.assertGreater(len(files), 40)
        for path in files:
            with open(path, encoding="utf-8") as f:
                src = f.read()
            with self.subTest(path=os.path.relpath(path, tests.APP_DIR)):
                ast.parse(src, path, feature_version=(3, 12))
                compile(src, path, "exec")

    def test_tax_tables_json(self):
        for path in glob.glob(os.path.join(tests.APP_DIR, "data", "tax_tables_*.json")):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertIn("social_security", data)


if __name__ == "__main__":
    unittest.main()
