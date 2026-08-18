from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from release_notifier.cli import main

class CliTest(unittest.TestCase):
    def test_jenkins_parameters_and_build_description_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / "workspace" / "state.json"
            common_state_args = [
                "--state-file",
                str(state_file),
            ]
            with patch.dict(os.environ, {"WORKSPACE": temporary_directory}, clear=False):
                self.assertEqual(0, main(["init", *common_state_args]))
                values = {
                    "RELEASE_REPOSITORY": "mezz/Example",
                    "RELEASE_CHANNEL": "main",
                    "RELEASE_PROJECT_NAME": "Example",
                    "RELEASE_VERSION": "1.2.3",
                    "RELEASE_BASE_COMMIT": "a" * 40,
                    "RELEASE_HEAD_COMMIT": "b" * 40,
                    "RELEASE_MESSAGE": "Version 1.2.3 is available.\n\nThank you!",
                    "RELEASE_LINK_LABELS": "Maven\nModrinth",
                    "RELEASE_LINK_URLS": "https://example.invalid/maven\nhttps://example.invalid/modrinth",
                    "RELEASE_MINECRAFT_VERSIONS": "1.21.1",
                    "RELEASE_MOD_LOADERS": "Fabric\nNeoForge",
                    "RELEASE_ENHANCEMENT_LABELS_PRESENT": "false",
                }
                with patch.dict(os.environ, values, clear=False):
                    self.assertEqual(
                        0, main(["submit-parameters", *common_state_args])
                    )
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(0, main(["describe-state", *common_state_args]))

                description_file = Path(temporary_directory) / "description.txt"
                description_file.write_text(output.getvalue(), encoding="utf-8")
                restored = Path(temporary_directory) / "next-workspace" / "state.json"
                self.assertEqual(
                    0,
                    main(
                        [
                            "restore-state",
                            "--state-file",
                            str(restored),
                            "--description-file",
                            str(description_file),
                        ]
                    ),
                )

            request_value = json.loads(restored.read_text(encoding="utf-8"))["channels"][0]["requests"][0]["request"]
            self.assertEqual("1.2.3", request_value["version"])
            self.assertEqual(
                "Version 1.2.3 is available.\n\nThank you!", request_value["message"]
            )
            self.assertEqual(["1.21.1"], request_value["minecraftVersions"])
            self.assertEqual(["Fabric", "NeoForge"], request_value["modLoaders"])
            self.assertEqual(2, len(request_value["releaseLinks"]))


if __name__ == "__main__":
    unittest.main()
