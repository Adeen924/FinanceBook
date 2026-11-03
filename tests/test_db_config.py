import json
import os
import shutil
import tempfile
import unittest

import tests  # noqa: F401
import db_config


class ForgetDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._orig = db_config.SETTINGS_PATH
        db_config.SETTINGS_PATH = os.path.join(self.dir, "app_settings.json")
        self.a = os.path.join(self.dir, "a.db")
        self.b = os.path.join(self.dir, "b.db")
        for p in (self.a, self.b):
            open(p, "w").close()
        db_config.register_database(self.a)
        db_config.register_database(self.b, make_active=True)

    def tearDown(self):
        db_config.SETTINGS_PATH = self._orig
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_forgetting_active_switches_to_another(self):
        active = db_config.forget_database(self.b)
        self.assertEqual(active, self.a)
        self.assertEqual(db_config.get_known_databases(), [self.a])

    def test_forgetting_other_keeps_active_and_file(self):
        self.assertEqual(db_config.forget_database(self.a), self.b)
        self.assertTrue(os.path.exists(self.a))
        with open(db_config.SETTINGS_PATH) as f:
            self.assertNotIn(self.a, json.load(f)["known_databases"])

    def test_forgetting_last_leaves_no_active(self):
        db_config.forget_database(self.a)
        self.assertIsNone(db_config.forget_database(self.b))


if __name__ == "__main__":
    unittest.main()
