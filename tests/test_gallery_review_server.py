"""Private loopback review HTTP tests with synthetic media only."""
from __future__ import annotations
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import gallery_album_review as review
import gallery_review_server as server


class LiveReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="local-review-http-")
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.shared=self.root/"media"
        self.shared.mkdir()
        self.state=self.root/"private"
        self.state.mkdir(mode=0o700)
        (self.shared/"Camera").mkdir()
        path=self.shared/"Camera/one.jpg"
        path.write_bytes(b"fake synthetic photo bytes")
        stat=path.stat()
        self.item={"id":25,"path":"Camera/one.jpg","name":"one.jpg",
                   "album":"Camera","mtime":stat.st_mtime,"size":stat.st_size,
                   "person_photo_score":.7}
        self.scope=review.review_scope("",review.hashlib.sha256(b"").hexdigest()[:16])
        self.report={"reference_album":None,"album_membership_known":False,
                     "local_indexed":1,"candidate_count":1,
                     "candidates":[self.item],"review_scope":self.scope}
        self.server,self.url=server.make_server(
            self.report,self.state,port=0,page_size=20,shared_root=self.shared)
        self.addCleanup(self.server.server_close)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.server.shutdown)
        u=urlsplit(self.url)
        self.host=u.netloc
        self.prefix=u.path.rstrip("/")

    def fetch(self,method,path,body=None,headers=None):
        connection=http.client.HTTPConnection(self.host,timeout=4)
        connection.request(method,path,body=body,headers=headers or {})
        response=connection.getresponse()
        data=response.read()
        headers=dict(response.getheaders())
        code=response.status
        connection.close()
        return code,data,headers

    def send_decision(self, id_=25, decision="mine", origin=None,
                      fingerprint=None, extra_headers=None):
        if origin is None:origin="http://"+self.host
        payload=json.dumps({
            "id":id_,"decision":decision,
            "fingerprint":fingerprint or server.image_fingerprint(self.item),
        }).encode("utf-8")
        headers={"Content-Type":"application/json",
                 "X-Photo-Review":"manual-user-gesture",
                 "Origin":origin}
        if extra_headers:headers.update(extra_headers)
        return self.fetch("POST",self.prefix+"/api/review",payload,headers)

    def test_auth_url_only_loopback_bound_and_no_referrer(self):
        self.assertEqual(self.server.server_address[0],"127.0.0.1")
        status,html,headers=self.fetch("GET",self.prefix+"/")
        self.assertEqual(status,200)
        self.assertIn(b'const LIVE_MODE = true;',html)
        self.assertIn(b"connect-src &#39;self&#39;",html)
        self.assertIn(b"saveState",html)
        self.assertEqual(headers.get("Referrer-Policy"),"no-referrer")
        self.assertEqual(self.fetch("GET","/")[0],404)
        self.assertEqual(self.fetch("GET","/api/review")[0],404)

    def test_manual_save_durable_private_and_reload(self):
        status,body,_=self.send_decision()
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)["count"],1)
        expected=self.state/"review_decisions"/(self.scope.split(":")[1]+".json")
        self.assertTrue(expected.exists())
        self.assertEqual(expected.stat().st_mode&0o777,0o600)
        self.assertEqual(expected.parent.stat().st_mode&0o777,0o700)
        fresh=server.load_decisions(self.state,self.scope)
        self.assertEqual(fresh["25"]["decision"],"mine")
        report,approved=server._server_page(self.report,self.state,20)
        self.assertEqual(approved,{25:"mine"})
        self.assertIn("const PRIOR_REVIEWS = {\"25\":\"mine\"}",report)
        self.assertEqual((self.shared/"Camera/one.jpg").read_bytes(),b"fake synthetic photo bytes")

    def test_remove_mistaken_manual_mark_and_preserve_unreviewed_state(self):
        self.assertEqual(self.send_decision(decision="mine")[0],200)
        response,raw,_=self.send_decision(decision="clear")
        self.assertEqual(response,200)
        self.assertEqual(json.loads(raw)["count"],0)
        self.assertEqual(server.load_decisions(self.state,self.scope),{})
        self.assertEqual(server.validated_state_decisions(self.report,self.state),{})

    def test_forbid_cross_origin_no_custom_header_invalid_payload(self):
        self.assertEqual(self.send_decision(origin="https://bad.example")[0],403)
        self.assertEqual(self.send_decision(extra_headers={"X-Photo-Review":"wrong"})[0],403)
        self.assertEqual(self.send_decision(id_=999)[0],422)
        self.assertEqual(self.send_decision(fingerprint="wrong")[0],422)
        self.assertEqual(self.send_decision(decision="face_identified")[0],422)
        self.assertEqual(server.load_decisions(self.state,self.scope),{})

    def test_reject_changed_original_and_missing_file(self):
        file=self.shared/"Camera/one.jpg"
        file.write_bytes(b"changed bytes with other length")
        self.assertEqual(self.send_decision()[0],422)
        file.unlink()
        self.assertEqual(self.send_decision()[0],422)
        self.assertEqual(server.load_decisions(self.state,self.scope),{})

    def test_reject_symlink_swap_and_preserve_db_free_state(self):
        original=self.shared/"Camera/one.jpg"
        original.unlink()
        original.symlink_to(self.root/"another.txt")
        self.assertEqual(self.send_decision()[0],422)

    def test_batch_import_atomic_rejects_any_stale_entries(self):
        payload={"updates":[
            {"id":25,"decision":"mine","fingerprint":server.image_fingerprint(self.item)},
            {"id":999,"decision":"other","fingerprint":"bogus"},
        ]}
        headers={"Content-Type":"application/json",
                 "X-Photo-Review":"manual-user-gesture",
                 "Origin":"http://"+self.host}
        code,_,_=self.fetch("POST",self.prefix+"/api/import",
                            json.dumps(payload).encode(),headers)
        self.assertEqual(code,422)
        self.assertEqual(server.load_decisions(self.state,self.scope),{})
        payload["updates"].pop()
        code,result,_=self.fetch("POST",self.prefix+"/api/import",
                                 json.dumps(payload).encode(),headers)
        self.assertEqual(code,200)
        self.assertEqual(json.loads(result)["count"],1)
        self.assertEqual(server.load_decisions(self.state,self.scope)["25"]["decision"],"mine")

    def test_bulk_origin_and_path_security(self):
        headers={"Content-Type":"application/json",
                 "X-Photo-Review":"manual-user-gesture",
                 "Origin":"https://not-local.example"}
        payload=b'{"updates":[]}'
        self.assertEqual(self.fetch("POST",self.prefix+"/api/import",payload,headers)[0],403)
        self.assertEqual(self.fetch("POST","/api/import",payload,headers)[0],404)

    def test_input_body_limit(self):
        headers={"Content-Type":"application/json",
                 "X-Photo-Review":"manual-user-gesture",
                 "Origin":"http://"+self.host}
        self.assertEqual(self.fetch("POST",self.prefix+"/api/review",b"x"*5000,headers)[0],422)

    def test_review_scope_keeps_different_albums_separate(self):
        self.assertEqual(self.send_decision()[0],200)
        other=review.review_scope("Other",review.hashlib.sha256(b"").hexdigest()[:16])
        self.assertEqual(server.load_decisions(self.state,other),{})

    def test_saved_review_used_after_restart_but_not_if_image_id_reused(self):
        self.assertEqual(self.send_decision(decision="mine")[0],200)
        valid=server.validated_state_decisions(self.report,self.state)
        self.assertEqual(valid,{25:"mine"})
        # A new catalog with same ID but different media must not inherit
        # an identity decision from the prior file.
        newer=dict(self.item,path="Camera/another.jpg")
        changed=dict(self.report,candidates=[newer])
        self.assertEqual(server.validated_state_decisions(changed,self.state),{})
        # The historic record remains safe in private state.
        self.assertEqual(server.load_decisions(self.state,self.scope)["25"]["decision"],"mine")

    def test_batch_is_not_partial_if_some_file_no_longer_exists(self):
        self.assertEqual(self.send_decision()[0],200)
        old=server.load_decisions(self.state,self.scope)
        (self.shared/"Camera/one.jpg").unlink()
        data={"updates":[
            {"id":25,"decision":"other","fingerprint":server.image_fingerprint(self.item)}
        ]}
        headers={"Content-Type":"application/json",
                 "X-Photo-Review":"manual-user-gesture",
                 "Origin":"http://"+self.host}
        code,_,_=self.fetch("POST",self.prefix+"/api/import",
                            json.dumps(data).encode(),headers)
        self.assertEqual(code,422)
        self.assertEqual(server.load_decisions(self.state,self.scope),old)

    def test_page_fingerprint_matches_server(self):
        self.assertEqual(review._live_fingerprint(self.item),
                         server.image_fingerprint(self.item))


if __name__=="__main__":
    unittest.main()
