"""On-device people groups UI tests; only synthetic face embedding data."""
import http.client
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlsplit

import numpy as np
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import gallery_face_groups as groups
import gallery_face_web as web


class FaceWebTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix="face-web-tests-")
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.db=self.root/"private"/"faces.sqlite3"
        c=groups.setup_private_db(self.db)
        v=np.zeros(128,dtype=np.float32);v[0]=1
        c.execute("""INSERT INTO face_embeddings(face_id,image_id,face_ordinal,
           x,y,width,height,score,vector,algorithm)
           VALUES(1,11,0,120,100,100,100,.96,?,?)""",(v.tobytes(),groups.VERSION))
        c.execute("INSERT INTO face_clusters(face_id,cluster_id,assigned_at) VALUES(1,1,?)",
                  (groups.now(),))
        c.commit();c.close()
        self.report={"groups":[{"id":1,"photos":1,"photo_ids":[11],
                      "name":"Человек 1","verified_name":False}],
                     "scanned_images":1,"indexed_faces":1}
        self.http,self.url=web.make_server(self.report,self.db)
        self.t=threading.Thread(target=self.http.serve_forever,daemon=True)
        self.t.start()
        self.addCleanup(self.http.shutdown)
        self.addCleanup(self.http.server_close)
        u=urlsplit(self.url)
        self.host=u.netloc;self.prefix=u.path.rstrip("/")

    def fetch(self,method,path,body=b"",headers=None):
        c=http.client.HTTPConnection(self.host,timeout=5)
        c.request(method,path,body=body,headers=headers or {})
        r=c.getresponse();b=r.read();h=dict(r.getheaders());code=r.status;c.close()
        return code,b,h

    def send_name(self,name="Давид",group=1,origin=None,custom=True):
        body=json.dumps({"group_id":group,"name":name,
                         "revision":self.http.group_revisions.get(group,"invalid")}).encode()
        headers={"Content-Type":"application/json",
                 "Origin":origin or "http://"+self.host}
        if custom:headers["X-Face-Groups"]="manual-user-gesture"
        return self.fetch("POST",self.prefix+"/api/name",body,headers)

    def test_loopback_and_random_protected_url(self):
        self.assertEqual(self.http.server_address[0],"127.0.0.1")
        code,html,headers=self.fetch("GET",self.prefix+"/")
        self.assertEqual(code,200)
        self.assertIn("Назвать группу".encode("utf-8"),html)
        self.assertIn(b"function renameGroup",html)
        self.assertIn(b"connect-src 'self'",html)
        self.assertEqual(headers["Referrer-Policy"],"no-referrer")
        self.assertEqual(self.fetch("GET","/")[0],404)
        self.assertEqual(self.fetch("GET","/api/name")[0],404)

    def test_user_confirmed_name_persists_and_group_recalculation(self):
        response,body,_=self.send_name()
        self.assertEqual(response,200)
        self.assertEqual(json.loads(body)["name"],"Давид")
        c=groups.sqlite_ro(self.db)
        self.assertEqual(c.execute("SELECT label FROM face_manual_names").fetchone()[0],"Давид")
        c.close()
        out=groups.groups_report(self.db,self.db)
        self.assertEqual(out["groups"][0]["name"],"Давид")
        groups.group_embeddings(self.db)
        out=groups.groups_report(self.db,self.db)
        self.assertEqual(out["groups"][0]["name"],"Давид")

    def test_attack_requests_denied_and_not_stored(self):
        self.assertEqual(self.send_name(origin="https://evil.example")[0],403)
        self.assertEqual(self.send_name(custom=False)[0],403)
        self.assertEqual(self.send_name(group=3)[0],422)
        self.assertEqual(self.send_name(name="")[0],422)
        self.assertEqual(self.send_name(name="<script>\n")[0],422)
        self.assertEqual(self.fetch("POST","/api/name",b'{}')[0],404)
        c=groups.sqlite_ro(self.db)
        self.assertEqual(c.execute("select count(*) from face_manual_names").fetchone()[0],0)
        c.close()

    def test_group_page_endpoint_returns_all_photos_not_first_eighteen(self):
        c=groups.setup_private_db(self.db)
        vec=np.zeros(128,dtype=np.float32)
        vec[0]=1
        with c:
            for idx in range(2,72):
                c.execute("""INSERT INTO face_embeddings(
                    face_id,image_id,face_ordinal,x,y,width,height,score,vector,algorithm)
                    VALUES(?,?,0,2,3,66,66,?, ?,?)""",
                    (idx,idx+10,.93,vec.tobytes(),groups.VERSION))
                c.execute("INSERT INTO face_clusters VALUES(?,?,?)",
                          (idx,1,groups.now()))
        c.close()
        first=groups.group_photo_page(self.db,1,0,48)
        later=groups.group_photo_page(self.db,1,48,48)
        self.assertEqual(first["total"],71)
        self.assertEqual(len(first["photo_ids"]),48)
        self.assertTrue(first["has_more"])
        self.assertEqual(len(later["photo_ids"]),23)
        self.assertFalse(later["has_more"])
        self.assertEqual(len(set(first["photo_ids"]+later["photo_ids"])),71)
        # Old browser snapshot must not show changed group membership.
        code,_,_=self.fetch("GET",self.prefix+
                        "/api/group?id=1&offset=48&limit=48&revision="+
                        self.http.group_revisions[1])
        self.assertEqual(code,422)
        con=groups.sqlite_ro(self.db)
        try:
            latest=groups.group_revision(con,1)
        finally:
            con.close()
        fresh_report=dict(self.report,groups=[dict(self.report["groups"][0],
                         revision=latest)])
        fresh,url=web.make_server(fresh_report,self.db)
        t=threading.Thread(target=fresh.serve_forever,daemon=True);t.start()
        try:
            u=urlsplit(url)
            connection=http.client.HTTPConnection(u.netloc,timeout=5)
            query=u.path.rstrip("/")+"/api/group?id=1&offset=48&limit=48&revision="+fresh.group_revisions[1]
            connection.request("GET",query)
            response=connection.getresponse()
            data=response.read();headers=dict(response.getheaders())
            code=response.status;connection.close()
            self.assertEqual(code,200)
            obj=json.loads(data)
            self.assertEqual(obj["total"],71)
            self.assertEqual(len(obj["photo_ids"]),23)
            self.assertEqual(headers["Referrer-Policy"],"no-referrer")
        finally:
            fresh.shutdown();fresh.server_close();t.join(timeout=2)

    def test_group_photo_page_rejects_unknown_oversized_and_unauthorized(self):
        self.assertEqual(self.fetch("GET","/api/group?id=1&offset=0&limit=48")[0],404)
        self.assertEqual(self.fetch("GET",self.prefix+
            "/api/group?id=9&offset=0&limit=48")[0],422)
        self.assertEqual(self.fetch("GET",self.prefix+
            "/api/group?id=1&offset=0&limit=50000")[0],422)
        self.assertEqual(self.fetch("GET",self.prefix+
            "/api/group?id=1&offset=-1&limit=48")[0],422)
        self.assertEqual(self.fetch("GET",self.prefix+
            "/api/group?id=1&offset=0&limit=48&unexpected=1")[0],422)
        self.assertEqual(self.fetch("GET",self.prefix+
            "/api/group?id=1&offset=0&limit=48&revision="+
            self.http.group_revisions[1])[0],200)
        with self.assertRaises(ValueError):
            groups.group_photo_page(self.db,1,0,81)
        with self.assertRaises(ValueError):
            groups.group_photo_page(self.db,True,0,48)

    def test_stale_group_name_update_blocked_after_regrouping(self):
        self.assertEqual(self.send_name(name="Previous",group=1)[0],200)
        c=groups.setup_private_db(self.db)
        v=np.zeros(128,dtype=np.float32);v[0]=1
        with c:
            c.execute("""INSERT INTO face_embeddings(
                face_id,image_id,face_ordinal,x,y,width,height,score,vector,algorithm)
                VALUES(3,66,0,1,2,55,55,.93,?,?)""",
                (v.tobytes(),groups.VERSION))
            c.execute("INSERT INTO face_clusters VALUES(3,1,?)",(groups.now(),))
        c.close()
        self.assertEqual(self.send_name(name="Wrong",group=1)[0],422)
        c=groups.sqlite_ro(self.db)
        try:
            self.assertEqual(c.execute(
                "SELECT label FROM face_manual_names").fetchone()[0],"Previous")
        finally:
            c.close()

    def test_browser_loads_group_only_when_expanded(self):
        page=groups.render_private_gallery(self.report,live=True)
        self.assertIn('onGroupToggle(this)',page)
        self.assertIn("const photoPages = new Map()",page)
        self.assertIn("loadNextGroupPage(details)",page)
        self.assertIn("limit=48",page)
        self.assertIn("Показано ",page)
        self.assertNotIn('src="http://127.0.0.1:18780/api/thumb?id=11"',page)
        offline=groups.render_private_gallery(self.report,live=False)
        self.assertIn('src="http://127.0.0.1:18780/api/thumb?id=11"',offline)

    def test_portrait_cover_stays_local_and_no_original_changes(self):
        media=self.root/"shared"
        (media/"Camera").mkdir(parents=True)
        source=media/"Camera/photo.png"
        im=Image.new("RGB",(640,640),(165,125,113))
        im.save(source)
        original=source.read_bytes()
        gallery=self.root/"gallery.sqlite3"
        c=sqlite3.connect(gallery)
        c.execute("create table images (id integer primary key,path text,size integer,mtime real)")
        c.execute("insert into images values(?,?,?,?)",
                  (11,"Camera/photo.png",source.stat().st_size,source.stat().st_mtime))
        c.commit();c.close()
        x=groups.attach_face_covers(self.report,self.db,gallery,root=media)
        self.assertEqual(x["cover_count"],1)
        self.assertTrue(x["groups"][0]["face_cover_data_uri"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(source.read_bytes(),original)
        html=groups.render_private_gallery(x)
        self.assertIn("data:image/jpeg;base64,",html)
        self.assertIn("img-src http://127.0.0.1:18780 data:",html)

if __name__=="__main__":unittest.main()
