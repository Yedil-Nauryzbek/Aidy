import os
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from aidy import intent_api


class ServiceLifecycleTests(unittest.TestCase):
    def test_child_has_independent_stdin_and_owned_cleanup(self):
        process = Mock()
        process.poll.return_value = None
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(os.environ, {"LOCALAPPDATA": tmp}), \
             patch.object(intent_api, "_owned_process", None), \
             patch.object(intent_api, "_api_is_healthy", side_effect=[False, True]), \
             patch.object(intent_api, "is_port_open", return_value=False), \
             patch.object(intent_api, "_resolve_api_dir", return_value=tmp), \
             patch.object(intent_api.subprocess, "Popen", return_value=process) as launch:
            self.assertTrue(intent_api.start_local_intent_api(tmp))
            self.assertEqual(launch.call_args.kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(launch.call_args.args[0][0], sys.executable)
            intent_api.stop_local_intent_api()
            intent_api.stop_local_intent_api()
            process.terminate.assert_called_once()

    def test_preexisting_service_is_not_owned_or_terminated(self):
        with patch.object(intent_api, "_owned_process", None), \
             patch.object(intent_api, "_api_is_healthy", return_value=True), \
             patch.object(intent_api.subprocess, "Popen") as launch:
            self.assertTrue(intent_api.start_local_intent_api("unused"))
            intent_api.stop_local_intent_api()
            launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
