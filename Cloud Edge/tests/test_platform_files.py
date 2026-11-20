import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cloud_edge.launcher import acquire_instance_lock, run_with_clients
from cloud_edge.oauth_store import FileTokenStorage
from cloud_edge.device_store import DeviceStore, CreateJournal


class PlatformFileTests(unittest.TestCase):
    def test_lock_contends_across_processes_and_releases_on_exit(self):
        script = '''
import sys
from pathlib import Path
from cloud_edge.launcher import acquire_instance_lock
try:
    fd = acquire_instance_lock(Path(sys.argv[1]))
except BlockingIOError:
    sys.exit(3)
# Deliberately leave fd open: process exit must release it.
'''
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'private' / 'edge.lock'
            def child():
                return subprocess.run([sys.executable, '-c', script, str(path)],
                                      capture_output=True, text=True, timeout=15)
            fd = acquire_instance_lock(path)
            try:
                result = child()
                self.assertEqual(result.returncode, 3, result.stderr)
            finally:
                os.close(fd)
            for _ in range(2):
                result = child()
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_runtime_failure_releases_lock(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'private' / 'edge.lock'
            def fail():
                raise RuntimeError('test')
            with self.assertRaises(RuntimeError):
                run_with_clients(fail, object(), [], instance_lock=path)
            os.close(acquire_instance_lock(path))

    def test_stores_and_lock_reject_symlink_paths(self):
        # Mock the path check so Windows does not require symlink privileges.
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'private' / 'state'
            with patch.object(Path, 'is_symlink', return_value=True):
                for read in (DeviceStore(path).load, CreateJournal(path).load,
                             FileTokenStorage(path, None, None)._read,
                             lambda: acquire_instance_lock(path)):
                    with self.subTest(read=read), self.assertRaises(PermissionError):
                        read()
