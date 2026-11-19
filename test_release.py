import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import model_adapter
import server


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db = patch.object(server, "DB", Path(temp.name) / "test.db")
        db.start()
        self.addCleanup(db.stop)
        env = patch.dict(os.environ, {"TAVERN_MODEL_URL": "", "TAVERN_MODEL": "",
                                      "TAVERN_MODEL_KEYLESS": "0"})
        env.start()
        self.addCleanup(env.stop)
        server.initialize()
        self.client = server.app.test_client()
        self.client.get("/api/cards")
        self.headers = {"X-Tavern-Client": "web"}

    def post(self, suffix, **data):
        return self.client.post("/api/chat/star" + suffix, json=data, headers=self.headers)

    def test_disconnected_preview_and_scene_isolation(self):
        with patch("model_adapter.requests.post") as upstream:
            self.assertFalse(self.client.get("/api/model").json["enabled"])
            self.assertEqual(self.post("", prompt="新消息", nonce="1").status_code, 503)
            self.assertEqual(self.post("/scene", adult=True, boundaries="止于拥抱").status_code, 200)
            preview = self.post("/context", prompt="新消息").json
            self.assertIn("止于拥抱", preview["messages"][0]["content"])
            self.assertTrue(self.client.get("/api/chat/star").json["scene"]["adult"])
            other = server.app.test_client()
            self.assertFalse(other.get("/api/chat/star").json["scene"]["adult"])
            self.assertFalse(self.client.get("/api/chat/woods").json["scene"]["adult"])
            self.assertEqual(self.client.get("/api/chat/star").json["turns"], [])
            upstream.assert_not_called()

    def test_preview_matches_sent_messages_and_retries(self):
        os.environ.update(TAVERN_MODEL_URL="http://localhost:8000/v1/chat/completions",
                          TAVERN_MODEL="example", TAVERN_MODEL_KEYLESS="1")
        response = Mock(status_code=200)
        response.json.return_value = {"choices": [{"message": {"content": "收到"}}]}
        preview = self.post("/context", prompt="钥匙呢？").json
        with patch("model_adapter.requests.post", return_value=response) as upstream:
            self.headers["X-Model-Key"] = "unused"
            for _ in range(2):
                self.assertEqual(self.post("", prompt="钥匙呢？", nonce="a").status_code, 200)
            upstream.assert_called_once()
            self.assertEqual(upstream.call_args.kwargs["json"]["messages"], preview["messages"])
            self.assertEqual(upstream.call_args.kwargs["headers"], {})
            self.assertEqual(len(self.client.get("/api/chat/star").json["turns"]), 1)
            response.json.return_value = {"choices": [{"message": {"refusal": "declined"}}]}
            error = self.post("", prompt="下一幕", nonce="b")
            self.assertEqual(error.status_code, 502)
            self.assertIn("拒绝", error.json["error"])
            self.assertEqual(len(self.client.get("/api/chat/star").json["turns"]), 1)

    def test_history_budget_keeps_complete_turns(self):
        with patch("model_adapter.generate", return_value="月" * 3000):
            for n in range(8):
                self.post("", prompt="窗", nonce=str(n))
        preview = self.post("/context", prompt="继续").json
        self.assertEqual(preview["history_turns"], 5)
        self.assertEqual(len(preview["messages"]), 13)
        self.assertEqual(preview["characters"], sum(len(m["content"]) for m in preview["messages"]))
