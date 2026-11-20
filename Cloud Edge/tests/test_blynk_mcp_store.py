import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest

from cloud_edge.oauth_store import FileTokenStorage


class FakeModel:
    def __init__(self, value):
        self.value = value

    def model_dump(self, mode):
        assert mode == "json"
        return self.value

    @classmethod
    def model_validate(cls, value):
        return cls(value)


class StoreTests(unittest.TestCase):
    def test_persists_both_records_privately_across_instances(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "private" / "oauth.json"
            first = FileTokenStorage(path, FakeModel, FakeModel)
            asyncio.run(first.set_client_info(FakeModel({"client_id": "test"})))
            asyncio.run(first.set_tokens(FakeModel({"access_token": "secret", "expires_in": 10})))
            second = FileTokenStorage(path, FakeModel, FakeModel)
            self.assertEqual(asyncio.run(second.get_client_info()).value["client_id"], "test")
            self.assertEqual(asyncio.run(second.get_tokens()).value["access_token"], "secret")
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertIsNotNone(second.seconds_until_expiry())

    @unittest.skipIf(os.name == "nt", "POSIX permission bits only")
    def test_rejects_public_or_symlink_store(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "private"
            directory.mkdir(mode=0o700)
            path = directory / "oauth.json"
            path.write_text(json.dumps({"tokens": {"access_token": "secret"}}))
            os.chmod(path, 0o644)
            store = FileTokenStorage(path, FakeModel, FakeModel)
            with self.assertRaises(PermissionError):
                asyncio.run(store.get_tokens())
            os.chmod(path, 0o600)
            link = directory / "link.json"
            link.symlink_to(path)
            with self.assertRaises(PermissionError):
                asyncio.run(FileTokenStorage(link, FakeModel, FakeModel).get_tokens())


if __name__ == "__main__":
    unittest.main()
