import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "umalauncher"))
from carrotjuicer import CarrotJuicer


class SkillSimulationStreamTests(unittest.TestCase):
    preview = dict(type="progress", completed=250, total=2000, candidates={"10": 0.4}, evaluations={})
    final = dict(baselineStats={}, candidates={"10": {"bashinGainedStats": {"mean": 0.5}}})

    def setUp(self):
        self.owner = CarrotJuicer.__new__(CarrotJuicer)
        self.owner._skill_sim_condition = threading.Condition()
        self.owner._skill_sim_generation = 1
        self.owner._skill_sim_stop = self.owner._skill_sim_cancel_requested = False
        self.owner._skill_sim_process = self.owner._skill_sim_update = None

    def run_child(self, script, callback):
        # Exercise live pipe reads and process termination using an actual child.
        popen = subprocess.Popen
        def launch(command, **kwargs):
            self.assertTrue(json.loads(command[1])["streamProgress"])
            return popen([sys.executable, "-u", "-c", script], **kwargs)
        with mock.patch("carrotjuicer.subprocess.Popen", side_effect=launch):
            return self.owner.run_simulation("test-sim", {"streamProgress": True},
                                             expected_generation=1, on_progress=callback)

    def test_preview_arrives_before_final_and_stderr_cannot_block_it(self):
        with tempfile.TemporaryDirectory() as directory:
            ack = Path(directory) / "preview-received"
            updates = []
            def receive(message):
                updates.append(message)
                ack.touch()
            script = f"""
import os, sys, time
sys.stderr.write('condition skipped\\n' * 20000)
sys.stderr.flush()
print({json.dumps(self.preview)!r}, flush=True)
deadline = time.monotonic() + 4
while not os.path.exists({str(ack)!r}) and time.monotonic() < deadline:
    time.sleep(0.01)
assert os.path.exists({str(ack)!r})
print({json.dumps(self.final)!r})
"""
            self.assertEqual(self.run_child(script, receive), self.final)
            self.assertEqual(updates, [self.preview])

    def test_old_binary_can_emit_only_final(self):
        receive = mock.Mock()
        self.assertEqual(self.run_child(f"print({json.dumps(self.final)!r})", receive), self.final)
        receive.assert_not_called()

    def test_stop_during_preview_discards_partial_run(self):
        def stop_run(message):
            self.owner._publish_skill_progress(1, "race", message)
            with self.owner._skill_sim_condition:
                self.owner._invalidate_skill_simulation_locked()
        script = f"import time; print({json.dumps(self.preview)!r}, flush=True); time.sleep(4)"
        self.assertEqual(self.run_child(script, stop_run), {})
        self.owner._publish_skill_progress(1, "race", self.preview)
        self.assertIsNone(self.owner._take_skill_simulation_update())

    def test_preview_delivery_is_scoped_and_separate_from_final(self):
        self.owner.skill_browser = mock.Mock()
        self.owner._skill_window_requested_snapshot = dict(pageId="tab", selectionVersion=2)
        self.owner._skill_window_requested_key = "race"
        selection = dict(pageId="tab", mode="ace", version=2)
        update = (1, "race", self.preview)
        send = self.owner.skill_browser.execute_script
        for obsolete in (dict(selection, pageId="old"), dict(selection, version=1), dict(selection, mode="rating")):
            self.assertFalse(self.owner._show_skill_simulation_update(update, obsolete))
        self.assertFalse(self.owner._show_skill_simulation_update((0, "race", self.preview), selection))
        self.assertFalse(self.owner._show_skill_simulation_update((1, "old", self.preview), selection))
        send.assert_not_called()
        self.assertFalse(self.owner._show_skill_simulation_update(update, selection))
        bridge, snapshot = send.call_args.args
        self.assertIn("loadLauncherProgress", bridge)
        self.assertEqual(snapshot, dict(pageId="tab", selectionVersion=2, runGeneration=1, progress=self.preview))
        send.return_value = True
        self.assertTrue(self.owner._show_skill_simulation_update((1, "race", self.final), selection))
        bridge, snapshot = send.call_args.args
        self.assertIn("loadLauncherData", bridge)
        self.assertEqual(snapshot["results"], self.final)
        self.assertNotIn("progress", snapshot)
