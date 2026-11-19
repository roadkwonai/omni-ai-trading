import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import local_env


class EnvFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / 'repo'
        self.repo.mkdir()
        patcher = mock.patch.object(local_env, 'REPO', self.repo)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('VIBE_TRADING_ENV_FILE', None)

    def test_repo_env_wins_over_legacy_envrc(self):
        (self.repo.parent / '.envrc').write_text('APP_KEY=old\n')
        self.assertEqual(local_env.env_file(), self.repo.parent / '.envrc')
        (self.repo / '.env').write_text('APP_KEY=new\n')
        self.assertEqual(local_env.env_file(), self.repo / '.env')

    def test_override_and_missing(self):
        self.assertEqual(local_env.env_file(), self.repo / '.env')
        os.environ['VIBE_TRADING_ENV_FILE'] = '~/elsewhere.env'
        self.assertEqual(local_env.env_file(), Path.home() / 'elsewhere.env')


if __name__ == '__main__':
    unittest.main()

