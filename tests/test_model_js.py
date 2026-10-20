import subprocess
import unittest

from tests import util


@unittest.skipUnless(util.have("node"), "node is not installed")
class ModelJs(unittest.TestCase):
    def test_model_js(self):
        r = subprocess.run(["node", str(util.ROOT / "tests" / "model_test.js")], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("all assertions passed", r.stdout)


if __name__ == "__main__":
    unittest.main()
