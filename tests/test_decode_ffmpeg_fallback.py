"""FFmpeg fallback is bounded and never touches source pictures.
Author: @dvkrum
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import subprocess
import unittest

from PIL import Image, UnidentifiedImageError
import gallery_decode


class DecodeFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "private-state"
        self.config_patch = patch.object(gallery_decode, "STATE_DIR", self.state)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

    def test_regular_png_uses_pillow_only(self):
        source = self.root / "source.png"
        Image.new("RGB", (12, 9), (14, 76, 190)).save(source)
        with patch.object(gallery_decode.subprocess, "run") as runner:
            with gallery_decode.open_image(Image, source) as image:
                self.assertEqual(image.size, (12, 9))
            runner.assert_not_called()
        with Image.open(source) as image:
            self.assertEqual(image.size, (12, 9))

    def test_zip_disguised_as_webp_never_runs_ffmpeg(self):
        source = self.root / "fake.webp"
        before = b"PK\x03\x04" + b"not-an-image" * 5
        source.write_bytes(before)
        with patch.object(gallery_decode.subprocess, "run") as runner:
            with self.assertRaises(UnidentifiedImageError):
                with gallery_decode.open_image(Image, source):
                    pass
            runner.assert_not_called()
        self.assertEqual(source.read_bytes(), before)

    def test_heic_dng_trigger_preview_without_modifying_source(self):
        cases = [
            ("sample.heic", b"\x00\x00\x00\x18ftypheicxxxx"),
            ("sample.dng", b"II*\x00\x08\x00\x00\x00raw-synthetic"),
        ]
        for name, signature in cases:
            with self.subTest(name=name):
                original = self.root / name
                original.write_bytes(signature)
                def fake_ffmpeg(command, **kwargs):
                    self.assertEqual(kwargs["timeout"], gallery_decode.FFMPEG_SECONDS)
                    self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
                    self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
                    self.assertTrue(any("force_original_aspect_ratio=decrease" in x for x in command))
                    Image.new("RGB", (30, 20), (20, 60, 99)).save(command[-1])
                    return subprocess.CompletedProcess(command, 0)
                with patch.object(gallery_decode.subprocess, "run", side_effect=fake_ffmpeg) as runner:
                    with gallery_decode.open_image(Image, original) as decoded:
                        self.assertEqual(decoded.size, (30, 20))
                        self.assertEqual(decoded.mode, "RGB")
                    runner.assert_called_once()
                self.assertEqual(original.read_bytes(), signature)
                self.assertEqual(list(self.state.glob(".decode-preview-*")), [])

    def test_failed_conversion_is_not_a_success(self):
        original = self.root / "broken.heic"
        original.write_bytes(b"\x00\x00\x00\x18ftypheic")
        with patch.object(gallery_decode.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaises(UnidentifiedImageError):
                with gallery_decode.open_image(Image, original):
                    pass
        self.assertEqual(list(self.state.glob(".decode-preview-*")), [])

    def test_wrong_magic_never_uses_ffmpeg(self):
        original = self.root / "wrong.heic"
        original.write_bytes(b"PK\x03\x04archive-not-photo")
        with patch.object(gallery_decode.subprocess, "run") as runner:
            with self.assertRaises(UnidentifiedImageError):
                with gallery_decode.open_image(Image, original):
                    pass
            runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
