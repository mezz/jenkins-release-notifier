from __future__ import annotations

import json
import unittest
from dataclasses import replace

from release_notifier.errors import UnsupportedSchemaError, ValidationError
from release_notifier.model import ReleaseRequest

from tests.support import request


class ReleaseRequestTest(unittest.TestCase):
    def test_plain_library_request_round_trips_canonically(self) -> None:
        release = request(links=False)

        restored = ReleaseRequest.from_json(release.to_json())

        self.assertEqual(release, restored)
        self.assertEqual([], json.loads(release.to_json())["releaseLinks"])
        self.assertNotIn("minecraftVersions", json.loads(release.to_json()))

    def test_minecraft_metadata_is_optional_presentation_data(self) -> None:
        release = request(metadata=True)

        value = release.to_dict()

        self.assertEqual(["1.21.1"], value["minecraftVersions"])
        self.assertEqual(["Fabric", "NeoForge"], value["modLoaders"])

    def test_custom_message_round_trips_as_normalized_markdown(self) -> None:
        release = request(message="  Released!\r\n\r\nThanks.  ")

        self.assertEqual("Released!\n\nThanks.", release.message)
        self.assertEqual("Released!\n\nThanks.", release.to_dict()["message"])

    def test_request_key_is_stable_for_repository_case_and_presentation_changes(self) -> None:
        first = request(repository="Mezz/Example", metadata=False)
        second = request(repository="mezz/example", metadata=True)

        self.assertEqual(first.request_key, second.request_key)
        self.assertNotEqual(first.to_json(), second.to_json())

    def test_rejects_unknown_schema_and_fields(self) -> None:
        value = request().to_dict()
        value["schemaVersion"] = 2
        with self.assertRaises(UnsupportedSchemaError):
            ReleaseRequest.from_dict(value)

        value["schemaVersion"] = 1
        value["token"] = "secret"
        with self.assertRaisesRegex(ValidationError, "unknown fields: token"):
            ReleaseRequest.from_dict(value)

    def test_rejects_short_commits_invalid_repository_and_credential_url(self) -> None:
        value = request().to_dict()
        value["baseCommit"] = "abc1234"
        with self.assertRaisesRegex(ValidationError, "full 40-character"):
            ReleaseRequest.from_dict(value)

        value = request().to_dict()
        value["repository"] = "not-a-repository"
        with self.assertRaisesRegex(ValidationError, "owner/name"):
            ReleaseRequest.from_dict(value)

        value = request().to_dict()
        value["releaseLinks"] = [{"label": "private", "url": "https://user:pass@example.com/x"}]
        with self.assertRaisesRegex(ValidationError, "must not contain credentials"):
            ReleaseRequest.from_dict(value)

        value["releaseLinks"] = [{"label": "private", "url": "https://example.com/x?access_token=secret"}]
        with self.assertRaisesRegex(ValidationError, "credential parameters"):
            ReleaseRequest.from_dict(value)

    def test_rejects_control_characters_and_duplicate_links(self) -> None:
        value = request().to_dict()
        value["channel"] = "main\nsecret"
        with self.assertRaisesRegex(ValidationError, "control"):
            ReleaseRequest.from_dict(value)

        value = request().to_dict()
        value["releaseLinks"] = [value["releaseLinks"][0], value["releaseLinks"][0]]
        with self.assertRaisesRegex(ValidationError, "duplicates"):
            ReleaseRequest.from_dict(value)

    def test_changed_base_keeps_identity_but_not_payload(self) -> None:
        release = request()
        changed = replace(release, base_commit="c" * 40)

        self.assertEqual(release.request_key, changed.request_key)
        self.assertNotEqual(release.to_json(), changed.to_json())


if __name__ == "__main__":
    unittest.main()
