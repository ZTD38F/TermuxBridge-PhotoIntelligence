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

    def test_current_tag_requires_version_bump(self):
        # A previous tag must never be republished with modified contents.
        workflow=(ROOT/".github/workflows/release.yml").read_text()
        self.assertIn('bump addon.json before publishing', workflow)
        self.assertIn('existing_tag="$(git ls-remote', workflow)

if __name__=="__main__":
    unittest.main()
