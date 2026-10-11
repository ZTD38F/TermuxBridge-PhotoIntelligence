"""E2E regression: launch PhotoIntelligence only in a synthetic isolated workspace.
Author: @dvkrum

Never uses Android storage, real app state, real bridge ports, or live services.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]


class IsolatedServiceTests(unittest.TestCase):
    def test_supervisor_dashboard_and_gallery_on_synthetic_photo(self):
        with tempfile.TemporaryDirectory(prefix="photo-e2e-") as tmp:
            stage=Path(tmp)
            state=stage/"state"
            logs=stage/"logs"
            shared=stage/"shared"
            (shared/"DCIM/Camera").mkdir(parents=True)
            state.mkdir()
            logs.mkdir()
            Image.new("RGB",(42,38),(54,122,167)).save(
                shared/"DCIM/Camera/synthetic.jpg")
            settings={"profile":"balanced","watch_local":False,"auto_scan":True,
                      "auto_ocr":False,"auto_metadata":False,"auto_visual":False,
                      "auto_semantic":False,"auto_duplicates":False,
                      "auto_taxonomy":False}
            (state/"settings.json").write_text(json.dumps(settings))
            with socket.socket() as sock:
                sock.bind(("127.0.0.1",0))
                port=sock.getsockname()[1]
            env=os.environ.copy()
            env.update({
                "PHOTO_INTELLIGENCE_HOME":str(stage),
                "PHOTO_INTELLIGENCE_STATE":str(state),
                "PHOTO_INTELLIGENCE_LOGS":str(logs),
                "PHOTO_INTELLIGENCE_CURRENT":str(ROOT),
                "PHOTO_INTELLIGENCE_SHARED_ROOT":str(shared),
                "PHOTO_INTELLIGENCE_DISABLE_WAKE_LOCK":"1",
                "PHOTO_INTELLIGENCE_DASHBOARD_HOST":"127.0.0.1",
                "PHOTO_INTELLIGENCE_DASHBOARD_PORT":str(port),
                "PHOTO_INTELLIGENCE_QUARANTINE_DIR":str(stage/"quarantine"),
                "PHOTO_INTELLIGENCE_EXCHANGE_DIR":str(stage/"exchange"),
            })
            with (logs/"service-test.log").open("wb") as output:
                proc=subprocess.Popen([sys.executable,str(ROOT/"service.py")],
                        env=env,cwd=ROOT,stdin=subprocess.DEVNULL,
                        stdout=output,stderr=output)
                try:
                    deadline=time.monotonic()+24.0
                    status=None
                    last_error=None
                    while time.monotonic()<deadline:
                        if proc.poll() is not None:
                            self.fail("Isolated supervisor exited before ready")
                        try:
                            with urlopen(f"http://127.0.0.1:{port}/api/status",timeout=2) as response:
                                self.assertEqual(response.status,200)
                                status=json.load(response)
                            if status.get("library",{}).get("total")==1:break
                        except Exception as exc:
                            last_error=type(exc).__name__
                        time.sleep(.4)
                    self.assertIsNotNone(status,last_error)
                    self.assertEqual(status["library"]["total"],1,status)
                    self.assertEqual(status.get("db_integrity"),"ok")
                    with urlopen(f"http://127.0.0.1:{port}/api/gallery?limit=10",timeout=4) as response:
                        self.assertEqual(response.status,200)
                        gallery=json.load(response)
                    self.assertEqual(gallery["total"],1)
                    self.assertEqual(gallery["items"][0]["name"],"synthetic.jpg")
                finally:
                    if proc.poll() is None:
                        proc.send_signal(signal.SIGTERM)
                    proc.wait(timeout=20)
                    self.assertEqual(proc.returncode,0)


if __name__=="__main__":
    unittest.main()
