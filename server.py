import hashlib
import os
import re
import secrets
import sqlite3
import threading
from pathlib import Path
from contextlib import contextmanager

import model_adapter
from flask import Flask, abort, g, jsonify, request

ROOT = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=str(ROOT / "static"))
app.config.update(MAX_CONTENT_LENGTH=131072, TRUSTED_HOSTS=[h.strip() for h in os.environ.get("TAVERN_HOSTS","127.0.0.1,localhost").split(",") if h.strip()])
DB = Path(os.environ.get("TAVERN_DB", str(ROOT / "tavern.sqlite3")))
locks = [threading.Lock() for _ in range(64)]
COVERS = {"mountain", "library", "forest"}
SEEDS = [
    ("star", "沈星遥", "科幻", "停电的第七夜，山顶观测站收到了一封来自明天的电报。", "你是山顶天文台的值夜人沈星遥，冷静，好奇，爱记手账。以对话推进悬疑故事，让访客自己决定行动。", "你来得正好。电台刚刚报出了明天的日期。你愿意替我再听一遍吗？", "mountain"),
    ("archive", "林见秋", "悬疑", "旧书修复师在一本无人借阅的书里，发现了写给你的借条。", "你是旧书修复师林见秋。你说话温和，用纸张和墨迹寻找线索。不要替访客决定其行动。", "坐吧，茶还温着。你看这张借条，签名像不像你的字？", "library"),
    ("woods", "阿苔", "奇幻", "穿过雾林，你遇见了一位只收故事、不收金币的引路人。", "你叫阿苔，是雾林的引路人，机灵而善意。你用简短的中文和具体细节回应访客。", "往前是会说谎的桥，往左是睡着的河。带了什么故事来换路？", "forest"),
]

@contextmanager
def connect():
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()

def initialize():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS cards (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, genre TEXT NOT NULL,
            description TEXT NOT NULL, persona TEXT NOT NULL, greeting TEXT NOT NULL,
            cover TEXT NOT NULL, public INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS turns (
            id INTEGER PRIMARY KEY, owner TEXT NOT NULL, card TEXT NOT NULL,
            nonce TEXT NOT NULL, prompt TEXT NOT NULL, reply TEXT NOT NULL,
            UNIQUE(owner,card,nonce));
        CREATE TABLE IF NOT EXISTS memories (
            owner TEXT NOT NULL, card TEXT NOT NULL, topic TEXT NOT NULL,
            fact TEXT NOT NULL, turn_id INTEGER, speaker TEXT,
            version TEXT NOT NULL, PRIMARY KEY(owner,card,topic));
        CREATE TABLE IF NOT EXISTS scene_preferences (
            owner TEXT NOT NULL, card TEXT NOT NULL, adult INTEGER NOT NULL,
            boundaries TEXT NOT NULL, PRIMARY KEY(owner,card));
        CREATE INDEX IF NOT EXISTS turns_lookup ON turns(owner,card,id);
        """)
        for card in SEEDS:
            db.execute("INSERT OR IGNORE INTO cards VALUES (?, '', ?, ?, ?, ?, ?, ?, 1)", card)

@app.before_request
def identify():
    if request.path.startswith("/static/"):
        return
    token = request.cookies.get("tavern_guest", "")
    if not re.fullmatch(r"[a-f0-9]{64}", token):
        token = secrets.token_hex(32)
        g.new_token = token
    g.owner = hashlib.sha256(token.encode()).hexdigest()
    if request.method == "POST" and (request.headers.get("X-Tavern-Client") != "web" or not request.is_json):
        abort(403)

@app.after_request
def response_headers(response):
    if getattr(g, "new_token", None):
        response.set_cookie("tavern_guest", g.new_token, max_age=86400*365,
                            httponly=True, samesite="Lax", secure=request.is_secure or os.environ.get("TAVERN_HTTPS") == "1")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    if not request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response

@app.get("/")
def index():
    return app.send_static_file("index.html")

def get_card(db, cid):
    row = db.execute("SELECT * FROM cards WHERE id=? AND (public=1 OR owner=?)", (cid,g.owner)).fetchone()
    if not row:
        abort(404)
    return row

def present(row):
    item = dict(row)
    item["mine"] = item.pop("owner") == g.owner
    return item

@app.get("/api/cards")
def catalog():
    with connect() as db:
        rows = db.execute("SELECT * FROM cards WHERE public=1 OR owner=? ORDER BY (owner='') ASC, CASE WHEN owner='' THEN rowid ELSE -rowid END", (g.owner,)).fetchall()
    return jsonify([present(r) for r in rows])

def body_text(data, field, limit, optional=False):
    text = data.get(field, "")
    if not isinstance(text, str) or len(text.strip()) > limit or (not optional and not text.strip()):
        abort(400)
    return text.strip()

@app.post("/api/cards")
def create_card():
    data = request.get_json()
    if not isinstance(data, dict):
        abort(400)
    values = [body_text(data, k, limit, opt) for k,limit,opt in
              [("name",40,False),("genre",20,True),("description",240,True),("persona",6000,False),("greeting",1500,False)]]
    values[1] = values[1] or "原创"
    cover = data.get("cover","mountain")
    if not isinstance(cover,str) or cover not in COVERS:
        abort(400)
    cid = secrets.token_urlsafe(12)
    with connect() as db:
        db.execute("INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?)", (cid,g.owner,*values,cover,int(data.get("public") is True)))
    return jsonify(id=cid),201

@app.get("/api/chat/<cid>")
def history(cid):
    with connect() as db:
        card = get_card(db,cid)
        turns = db.execute("SELECT id,prompt,reply FROM turns WHERE owner=? AND card=? ORDER BY id", (g.owner,cid)).fetchall()
        return jsonify(card=present(card),turns=[dict(t) for t in turns], memories=read_memories(db,cid), recent_limit=RECENT_LIMIT, scene=read_scene(db, cid))

RECENT_LIMIT = 12
MEMORY_LIMIT = 16

def read_memories(db, cid):
    rows = db.execute("SELECT topic,fact,turn_id,speaker,version FROM memories WHERE owner=? AND card=? ORDER BY topic", (g.owner,cid))
    return [dict(row) for row in rows]

@app.post("/api/chat/<cid>/memory")
def save_memory(cid):
    data = request.get_json()
    if not isinstance(data, dict):
        abort(400)
    topic = body_text(data, "topic", 40)
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        get_card(db,cid)
        where = (g.owner,cid,topic)
        current = db.execute("SELECT version FROM memories WHERE owner=? AND card=? AND topic=?",where).fetchone()
        if data.get("version") != (current["version"] if current else None):
            return jsonify(error="这条记忆已有更新，请关闭记忆页再打开，查看最新内容。"),409
        if data.get("remove") is True:
            db.execute("DELETE FROM memories WHERE owner=? AND card=? AND topic=?",where)
        else:
            fact = body_text(data,"fact",240)
            if not current and len(read_memories(db,cid)) >= MEMORY_LIMIT:
                return jsonify(error="已记满 16 条，请先修订或移除旧记忆。"),400
            turn_id, speaker = data.get("turn_id"), data.get("speaker")
            if turn_id is not None or speaker is not None:
                if type(turn_id) is not int or speaker not in ("prompt","reply"):
                    abort(400)
                if not db.execute("SELECT 1 FROM turns WHERE id=? AND owner=? AND card=?", (turn_id,g.owner,cid)).fetchone():
                    abort(404)
            db.execute("""INSERT INTO memories VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(owner,card,topic) DO UPDATE SET fact=excluded.fact,turn_id=excluded.turn_id,
                speaker=excluded.speaker,version=excluded.version""",
                (*where,fact,turn_id,speaker,secrets.token_hex(12)))
        return jsonify(memories=read_memories(db,cid))

HISTORY_CHARS = 16000


@app.get("/api/model")
def model_info():
    return jsonify(model_adapter.configuration())


def read_scene(db, cid):
    row = db.execute("SELECT adult,boundaries FROM scene_preferences WHERE owner=? AND card=?",
                     (g.owner, cid)).fetchone()
    return {"adult": bool(row and row["adult"]), "boundaries": row["boundaries"] if row else ""}


@app.post("/api/chat/<cid>/scene")
def save_scene(cid):
    data = request.get_json()
    if not isinstance(data, dict) or type(data.get("adult")) is not bool:
        abort(400)
    boundaries = body_text(data, "boundaries", 400, optional=True)
    with connect() as db:
        get_card(db, cid)
        db.execute("""INSERT INTO scene_preferences VALUES (?,?,?,?)
                      ON CONFLICT(owner,card) DO UPDATE SET adult=excluded.adult,
                      boundaries=excluded.boundaries""", (g.owner, cid, data["adult"], boundaries))
    return jsonify(adult=data["adult"], boundaries=boundaries)


def build_context(db, card, prompt):
    cid = card["id"]
    scene = read_scene(db, cid)
    memories = read_memories(db, cid)
    system = "请用中文与访客进行虚构角色对话。角色名：" + card["name"] + "。人物设定：" + card["persona"]
    if scene["adult"]:
        system += " 本场景允许描写成年人之间双方自愿的亲密关系，尊重参与者的选择和边界。"
    else:
        system += " 亲密情节以淡出处理。"
    if scene["boundaries"]:
        system += " 访客设定的叙事边界：" + scene["boundaries"]
    if memories:
        system += " 剧情记忆记录目前的状态。与旧对白冲突时采用已确认的最新状态。"
    messages = [{"role": "system", "content": system}]
    if memories:
        facts = "\n".join(m["topic"] + "：" + m["fact"] for m in memories)
        messages.append({"role": "user", "content": "本段对话已确认的剧情记忆：\n" + facts})
    messages.append({"role": "assistant", "content": card["greeting"]})
    recent = []
    chars = 0
    rows = db.execute("""SELECT prompt,reply FROM turns WHERE owner=? AND card=?
                         ORDER BY id DESC LIMIT ?""", (g.owner, cid, RECENT_LIMIT))
    for row in rows:
        size = len(row["prompt"]) + len(row["reply"])
        if chars + size > HISTORY_CHARS:
            break
        recent.append(row)
        chars += size
    for row in reversed(recent):
        messages.extend([{"role": "user", "content": row["prompt"]},
                         {"role": "assistant", "content": row["reply"]}])
    if prompt:
        messages.append({"role": "user", "content": prompt})
    return {"messages": messages, "history_turns": len(recent), "memory_count": len(memories),
            "characters": sum(len(m["content"]) for m in messages)}


@app.post("/api/chat/<cid>/context")
def preview_context(cid):
    data = request.get_json()
    if not isinstance(data, dict):
        abort(400)
    prompt = body_text(data, "prompt", 4000, optional=True)
    with connect() as db:
        return jsonify(build_context(db, get_card(db, cid), prompt))


@app.post("/api/chat/<cid>")
def chat(cid):
    data = request.get_json()
    if not isinstance(data,dict):
        abort(400)
    prompt = body_text(data,"prompt",4000)
    nonce = body_text(data,"nonce",80)
    with connect() as db:
        card = get_card(db,cid)
    lock = locks[hash((g.owner,cid)) % len(locks)]
    if not lock.acquire(blocking=False):
        return jsonify(error="上一条回复还在生成，请稍候。"),409
    try:
        with connect() as db:
            old = db.execute("SELECT prompt,reply FROM turns WHERE owner=? AND card=? AND nonce=?", (g.owner,cid,nonce)).fetchone()
            if old:
                if old["prompt"] != prompt:
                    abort(409)
                return jsonify(reply=old["reply"])
            messages = build_context(db, card, prompt)["messages"]
        try:
            reply = model_adapter.generate(messages, request.headers.get("X-Model-Key", "").strip())
        except model_adapter.ModelError as exc:
            return jsonify(error=str(exc)), exc.status
        with connect() as db:
            db.execute("INSERT INTO turns(owner,card,nonce,prompt,reply) VALUES (?,?,?,?,?)",(g.owner,cid,nonce,prompt,reply))
        return jsonify(reply=reply)
    finally:
        lock.release()

if __name__ == "__main__":
    initialize()
    app.run(host="127.0.0.1",port=int(os.environ.get("TAVERN_PORT","18761")),threaded=True,debug=False)
