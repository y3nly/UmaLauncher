"""DOM regressions, without Chrome.

Run npm install --prefix venv/dom-tests --no-package-lock jsdom@31.0.1,
then set NODE_PATH to the absolute venv/dom-tests/node_modules directory.
"""
import ast
import json
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GameToraDOMTests(unittest.TestCase):
    def test_live_and_legacy_event_layouts(self):
        if not shutil.which("node"):
            self.skipTest("Node.js is required for DOM tests")
        available = subprocess.run(
            ["node", "-e", "require('jsdom')"], capture_output=True,
        )
        if available.returncode:
            self.skipTest("Install jsdom and set NODE_PATH to its node_modules directory")
        tree = ast.parse((ROOT / "umalauncher/carrotjuicer.py").read_text(encoding="utf-8"))
        scripts = [node.value for node in ast.walk(tree)
                   if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        focus = next(script for script in scripts if "state.extract = () =>" in script)
        extraction = focus.split("state.extract = () =>", 1)[1].split("state.ensureOpen =", 1)[0]
        search = next(script for script in scripts if "const eventEntries =" in script)
        configure = next(script for script in scripts if "const hiddenSources =" in script)
        helper = (ROOT / "umalauncher/_assets/training_helper/index.html").read_text(encoding="utf-8")
        tokenizer = helper.split("function appendSemanticTokens", 1)[1].split("function appendSemanticText", 1)[0]
        result = subprocess.run(
            ["node", str(ROOT / "tests/gametora_dom.cjs")],
            input=json.dumps({"extraction": extraction, "search": search,
                              "configure": configure, "tokenizer": tokenizer}),
            text=True, capture_output=True, encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
