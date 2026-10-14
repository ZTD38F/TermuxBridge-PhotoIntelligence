"""Release pipeline guards versioned PhotoIntelligence archives from mutation.
Author: @dvkrum
"""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]

class ReleaseIntegrityTests(unittest.TestCase):
    def test_republish_requires_new_version(self):
        workflow=(ROOT/".github/workflows/release.yml").read_text()
        self.assertIn("photo-intelligence-stable-release", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("git ls-remote --tags --refs origin", workflow)
        self.assertIn('if [[ -n "$existing_tag" ]]', workflow)
        self.assertIn('if gh release view "$TAG"', workflow)
        self.assertIn('--target "$GITHUB_SHA"', workflow)
        self.assertNotIn("--clobber", workflow)
        self.assertNotIn("gh release upload", workflow)
        self.assertIn("Validate exact release commit", workflow)
        self.assertIn("python -m unittest discover -s tests -v", workflow)
        self.assertLess(workflow.index("Validate exact release commit"),
                        workflow.index("Build release"))
        self.assertLess(workflow.index("Build release"),
                        workflow.index("Publish"))

    def test_release_package_is_tracked_only_and_state_free(self):
        workflow=(ROOT/".github/workflows/release.yml").read_text()
        self.assertIn('git archive --format=tar "$GITHUB_SHA"',workflow)
        self.assertIn('test ! -e dist/package/.release-test-home',workflow)
        self.assertIn('test ! -d dist/package/__pycache__',workflow)
        self.assertNotIn('rsync -a --exclude .git --exclude dist',workflow)

    def test_git_archive_discards_synthetic_untracked_gallery_state(self):
        import io,subprocess,tarfile,tempfile
        # This verifies the real archive command using this test checkout.
        with tempfile.TemporaryDirectory() as td:
            fake=ROOT/'.release-test-home'
            # Do not mutate the live tree. Test the behavior in a tiny fresh
            # isolated repository with the same actual command shape.
            from pathlib import Path
            t=Path(td)
            subprocess.run(["git","init","-q",str(t)],check=True)
            (t/"addon.json").write_text('{"name":"safe"}')
            subprocess.run(["git","-C",str(t),"add","addon.json"],check=True)
            subprocess.run(["git","-C",str(t),"-c","user.name=CI",
                            "-c","user.email=ci@example.invalid",
                            "commit","-qm","baseline"],check=True)
            (t/".release-test-home/state").mkdir(parents=True)
            (t/".release-test-home/state/gallery.sqlite3").write_bytes(b"TEST_STATE")
            (t/"__pycache__").mkdir()
            (t/"__pycache__/secret.pyc").write_bytes(b"TEST_CACHE")
            raw=subprocess.check_output(["git","-C",str(t),"archive","--format=tar","HEAD"])
            with tarfile.open(fileobj=io.BytesIO(raw)) as arc:
                names=arc.getnames()
            self.assertIn("addon.json",names)
            self.assertFalse(any("release-test-home" in n or
                                 "__pycache__" in n for n in names),names)

    def test_current_tag_requires_version_bump(self):
        # A previous tag must never be republished with modified contents.
        workflow=(ROOT/".github/workflows/release.yml").read_text()
        self.assertIn('bump addon.json before publishing', workflow)
        self.assertIn('existing_tag="$(git ls-remote', workflow)

if __name__=="__main__":
    unittest.main()
