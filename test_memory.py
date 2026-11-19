"""Regression checks for private, user-confirmed conversation memory."""
from contextlib import closing
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server


class MemoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(setattr, server, "DB", server.DB)
        server.DB = Path(self.temp.name) / "old.db"
        with closing(sqlite3.connect(server.DB)) as db:
            db.execute("""CREATE TABLE turns(id INTEGER PRIMARY KEY,owner TEXT,card TEXT,
                       nonce TEXT,prompt TEXT,reply TEXT,UNIQUE(owner,card,nonce))""")
        server.initialize()
        self.client = server.app.test_client()
        self.other = server.app.test_client()
        for c in [self.client, self.other]:
            c.get("/api/cards")
        self.h = {"X-Tavern-Client":"web", "X-Model-Key":"test-only"}
        result = Mock(status_code=200)
        result.json.return_value = {"choices":[{"message":{"content":"下一幕"}}]}
        cfg = patch.dict(os.environ, {"TAVERN_MODEL_URL": "https://example.invalid/chat/completions", "TAVERN_MODEL": "test"})
        cfg.start()
        self.addCleanup(cfg.stop)
        stub = patch("model_adapter.requests.post",return_value=result)
        self.model = stub.start()
        self.addCleanup(stub.stop)

    def send(self, n, text):
        r = self.client.post("/api/chat/star", json={"prompt":text,"nonce":str(n)},headers=self.h)
        self.assertEqual(r.status_code,200)
        return self.model.call_args.kwargs["json"]["messages"]

    def note(self, client, payload, card="star"):
        return client.post("/api/chat/"+card+"/memory",json=payload,headers=self.h)

    def test_old_fact_beyond_window_and_supersession(self):
        self.send(0,"铜钥匙由阿苔保管")
        turn = self.client.get("/api/chat/star").json["turns"][0]
        note = {"topic":"铜钥匙","fact":"阿苔保管","turn_id":turn["id"],"speaker":"prompt"}
        saved = self.note(self.client,note).json["memories"][0]
        self.assertEqual(self.model.call_count,1)
        for n in range(1,31):
            messages = self.send(n,"观察第"+str(n)+"扇窗")
        self.assertFalse(any(m["content"]=="铜钥匙由阿苔保管" for m in messages))
        self.assertTrue(any("铜钥匙：阿苔保管" in m["content"] for m in messages))
        self.assertEqual(len(messages),28)  # persona, facts, greeting, 12 turns and new prompt
        update = {"topic":"铜钥匙","fact":"已归还林见秋","version":saved["version"]}
        new = self.note(self.client,update).json["memories"][0]
        self.assertEqual(self.note(self.client,update).status_code,409)
        messages = self.send(31,"钥匙现在在哪里？")
        text = "\n".join(m["content"] for m in messages)
        self.assertIn("已归还林见秋",text)
        self.assertNotIn("阿苔保管",text)
        self.assertEqual(self.note(self.client,dict(topic="铜钥匙",remove=True,version=new["version"])).json["memories"],[])
        self.assertNotIn("已归还林见秋",str(self.send(32,"继续")))
        server.initialize()
        self.assertEqual(len(self.client.get("/api/chat/star").json["turns"]),33)

    def test_isolation_and_capacity(self):
        self.send(0,"只有我知道")
        tid=self.client.get("/api/chat/star").json["turns"][0]["id"]
        note=dict(topic="线索",fact="窗边",turn_id=tid,speaker="reply")
        self.assertEqual(self.note(self.other,note).status_code,404)
        self.assertEqual(self.note(self.client,note,"woods").status_code,404)
        self.assertEqual(self.note(self.client,note).status_code,200)
        for n in range(15):
            self.assertEqual(self.note(self.client,dict(topic=str(n),fact="原文")).status_code,200)
        self.assertEqual(self.note(self.client,dict(topic="第十七条",fact="超限")).status_code,400)
        self.assertEqual(self.other.get("/api/chat/star").json["memories"],[])
        self.assertEqual(self.client.get("/api/chat/woods").json["memories"],[])
        self.assertEqual(self.model.call_count,1)

if __name__ == "__main__":
    unittest.main()
