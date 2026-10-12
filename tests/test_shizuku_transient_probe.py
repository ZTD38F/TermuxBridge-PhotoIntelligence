"""Shizuku transient probes must be bounded and avoid source mutations.
Author: @dvkrum.
"""
import subprocess
import unittest
from unittest.mock import patch
import gallery_shizuku as shizuku

def response(rc):
    return subprocess.CompletedProcess(["rish"],rc)

class ShizukuTransientTests(unittest.TestCase):
    def setUp(self):
        self.old=(shizuku._AVAILABILITY_CHECKED_AT,shizuku._AVAILABILITY_VALUE)
        self.addCleanup(self.restore)
        shizuku._AVAILABILITY_CHECKED_AT=0
        shizuku._AVAILABILITY_VALUE=False
    def restore(self):
        shizuku._AVAILABILITY_CHECKED_AT,shizuku._AVAILABILITY_VALUE=self.old

    def test_transient_timeout_after_prior_success_recovers(self):
        shizuku._AVAILABILITY_VALUE=True
        with patch.object(shizuku,'_run',side_effect=[subprocess.TimeoutExpired(["rish"],3),response(0)]) as run,patch.object(shizuku.time,'sleep') as sleep:
            self.assertTrue(shizuku.available_cached(ttl=30))
            self.assertEqual(run.call_count,2)
            self.assertEqual(run.call_args.kwargs["timeout"],5.)
            sleep.assert_called_once()
            self.assertTrue(shizuku._AVAILABILITY_VALUE)

    def test_verified_unavailability_after_two_failures(self):
        shizuku._AVAILABILITY_VALUE=True
        with patch.object(shizuku,'_run',side_effect=[response(1),response(1)]) as run,patch.object(shizuku.time,'sleep'):
            self.assertFalse(shizuku.available_cached())
            self.assertEqual(run.call_count,2)

    def test_cold_unavailable_uses_one_probe(self):
        with patch.object(shizuku,'_run',return_value=response(1)) as run:
            self.assertFalse(shizuku.available_cached())
            run.assert_called_once()

    def test_force_bypasses_retry_and_cache(self):
        shizuku._AVAILABILITY_VALUE=True
        with patch.object(shizuku,'_run',return_value=response(1)) as run:
            self.assertFalse(shizuku.available_cached(force=True,timeout=8))
            self.assertEqual(run.call_count,1)
            self.assertEqual(run.call_args.kwargs["timeout"],8.0)

    def test_ready_cached_does_not_spawn_process(self):
        shizuku._AVAILABILITY_CHECKED_AT=shizuku.time.monotonic()
        shizuku._AVAILABILITY_VALUE=True
        with patch.object(shizuku,'_run') as run:
            self.assertTrue(shizuku.available_cached(ttl=30))
            run.assert_not_called()

if __name__ == '__main__':
    unittest.main()
