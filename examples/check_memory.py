"""Run the 86-turn story against the configured model in a temporary guest database."""
import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server


EVENTS = {
    1: ("访客", "我接过铜钥匙，把它收进口袋。"),
    39: ("阿宁", "我把铜钥匙还给阿宁，她当面收下了。"),
    64: ("许师傅", "许师傅来修门，阿宁从口袋里取出铜钥匙交给他。他收下钥匙走了。"),
}
CHECKPOINTS = {20, 60, 86}
QUESTION = "旧宅的铜钥匙现在由谁保管？请只答一个称呼：访客、阿宁或许师傅。"
PLACES = ["回廊", "前厅", "东厢", "后院", "门房", "厨房", "楼梯", "书房", "矮墙", "天井"]
DETAILS = ["光线", "风声", "旧漆", "灰尘", "雨痕", "木纹", "砖缝", "落叶", "脚印"]


def names_expected_holder(reply, expected):
    # Some local inference templates leave a reasoning delimiter in visible content.
    answer = reply.rsplit("</think>", 1)[-1]
    names = {name for name, _ in EVENTS.values() if name in answer}
    return names == {expected}


def run():
    cfg = server.model_adapter.configuration()
    if not cfg["enabled"]:
        raise SystemExit("请先配置 TAVERN_MODEL_URL 和 TAVERN_MODEL。本测试会发起 86 次模型请求。")
    client = server.app.test_client()
    client.get("/api/cards")
    card = json.loads((ROOT / "examples/旧宅的钥匙.json").read_text("utf-8"))["data"]
    headers = {"X-Tavern-Client": "web"}
    if os.getenv("TAVERN_TEST_KEY"):
        headers["X-Model-Key"] = os.environ["TAVERN_TEST_KEY"]

    def post(path, data):
        response = client.post(path, json=data, headers=headers)
        if response.status_code not in (200, 201):
            raise RuntimeError(f"{response.status_code}: {response.json}")
        return response.json

    cid = post("/api/cards", {"name": card["name"], "persona": card["description"],
                              "greeting": card["first_mes"]})["id"]
    chat = "/api/chat/" + cid
    version = None
    holder = ""
    checkpoints = []
    changes = []
    matched = 0
    previous_event = 0
    digest = hashlib.sha256()
    for n in range(1, 87):
        if n in EVENTS:
            prompt = EVENTS[n][1] + "请用一句短句回应。"
        elif n in CHECKPOINTS:
            prompt = QUESTION
        else:
            place = PLACES[(n - 1) % len(PLACES)]
            detail = DETAILS[(n - 1) // len(PLACES)]
            prompt = f"我们走到{place}。请只描写这里的{detail}，限一句短句。"
        preview = post(chat + "/context", {"prompt": prompt})
        # Capture the payload at the transport boundary and still send it to the real model.
        with patch.object(server.model_adapter.requests, "post",
                          wraps=server.model_adapter.requests.post) as transport:
            reply = post(chat, {"prompt": prompt, "nonce": str(n)})["reply"]
            transport.assert_called_once()
            sent = transport.call_args.kwargs["json"]["messages"]
        if sent != preview["messages"]:
            raise AssertionError(f"第 {n} 轮预览和实际请求不一致")
        if holder:
            expected = "本段对话已确认的剧情记忆：\n铜钥匙：" + holder + "保管铜钥匙。"
            if sent[1] != {"role": "user", "content": expected}:
                raise AssertionError(f"第 {n} 轮记忆不符")
            matched += 1
        digest.update(json.dumps([n, sent, reply], ensure_ascii=False).encode("utf-8") + b"\n")
        if n in CHECKPOINTS:
            checkpoints.append({"turn": n, "expected": holder, "reply": reply,
                                "names_expected_holder": names_expected_holder(reply, holder),
                                "exact_match": reply.strip(" \r\n\t。.!！「」“”") == holder,
                                "turns_since_event": n - previous_event,
                                "recent_turns": preview["history_turns"],
                                "handoff_in_recent_history": any(
                                    EVENTS[previous_event][1] in m["content"] for m in sent[3:-1])})
        if n in EVENTS:
            holder, _ = EVENTS[n]
            history = client.get(chat).json["turns"]
            note = post(chat + "/memory", {"topic": "铜钥匙", "fact": holder + "保管铜钥匙。",
                                           "version": version, "turn_id": history[-1]["id"],
                                           "speaker": "prompt"})["memories"][0]
            version = note["version"]
            previous_event = n
            changes.append({"after_turn": n, "holder": holder})
        if n % 10 == 0 or n in CHECKPOINTS:
            print(f"{n}/86", flush=True)
    total = len(client.get(chat).json["turns"])
    if total != 86 or matched != 85:
        raise AssertionError("轮数或核对次数不符")
    return {"model": cfg["model"], "completed_turns": total, "matched_memory_requests": matched,
            "preview_matches_request": total, "memory_changes": changes, "question": QUESTION,
            "checkpoints": checkpoints, "transcript_sha256": digest.hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="保存本次测试的 JSON 结果")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as folder:
        server.DB = Path(folder) / "test.db"
        server.initialize()
        report = run()
    result = json.dumps(report, ensure_ascii=False, indent=2)
    print(result)
    if args.output:
        args.output.write_text(result + "\n", encoding="utf-8")
