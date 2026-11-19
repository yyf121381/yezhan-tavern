const $ = id => document.getElementById(id);
const state = {cards: [], mine: false, genre: "全部", key: "", model: null, open: "", loading: 0, pending: new Map(), drafts: new Map(), retries: new Map()};
function el(tag, cls, value) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (value !== undefined) node.textContent = value;
  return node;
}
async function api(path, data, key) {
  const options = data === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json", "X-Tavern-Client": "web"}, body: JSON.stringify(data)
  };
  if (key) options.headers["X-Model-Key"] = key;
  const res = await fetch(path, options);
  const parsed = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(parsed.error || (res.status === 400 ? "请检查填写的内容和长度。" : "请求失败，请重试。"));
  return parsed;
}
function show(dialog) { if (!dialog.open) dialog.showModal(); }
document.querySelectorAll("[data-close]").forEach(b => b.onclick = () => b.closest("dialog").close());
function render() {
  const list = state.cards.filter(c => !state.mine || c.mine);
  const genres = ["全部", ...new Set(list.map(c => c.genre))];
  if (!genres.includes(state.genre)) state.genre = "全部";
  $("filters").replaceChildren(...genres.map(genre => {
    const b = el("button", "", genre); b.setAttribute("aria-pressed", genre === state.genre);
    b.onclick = () => { state.genre = genre; render(); }; return b;
  }));
  const q = $("search").value.trim().toLowerCase();
  const visible = list.filter(c => (state.genre === "全部" || c.genre === state.genre) && [c.name,c.genre,c.description].join(" ").toLowerCase().includes(q));
  $("cards").replaceChildren(...visible.map(c => {
    const b = el("button", "card");
    const img = el("img"); img.src = "/static/art/" + c.cover + ".png"; img.alt = ""; img.loading = "lazy";
    const body = el("div","card-body");
    body.append(el("small","",c.genre + (c.mine ? (c.public ? " · 公开" : " · 私人") : "")), el("h3","",c.name), el("p","",c.description));
    b.append(img, body); b.onclick = () => openChat(c.id); return b;
  }));
  if (!visible.length) $("cards").append(el("p","empty", list.length ? "没有找到符合条件的角色。" : "这里还没有角色，创建一张卡就能开始。"));
  $("heading").textContent = state.mine ? "我的角色" : "角色书架";
  $("hero").hidden = state.mine;
  $("discover").setAttribute("aria-pressed", !state.mine); $("mine").setAttribute("aria-pressed", state.mine);
}
async function refresh() { state.cards = await api("/api/cards"); render(); }
function switchShelf(mine) {state.mine=mine; state.genre="全部"; $("search").value=""; render();}
$("discover").onclick = () => switchShelf(false);
$("mine").onclick = () => switchShelf(true);
$("search").oninput = render;
$("feature").onclick = () => openChat("star");
$("create").onclick = () => { $("save-status").textContent=""; show($("editor")); };
$("card-form").onsubmit = async e => {
  e.preventDefault(); const form=e.currentTarget; const values=Object.fromEntries(new FormData(form));
  values.public=form.elements.public.checked;
  const button=form.querySelector("button.ink");button.disabled=true;
  try {
    const result=await api("/api/cards",values);
    form.reset(); $("editor").close(); await refresh(); switchShelf(true); await openChat(result.id);
  } catch(err) { $("save-status").textContent=err.message; } finally {button.disabled=false;}
};
async function settings(){
  show($("connection"));
  $("key").value=state.key;
  try {
    const cfg=await api("/api/model"); state.model=cfg;
    $("model-destination").textContent=cfg.enabled
      ? "本次对话由 "+cfg.host+" 的 "+cfg.model+" 处理。发送时会将预览中的内容提交给该服务。"
      : "站主尚未配置模型地址和名称。你可以先创建角色、整理记忆并预览上下文。";
    $("key-label").hidden=!cfg.enabled || !cfg.key_required;
  } catch(err){$("model-destination").textContent=err.message;}
}
$("connect").onclick=settings; $("chat-connect").onclick=settings;
function setKey(value){
  state.key=value; $("key").value=""; $("connect").textContent= value ? "模型设置" : "连接模型";
  $("connection").close(); if($("conversation").open) $("chat-status").textContent=value ? "密钥已保存到本页，发送时验证。" : "连接设置已更新。";
}
$("disconnect").onclick=()=>setKey("");
$("model-form").onsubmit=e=>{e.preventDefault();setKey($("key").value.trim());};
function bubble(text,user,turnId) {
  const b=el("div",user?"bubble user":"bubble");
  b.append(el("div","",text));
  if (turnId) {
    const btn=el("button","note-action","摘录记忆");
    btn.onclick=()=>openMemory({turn_id:turnId,speaker:user?"prompt":"reply"});
    b.append(btn);
  }
  $("thread").append(b);$("thread").scrollTop=$("thread").scrollHeight;
}
function enableChat(){
  const locked=state.pending.has(state.open);
  $("send").disabled=locked; $("message").disabled=locked; $("send").textContent=locked?"等待回复":"发送";
}
async function openChat(cid) {
  const gen=++state.loading; state.open=cid; $("chat-name").textContent="读取对话…"; $("chat-genre").textContent="";
  $("thread").replaceChildren(); $("chat-status").textContent=""; $("message").value=state.drafts.get(cid)||"";
  $("send").disabled=true; $("message").disabled=true; show($("conversation"));
  try {
    const data=await api("/api/chat/"+encodeURIComponent(cid)); if(gen!==state.loading) return;
    $("chat-name").textContent=data.card.name; $("chat-genre").textContent=data.card.genre;
    bubble(data.card.greeting,false);
    for(const turn of data.turns){bubble(turn.prompt,true,turn.id);bubble(turn.reply,false,turn.id);}
    enableChat(); if(!state.model?.enabled) $("chat-status").textContent="可先预览上下文；聊天需站主配置模型。";
  } catch(err){if(gen===state.loading) $("chat-status").textContent=err.message;}
}
$("message").oninput=()=>state.drafts.set(state.open,$("message").value);
$("conversation").addEventListener("close",()=>{state.open="";state.loading++;});
$("chat-form").onsubmit=async e=>{
  e.preventDefault(); const cid=state.open, prompt=$("message").value.trim();
  if(!prompt || !cid || state.pending.has(cid)) return;
  if(!state.model?.enabled || (state.model.key_required && !state.key)){await settings();return;}
  const previous=state.retries.get(cid);
  const nonce=previous && previous.prompt===prompt ? previous.nonce : crypto.randomUUID();
  state.retries.set(cid,{nonce,prompt});state.drafts.set(cid,prompt);state.pending.set(cid,true);
  enableChat();$("chat-status").textContent="角色正在回复…";
  try{
    await api("/api/chat/"+encodeURIComponent(cid),{prompt,nonce},state.key);
    state.retries.delete(cid); state.drafts.delete(cid);state.pending.delete(cid);
    if(state.open===cid) await openChat(cid);
  }catch(err){if(state.open===cid) $("chat-status").textContent=err.message;}
  finally{state.pending.delete(cid);if(state.open===cid){enableChat();$("message").focus();}}
};
Promise.all([api("/api/model").then(cfg=>{state.model=cfg;}),refresh()]).catch(err=>{$("notice").textContent=err.message;});
const notebook={cid:"", notes:[], turns:[], limit:12, source:null, editing:null, epoch:0};
function selectNote(note,source){
  notebook.editing=note; notebook.source=source;
  $("memory-topic").value=note?.topic||""; $("memory-topic").readOnly=Boolean(note);
  const turn=source && notebook.turns.find(t=>t.id===source.turn_id);
  const quote=turn?.[source?.speaker]||"";
  $("memory-fact").value=note?.fact ?? quote.slice(0,240);
  $("memory-source").textContent=quote ? "所选原话 · "+quote : "";
  $("memory-status").textContent="";
}
function drawNotes(){
  const box=$("memory-list");box.replaceChildren();
  $("context-count").textContent="下次发送包含角色设定、开场白、以下 "+notebook.notes.length+" 条记忆。实际纳入的对白可在上下文预览中查看。";
  for(const n of notebook.notes){
    const row=el("article","memory-row");
    row.append(el("strong","",n.topic),el("p","",n.fact));
    const index=notebook.turns.findIndex(t=>t.id===n.turn_id);
    if(index>=0){
      const source=el("details","memory-quote");
      source.append(el("summary","","来源 · 第 "+(index+1)+" 轮"+(n.speaker==="prompt"?"你的发言":"角色回复")),el("p","",notebook.turns[index][n.speaker]));
      row.append(source);
    }
    const edit=el("button","link","修订"),remove=el("button","link","移除");
    edit.onclick=()=>{selectNote(n,null);$("memory-fact").focus();};
    remove.onclick=()=>{if(window.confirm("从后续对话的上下文中移除这条记忆？")) updateNote({topic:n.topic,version:n.version,remove:true});};
    row.append(edit,remove);box.append(row);
  }
}
async function openMemory(source=null){
  const cid=state.open,epoch=++notebook.epoch;
  notebook.cid=cid; $("memory-save").disabled=true;
  $("memory-list").replaceChildren();$("context-count").textContent="正在读取…";selectNote(null,null);
  $("scene-form").querySelector("button").disabled=true; $("scene-status").textContent="";
  show($("memory-dialog"));
  try{
    const data=await api("/api/chat/"+encodeURIComponent(cid));
    if(epoch!==notebook.epoch)return;
    notebook.notes=data.memories;notebook.turns=data.turns;notebook.limit=data.recent_limit;
    $("adult-scene").checked=data.scene.adult; $("scene-boundaries").value=data.scene.boundaries;
    $("scene-form").querySelector("button").disabled=false;
    drawNotes();selectNote(null,source);$("memory-save").disabled=false;
  }catch(err){if(epoch===notebook.epoch)$("memory-status").textContent=err.message;}
}
async function updateNote(payload){
  const epoch=notebook.epoch, cid=notebook.cid;
  $("memory-save").disabled=true;
  try{
    const data=await api("/api/chat/"+encodeURIComponent(cid)+"/memory",payload);
    if(epoch!==notebook.epoch)return;
    notebook.notes=data.memories;drawNotes();selectNote(null,null);
    $("memory-status").textContent="已更新，从下一次发送起生效。";
  }catch(err){if(epoch===notebook.epoch)$("memory-status").textContent=err.message;}
  finally{if(epoch===notebook.epoch)$("memory-save").disabled=false;}
}
$("memory-open").onclick=()=>openMemory();
$("memory-dialog").addEventListener("close",()=>{notebook.epoch++;});
$("memory-reset").onclick=()=>selectNote(null,null);
$("memory-form").onsubmit=e=>{
  e.preventDefault();
  const topic=$("memory-topic").value.trim(),fact=$("memory-fact").value.trim();
  const existing=notebook.notes.find(n=>n.topic===topic);
  if(existing && !notebook.editing && !window.confirm("用这条新事实替换同主题的旧记忆？"))return;
  updateNote({topic,fact,version:(notebook.editing||existing)?.version||null,...notebook.source});
};

$("scene-form").onsubmit=async event=>{
  event.preventDefault();
  const epoch=notebook.epoch, button=event.currentTarget.querySelector("button");
  button.disabled=true;
  try{
    await api("/api/chat/"+encodeURIComponent(notebook.cid)+"/scene", {
      adult:$("adult-scene").checked, boundaries:$("scene-boundaries").value
    });
    if(epoch===notebook.epoch) $("scene-status").textContent="已保存，从下一次发送起生效。";
  }catch(err){if(epoch===notebook.epoch) $("scene-status").textContent=err.message;}
  finally{if(epoch===notebook.epoch) button.disabled=false;}
};

$("preview-open").onclick=async()=>{
  const cid=state.open, prompt=$("message").value;
  $("preview-content").replaceChildren(); $("preview-summary").textContent="正在读取…"; show($("preview"));
  try{
    const data=await api("/api/chat/"+encodeURIComponent(cid)+"/context",{prompt});
    if(state.open!==cid || !$("preview").open) return;
    $("preview-summary").textContent=data.memory_count+" 条记忆 · 最近 "+data.history_turns+" 轮 · 正文 "+data.characters+" 字符。"+(prompt.trim()?"已包含当前草稿。":"写下草稿后可预览完整请求。");
    for(const message of data.messages){
      const row=el("details","memory-row");
      row.append(el("summary","",{system:"角色与叙事设置",user:"访客消息或剧情记忆",assistant:"角色对白"}[message.role]),el("p","",message.content));
      $("preview-content").append(row);
    }
  }catch(err){$("preview-summary").textContent=err.message;}
};

$("export-card").onclick=()=>{
  const c=state.cards.find(card=>card.id===state.open);
  if(!c) return;
  const card={spec:"chara_card_v2",spec_version:"2.0",data:{
    name:c.name,description:c.persona,first_mes:c.greeting,personality:"",scenario:"",
    mes_example:"",creator_notes:c.description,system_prompt:"",post_history_instructions:"",
    alternate_greetings:[],tags:[c.genre],creator:"",character_version:"",
    extensions:{yezhan:{genre:c.genre,cover:c.cover,blurb:c.description}}
  }};
  const blob=new Blob([JSON.stringify(card,null,2)],{type:"application/json"});
  const url=URL.createObjectURL(blob), link=el("a");
  link.href=url; link.download=c.name+".json"; link.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
};

$("card-file").onchange=async event=>{
  const file=event.target.files[0], form=$("card-form");
  if(!file) return;
  try{
    if(file.size>256*1024) throw new Error("请选择小于 256 KiB 的角色卡 JSON。");
    const card=JSON.parse((await file.text()).replace(/^\uFEFF/,""));
    if(card?.spec!=="chara_card_v2" || !card.data) throw new Error("请使用 Character Card V2 JSON。");
    const d=card.data, ext=d.extensions?.yezhan || {};
    const values={name:d.name,persona:[d.description,d.personality,d.scenario].filter(Boolean).join("\n\n"),
      greeting:d.first_mes,description:ext.blurb ?? "",genre:ext.genre ?? (Array.isArray(d.tags)?d.tags[0]:"") ?? ""};
    for(const [key,value] of Object.entries(values)){
      const input=form.elements.namedItem(key);
      if(typeof value!=="string" || (input.required && !value.trim()) || value.length>input.maxLength)
        throw new Error("角色卡字段 "+key+" 缺失或超出长度，请先编辑 JSON。");
    }
    for(const [key,value] of Object.entries(values)) form.elements.namedItem(key).value=value;
    form.elements.cover.value=["forest","library","mountain"].includes(ext.cover)?ext.cover:"mountain";
    form.elements.public.checked=false;
    $("save-status").textContent="已读入基础设定。世界书、脚本、示例对白和额外开场白不会导入。";
  }catch(err){$("save-status").textContent=err instanceof SyntaxError?"文件不符合 JSON 格式。":err.message;}
  finally{event.target.value="";}
};
