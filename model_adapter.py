"""Optional Chat Completions transport. Only the operator sets the destination."""
import os
from urllib.parse import urlsplit

import requests


class ModelError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def configuration():
    url = os.environ.get("TAVERN_MODEL_URL", "").strip()
    model = os.environ.get("TAVERN_MODEL", "").strip()
    parts = urlsplit(url)
    local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
    valid = (parts.scheme == "https" or (local and parts.scheme == "http"))
    enabled = bool(model and valid and parts.hostname and not parts.username and
                   not parts.password and not parts.query and not parts.fragment)
    return {"enabled": enabled, "host": parts.netloc if enabled else "",
            "model": model if enabled else "",
            "key_required": os.environ.get("TAVERN_MODEL_KEYLESS") != "1"}


def generate(messages, key):
    config = configuration()
    if not config["enabled"]:
        raise ModelError("站主尚未配置模型。仍可创建角色卡、整理记忆和预览上下文。", 503)
    if config["key_required"] and not key:
        raise ModelError("请在模型连接设置中填写密钥。", 422)
    headers = {"Authorization": "Bearer " + key} if config["key_required"] else {}
    try:
        result = requests.post(os.environ["TAVERN_MODEL_URL"].strip(), headers=headers,
                               json={"model": config["model"], "messages": messages, "max_tokens": 1000},
                               timeout=(7, 75), allow_redirects=False)
        if result.status_code != 200:
            reason = {401: "密钥无效", 402: "账户余额不足", 403: "请求被拒绝",
                      429: "请求过于频繁"}.get(result.status_code, "请求失败")
            raise ModelError(f"模型服务返回 {result.status_code}，{reason}。")
        choice = result.json()["choices"][0]
        message = choice["message"]
        if message.get("refusal") or choice.get("finish_reason") == "content_filter":
            raise ModelError("模型服务拒绝了本次生成。")
        reply = message["content"]
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError()
        return reply
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        raise ModelError("未收到可用的模型回复，输入已保留。") from None
