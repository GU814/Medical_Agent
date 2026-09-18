/* ============ 医学问询智能体 · 桌面版前端逻辑 ============ */
"use strict";

const API = "";
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

const state = {
  token: localStorage.getItem("ma_token") || "",
  user: null,
  providers: [],
  defaultProvider: "",
  convs: [],
  currentConv: null,
  sending: false,
  reminders: [],
  firedReminders: new Set(JSON.parse(localStorage.getItem("ma_fired") || "[]")),
};

/* ---------------- 工具 ---------------- */
function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add("hidden"), ms);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* 轻量 markdown → HTML（粗体 / 列表 / 换行） */
function md(text) {
  const lines = esc(text).split("\n");
  let html = "", inUl = false;
  for (let line of lines) {
    line = line.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/^#{1,4}\s*(.+)$/, "<strong>$1</strong>");
    const li = line.match(/^\s*[-•*]\s+(.*)$/);
    if (li) {
      if (!inUl) { html += "<ul>"; inUl = true; }
      html += `<li>${li[1]}</li>`;
    } else {
      if (inUl) { html += "</ul>"; inUl = false; }
      if (line.trim()) html += `<p>${line}</p>`;
    }
  }
  if (inUl) html += "</ul>";
  return `<div class="md">${html}</div>`;
}

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (state.token) headers["Authorization"] = "Bearer " + state.token;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.json);
  }
  const res = await fetch(API + path, { ...opts, headers });
  if (res.status === 401) { logout(false); throw new Error("登录已过期"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `请求失败 (${res.status})`);
  return data;
}

function timeStr(ts) {
  const d = new Date((ts || 0) * 1000);
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/* ---------------- 认证 ---------------- */
let authMode = "login";
$$(".auth-tabs .tab").forEach(tab => tab.addEventListener("click", () => {
  $$(".auth-tabs .tab").forEach(t => t.classList.remove("active"));
  tab.classList.add("active");
  authMode = tab.dataset.mode;
  $("#auth-submit").textContent = authMode === "login" ? "登 录" : "注 册";
}));

$("#auth-submit").addEventListener("click", async () => {
  const username = $("#auth-username").value.trim();
  const password = $("#auth-password").value;
  $("#auth-error").textContent = "";
  try {
    const data = await api(`/api/auth/${authMode}`, { method: "POST", json: { username, password } });
    state.token = data.token;
    state.user = data.user;
    localStorage.setItem("ma_token", data.token);
    await enterApp();
  } catch (e) { $("#auth-error").textContent = e.message; }
});
$("#auth-password").addEventListener("keydown", e => { if (e.key === "Enter") $("#auth-submit").click(); });

function logout(clear = true) {
  if (clear) localStorage.removeItem("ma_token");
  state.token = ""; state.user = null;
  $("#app-page").classList.add("hidden");
  $("#auth-page").classList.remove("hidden");
}

$("#btn-logout").addEventListener("click", () => logout());

async function enterApp() {
  $("#auth-page").classList.add("hidden");
  $("#app-page").classList.remove("hidden");
  const _initial = (state.user.username || "U")[0].toUpperCase();
  $("#nav-avatar").textContent = _initial;
  const _logoutAv = $("#btn-logout"); if (_logoutAv) _logoutAv.textContent = _initial;
  try {
    const me = await api("/api/auth/me");
    state.providers = me.providers;
    state.defaultProvider = me.default_provider;
    renderModelSelect();
  } catch (e) { toast(e.message); }
  loadConversations();
  loadDocuments();
  loadProfile();
  loadReminders();
}

/* ---------------- 导航切换 ---------------- */
$$(".nav-icon[data-view]").forEach(btn => btn.addEventListener("click", () => {
  $$(".nav-icon[data-view]").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  $$(".view").forEach(v => v.classList.remove("active"));
  $(`#view-${btn.dataset.view}`).classList.add("active");
}));

/* ---------------- 模型选择 ---------------- */
function renderModelSelect() {
  const sel = $("#model-select");
  sel.innerHTML = "";
  state.providers.forEach(p => {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = `${p.label}${p.available ? "" : "（未配置）"}`;
    if (!p.available) opt.disabled = true;
    sel.appendChild(opt);
  });
  // 优先使用后端配置的 default_provider（若可用），否则选第一个可用项
  const preferred = state.providers.find(p => p.id === state.defaultProvider && p.available);
  const avail = state.providers.find(p => p.available);
  sel.value = preferred ? preferred.id : (avail ? avail.id : state.defaultProvider);
}
$("#model-select").addEventListener("change", (e) => toast(`已切换模型：${e.target.selectedOptions[0].textContent}`));

/* ---------------- 会话 ---------------- */
async function loadConversations() {
  try { state.convs = await api("/api/conversations"); } catch (e) { return; }
  renderConvList();
}

function renderConvList() {
  const q = ($("#conv-search").value || "").trim().toLowerCase();
  const box = $("#conv-list");
  box.innerHTML = "";
  state.convs
    .filter(c => !q || c.title.toLowerCase().includes(q))
    .forEach(c => {
      const el = document.createElement("div");
      el.className = "conv-item" + (state.currentConv?.id === c.id ? " active" : "");
      el.innerHTML = `
        <div class="t">${esc(c.title)}</div>
        <div class="s"><span>${c.message_count} 条对话</span>
          <button class="rename" title="整理标题">✦</button>
          <button class="del" title="删除">🗑</button></div>`;
      el.addEventListener("click", (ev) => {
        if (ev.target.classList.contains("del") || ev.target.classList.contains("rename")) return;
        openConversation(c.id);
      });
      el.querySelector(".rename").addEventListener("click", async (ev) => {
        ev.stopPropagation();
        try {
          const r = await api(`/api/conversations/${c.id}/summarize`, { method: "POST" });
          toast(`已整理标题：${r.title}`);
          loadConversations();
        } catch (e) { toast(e.message); }
      });
      el.querySelector(".del").addEventListener("click", async (ev) => {
        ev.stopPropagation();
        if (!confirm(`删除会话「${c.title}」？`)) return;
        try {
          await api(`/api/conversations/${c.id}`, { method: "DELETE" });
          if (state.currentConv?.id === c.id) { state.currentConv = null; renderMessages(); }
          loadConversations();
        } catch (e) { toast(e.message); }
      });
      box.appendChild(el);
    });
}
$("#conv-search").addEventListener("input", renderConvList);

async function startNewTopic() {
  if (state.sending) { toast("当前消息发送中，请稍候…"); return; }
  try {
    const conv = await api("/api/conversations", { method: "POST" });
    state.currentConv = conv;
    await loadConversations();
    renderMessages();
    $("#chat-input").value = "";
    $("#chat-input").focus();
    $("#chat-title").textContent = conv.title || "新的问诊";
    toast("新话题已经开好啦，说说这次哪里不舒服吧～");
  } catch (e) { toast(e.message); }
}

$("#btn-new-conv").addEventListener("click", startNewTopic);
$("#btn-new-topic").addEventListener("click", startNewTopic);

$("#btn-summarize-all").addEventListener("click", async () => {
  if (!confirm("将为当前账号下所有会话生成/刷新标题摘要？")) return;
  try {
    const r = await api("/api/conversations/summarize-all", { method: "POST" });
    toast(`已整理 ${r.count} 个会话标题`);
    loadConversations();
  } catch (e) { toast(e.message); }
});

async function openConversation(id) {
  try {
    state.currentConv = await api(`/api/conversations/${id}`);
    renderConvList();
    renderMessages();
  } catch (e) { toast(e.message); }
}

function renderMessages() {
  const box = $("#chat-messages");
  const conv = state.currentConv;
  if (!conv || !conv.messages?.length) {
    box.innerHTML = `<div class="empty-hint"><div class="empty-icon">🌿</div>
      <p>你好，我是你的健康小助手。<br>
      哪里不太舒服，慢慢跟我说说？我会陪你一步步理清楚，并参考你自己的知识库给你一些信息。</p></div>`;
    $("#chat-title").textContent = "新的问诊";
    return;
  }
  $("#chat-title").textContent = conv.title;
  box.innerHTML = "";
  conv.messages.forEach(m => box.appendChild(renderMsg(m.role, m.content, m.citations)));
  box.scrollTop = box.scrollHeight;
}

function renderMsg(role, content, citations) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  let cit = "";
  if (role === "assistant" && citations?.length) {
    cit = `<div class="citations"><div class="ct-title">📚 回答依据（知识库引用）</div>` +
      citations.map(c =>
        `<div class="citation">【来源：${esc(c.doc_name)}-片段${c.chunk_idx}】<span class="snip">${esc(c.snippet || c.text?.slice(0, 100))}</span></div>`
      ).join("") + `</div>`;
  }
  div.innerHTML = `<div class="avatar">${role === "user" ? "🙂" : "🌿"}</div>
    <div class="bubble">${role === "assistant" ? md(content) : esc(content)}${cit}</div>`;
  return div;
}

/* ---------------- SSE 流式对话 ---------------- */
$("#btn-send").addEventListener("click", sendMsg);
$("#chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMsg(); }
});
$("#chat-input").addEventListener("input", function () {
  this.style.height = "auto";
  this.style.height = Math.min(this.scrollHeight, 130) + "px";
});

async function sendMsg() {
  if (state.sending) return;
  const text = $("#chat-input").value.trim();
  if (!text) return;
  if (!state.currentConv) {
    try { state.currentConv = await api("/api/conversations", { method: "POST" }); loadConversations(); }
    catch (e) { toast(e.message); return; }
  }
  $("#chat-input").value = "";
  $("#chat-input").style.height = "auto";
  state.sending = true;
  $("#btn-send").disabled = true;

  const box = $("#chat-messages");
  if (box.querySelector(".empty-hint")) box.innerHTML = "";
  box.appendChild(renderMsg("user", text, null));
  const assistantDiv = document.createElement("div");
  assistantDiv.className = "msg assistant";
  assistantDiv.innerHTML = `<div class="avatar">🌿</div><div class="bubble typing"></div>`;
  box.appendChild(assistantDiv);
  const bubble = assistantDiv.querySelector(".bubble");
  box.scrollTop = box.scrollHeight;

  let answer = "", citations = [];
  const ctrl = new AbortController();
  const guardTimer = setTimeout(() => ctrl.abort(), 120000); // 120s 兜底，避免无限“思考”
  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + state.token },
      body: JSON.stringify({
        conversation_id: state.currentConv.id,
        message: text,
        provider: $("#model-select").value,
      }),
      signal: ctrl.signal,
    });
    if (res.status === 401) { logout(false); return; }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `请求失败 (${res.status})`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop();
      for (const part of parts) {
        if (!part.startsWith("data:")) continue;
        let ev; try { ev = JSON.parse(part.slice(5).trim()); } catch { continue; }
        if (ev.type === "meta") {
          citations = ev.citations || [];
          if (citations.length) {
            bubble.insertAdjacentHTML("beforeend",
              `<div class="citations"><div class="ct-title">📚 已检索到 ${citations.length} 条知识库依据</div></div>`);
          }
        } else if (ev.type === "delta") {
          answer += ev.content;
          renderStreamingBubble(bubble, answer, citations);
        } else if (ev.type === "error") {
          toast(ev.message, 4000);
        } else if (ev.type === "done") {
          if (ev.title && state.currentConv) {
            state.currentConv.title = ev.title;
            $("#chat-title").textContent = ev.title;
          }
        }
        box.scrollTop = box.scrollHeight;
      }
    }
  } catch (e) {
    if (e && e.name === "AbortError") {
      toast("请求超时：模型接口长时间无响应，请检查中转站/模型配置", 5000);
      if (!answer) answer = "（请求超时，模型接口未响应，请检查 config.yaml 中的接口配置）";
    } else {
      toast(e.message || "请求失败", 4000);
      if (!answer) answer = "（回答生成失败，请稍后重试或检查模型配置）";
    }
  } finally {
    clearTimeout(guardTimer);
  }
  bubble.classList.remove("typing");
  renderStreamingBubble(bubble, answer, citations, true);
  state.sending = false;
  $("#btn-send").disabled = false;
  loadConversations();
}

function renderStreamingBubble(bubble, answer, citations, final = false) {
  const citHtml = (final && citations.length) ? `<div class="citations">
    <div class="ct-title">📚 回答依据（知识库引用）</div>` +
    citations.map(c => `<div class="citation">【来源：${esc(c.doc_name)}-片段${c.chunk_idx}】
      <span class="snip">${esc(c.snippet)}</span></div>`).join("") + `</div>` : "";
  bubble.innerHTML = md(answer) + citHtml;
}

/* ---------------- 报告 ---------------- */
$("#btn-report").addEventListener("click", async () => {
  if (!state.currentConv || (state.currentConv.messages?.length || 0) < 2) {
    toast("当前会话对话内容不足，无法生成报告"); return;
  }
  toast("正在基于问诊对话生成检查报告…");
  try {
    const report = await api(`/api/conversations/${state.currentConv.id}/report`, { method: "POST" });
    showReport(report);
  } catch (e) { toast(e.message, 4000); }
});

function showReport(r) {
  const urgencyMap = { self_care: ["可先自我观察", "u-self_care"],
    outpatient: ["建议门诊就诊", "u-outpatient"], urgent: ["建议尽快就医", "u-urgent"] };
  const [uLabel, uCls] = urgencyMap[r.urgency] || ["—", "u-outpatient"];
  const sec = (t, v) => `<div class="report-section"><h4>${t}</h4><div>${md(String(v ?? "（问诊未涉及）"))}</div></div>`;
  const arr = (v) => Array.isArray(v) && v.length ? v.map(i => `<li>${esc(i)}</li>`).join("") : "<li>（无）</li>";
  $("#report-body").innerHTML = `
    ${sec("主诉", r.chief_complaint)}
    ${sec("现病史", r.present_illness)}
    ${sec("既往史 / 用药史 / 过敏史", r.past_history)}
    ${sec("问诊要点小结", r.consult_summary)}
    ${sec("初步分析（仅基于知识库与对话）", r.preliminary_analysis)}
    <div class="report-section"><h4>建议检查项目</h4><ul>${arr(r.suggested_exams)}</ul></div>
    <div class="report-section"><h4>生活建议</h4><ul>${arr(r.lifestyle_advice)}</ul></div>
    <div class="report-section"><h4>就医建议</h4>
      <span class="report-urgency ${uCls}">${uLabel}</span></div>
    <p style="color:#5c6470;font-size:12px;margin-top:10px">⚠️ ${esc(r.disclaimer || r.generated_note || "")}</p>`;
  $("#report-modal").classList.remove("hidden");
}
$("#btn-close-report").addEventListener("click", () => $("#report-modal").classList.add("hidden"));

/* ---------------- 知识库 ---------------- */
async function loadDocuments() {
  let docs = [];
  try { docs = await api("/api/kb/documents"); } catch (e) { return; }
  const box = $("#kb-doc-list");
  box.innerHTML = "";
  if (!docs.length) {
    box.innerHTML = `<div class="empty-hint" style="height:200px"><div class="empty-icon">📚</div>
      <p>知识库还是空的。先把文档传上来，后面问诊时我就会引用里面的内容当作依据。</p></div>`;
    return;
  }
  docs.forEach(d => {
    const el = document.createElement("div");
    el.className = "kb-doc";
    const icon = d.name.endsWith(".pdf") ? "📕" : d.name.endsWith(".docx") ? "📘" : "📄";
    el.innerHTML = `<div class="icon">${icon}</div>
      <div class="info"><div class="name">${esc(d.name)}</div>
      <div class="meta">${(d.size / 1024).toFixed(0)} KB · ${d.chunk_count} 个分块 · ${timeStr(d.uploaded_at)} 上传</div></div>
      <button class="del">删除</button>`;
    el.querySelector(".del").addEventListener("click", async () => {
      if (!confirm(`删除文档「${d.name}」？相关引用将失效。`)) return;
      try { await api(`/api/kb/documents/${d.id}`, { method: "DELETE" }); loadDocuments(); }
      catch (e) { toast(e.message); }
    });
    box.appendChild(el);
  });
}

async function uploadFiles(files) {
  for (const f of files) {
    if (!/\.(pdf|docx|md|markdown|txt)$/i.test(f.name)) { toast(`跳过不支持的文件：${f.name}`); continue; }
    const fd = new FormData();
    fd.append("file", f);
    toast(`正在上传并索引：${f.name}`);
    try {
      const r = await api("/api/kb/upload", { method: "POST", body: fd });
      toast(`✅ ${r.name} 已入库（${r.chunk_count} 个分块）`);
    } catch (e) { toast(`${f.name}：${e.message}`, 4000); }
  }
  loadDocuments();
}

$("#btn-upload").addEventListener("click", () => $("#kb-file").click());
$("#kb-file").addEventListener("change", (e) => { uploadFiles([...e.target.files]); e.target.value = ""; });
const drop = $("#kb-drop");
["dragover", "dragleave", "drop"].forEach(ev => drop.addEventListener(ev, (e) => {
  e.preventDefault();
  drop.classList.toggle("dragover", ev === "dragover");
  if (ev === "drop") uploadFiles([...e.dataTransfer.files]);
}));

$("#btn-kb-test").addEventListener("click", async () => {
  const q = $("#kb-test-query").value.trim();
  if (!q) return;
  try {
    const results = await api("/api/kb/search", { method: "POST", json: { query: q, top_k: 5 } });
    const box = $("#kb-test-result");
    box.classList.remove("hidden");
    box.innerHTML = results.length
      ? results.map(r => `<div class="r"><b>【${esc(r.doc_name)}-片段${r.chunk_idx}】</b>(相关度 ${r.score})<br>${esc(r.snippet)}</div>`).join("")
      : "未检索到相关内容（上传文档或换关键词试试）";
  } catch (e) { toast(e.message); }
});

/* ---------------- 知识库：分块分析 / 检索验证 / 知识图谱 ---------------- */
// 子页签切换
$$(".kb-tab").forEach(t => t.addEventListener("click", () => {
  $$(".kb-tab").forEach(x => x.classList.remove("active"));
  t.classList.add("active");
  const tab = t.dataset.tab;
  $$(".kb-tabpanel").forEach(p => p.classList.add("hidden"));
  const panel = $("#tab-" + tab);
  if (panel) panel.classList.remove("hidden");
  if (tab === "chunks") loadChunkDocs();
  if (tab === "graph") loadGraph();
}));

/* ---------- 分块分析 ---------- */
async function loadChunkDocs() {
  const sel = $("#chunk-doc-select");
  let docs = [];
  try { docs = await api("/api/kb/documents"); } catch (e) { return; }
  sel.innerHTML = docs.length
    ? docs.map(d => `<option value="${esc(d.id)}">${esc(d.name)}（${d.chunk_count} 块）</option>`).join("")
    : `<option value="">（暂无文档，请先上传）</option>`;
}

$("#btn-chunk-preview").addEventListener("click", async () => {
  const docId = $("#chunk-doc-select").value;
  if (!docId) { toast("请先在上方选择文档"); return; }
  const body = {
    doc_id: docId,
    strategy: $("#chunk-strategy").value,
    chunk_size: Number($("#chunk-size").value) || 500,
    chunk_overlap: Number($("#chunk-overlap").value) || 80,
  };
  try {
    const r = await api("/api/kb/preview-chunks", { method: "POST", json: body });
    renderChunkStats($("#chunk-stats"), r.stats, r.chunk_count);
    renderChunkList($("#chunk-list"), r.chunks);
  } catch (e) { toast(e.message, 4000); }
});

$("#btn-chunk-evaluate").addEventListener("click", async () => {
  const docId = $("#chunk-doc-select").value;
  if (!docId) { toast("请先在上方选择文档"); return; }
  const body = {
    doc_id: docId,
    strategy: $("#chunk-strategy").value,
    chunk_size: Number($("#chunk-size").value) || 500,
    chunk_overlap: Number($("#chunk-overlap").value) || 80,
    top_k: 5, sample: 30,
  };
  toast("正在评估检索效果…");
  try {
    const r = await api("/api/kb/evaluate", { method: "POST", json: body });
    renderCompare($("#chunk-compare"), r);
  } catch (e) { toast(e.message, 4000); }
});

function renderChunkStats(box, s, count) {
  if (!s) { box.innerHTML = ""; return; }
  box.classList.remove("hidden");
  box.innerHTML = `
    <div class="stat-grid">
      <div class="stat"><span class="n">${count}</span><span class="l">分块数</span></div>
      <div class="stat"><span class="n">${s.avg}</span><span class="l">平均长度</span></div>
      <div class="stat"><span class="n">${s.min}</span><span class="l">最短</span></div>
      <div class="stat"><span class="n">${s.max}</span><span class="l">最长</span></div>
    </div>
    <div class="stat-row">过短(&lt;200字) <b>${s.too_short}</b> · 过长(&gt;1500字) <b>${s.too_long}</b> · 平均相邻重叠 <b>${s.overlap_avg}</b></div>`;
}

function renderChunkList(box, chunks) {
  box.innerHTML = chunks.map(c => {
    let tag = c.len < 200 ? `<span class="tag bad">过短</span>`
      : c.len > 1500 ? `<span class="tag warn">过长</span>`
        : `<span class="tag ok">良好</span>`;
    return `<div class="chunk-card">
      <div class="chunk-head"><b>片段 ${c.idx}</b> · ${c.len} 字 ${tag}</div>
      <div class="chunk-text">${esc(c.text)}</div></div>`;
  }).join("");
}

function renderCompare(box, r) {
  const card = (name, o) => {
    const strat = o.strategy === "stored" ? "当前已存" : `${o.strategy} · ${o.chunk_size}字/重叠${o.chunk_overlap}`;
    return `<div class="cmp-card">
      <div class="cmp-title">${name}（${strat}）</div>
      <div class="cmp-stat">分块数 <b>${o.chunk_count}</b> · 平均长度 <b>${o.stats?.avg ?? "-"}</b></div>
      <div class="cmp-cov">自身可检索率 <b>${Math.round((o.coverage || 0) * 100)}%</b>
        <span class="muted">（命中 ${o.hits}/${o.total}）</span></div>
    </div>`;
  };
  box.classList.remove("hidden");
  box.innerHTML = `<div class="cmp-row">${card("基线：当前已存分块", r.baseline)}${card("候选：所选参数", r.candidate)}</div>
    <div class="cmp-note">「自身可检索率」= 各分块自身内容作为查询时，能否在 top_k 检索结果中被命中的比例。比例越高，说明该分块策略越便于被精准检索到。若候选优于基线，可在上传时改用对应分块参数。</div>`;
}

/* ---------- 检索验证 ---------- */
async function runVerify(body) {
  toast("正在验证检索命中…");
  try {
    const r = await api("/api/kb/verify", { method: "POST", json: body });
    renderVerify($("#verify-summary"), $("#verify-feedback"), $("#verify-list"), r);
  } catch (e) { toast(e.message, 4000); }
}
$("#btn-verify-run").addEventListener("click", () => {
  const raw = $("#verify-input").value.trim();
  const queries = raw ? raw.split("\n").map(s => s.trim()).filter(Boolean) : null;
  runVerify({ queries, auto: !queries, sample: Number($("#verify-sample").value) || 30, top_k: 5 });
});
$("#btn-verify-auto").addEventListener("click", () => {
  runVerify({ queries: null, auto: true, sample: Number($("#verify-sample").value) || 30, top_k: 5 });
});

function renderVerify(summaryBox, fbBox, listBox, r) {
  const pct = Math.round((r.coverage || 0) * 100);
  summaryBox.classList.remove("hidden");
  summaryBox.innerHTML = `整体可检索率 <b>${pct}%</b> · 命中 ${r.hits}/${r.total}
    <span class="vbar"><i style="width:${pct}%"></i></span>`;
  const hits = (r.results || []).filter(x => x.hit).length;
  fbBox.innerHTML = `<span class="pill ok">命中 ${hits}</span> <span class="pill bad">未命中 ${r.total - hits}</span>
    <span class="fb-tip">未命中的查询代表对应内容在检索时可能落空，建议调整分块策略或补充相关知识。</span>`;
  listBox.innerHTML = (r.results || []).map(x => `
    <div class="verify-item ${x.hit ? "hit" : "miss"}">
      <div class="vq">${esc(x.query)}</div>
      <div class="vr">${x.hit
        ? `<span class="tag ok">命中</span> 来源 <b>${esc(x.best_doc || "-")}</b> · 相关度 ${(x.best_score || 0)}`
        : `<span class="tag bad">未命中</span>`}</div>
      ${x.snippet ? `<div class="vs">${esc(x.snippet)}</div>` : ""}
    </div>`).join("");
}

/* ---------- 知识图谱 ---------- */
let gCanvas, gData = null, gNodes = [], gLinks = [], gPos = {}, gVel = {},
  gSel = null, gDragId = null, gAlpha = 1, gRaF = null, gMoved = false;
const REL_COLOR = { cause: "#e54545", complication: "#d4880f", coexist: "#0089ff", related: "#8a94a6" };
const REL_LABEL = { cause: "可能引发", complication: "并发症", coexist: "伴随", related: "相关" };
// 节点按系统分类着色，层级分明
const CAT_COLOR = {
  "心脑血管": "#e54545", "呼吸系统": "#0089ff", "消化系统": "#d4880f",
  "泌尿系统": "#2d6db5", "血液系统": "#a040bf", "内分泌代谢": "#0aa37f",
  "风湿免疫": "#c0392b", "神经精神": "#6c5ce7", "皮肤科": "#e08e0b",
  "妇儿": "#16a085", "骨骼肌肉": "#7f8c8d", "耳鼻喉口腔": "#34495e", "其他": "#8a94a6",
};

async function loadGraph() {
  try {
    const g = await api("/api/kb/graph?rebuild=false");
    gData = g; initGraph(g);
  } catch (e) { toast(e.message, 4000); }
}

function renderGraphTree(g) {
  const box = $("#graph-tree");
  const tree = g.tree || [];
  if (!tree.length) { box.innerHTML = `<div class="gt-empty">暂无分类节点，上传知识后自动生成</div>`; return; }
  box.innerHTML = tree.map(t => {
    const col = CAT_COLOR[t.category] || "#8a94a6";
    return `<div class="gt-cat"><div class="gt-head" data-cat="${esc(t.category)}">
      <i class="gt-dot" style="background:${col}"></i>
      <span class="gt-name">${esc(t.category)}</span>
      <span class="gt-count">${t.count}</span></div>
      <div class="gt-nodes">${(t.nodes || []).map(id => {
        const n = (g.nodes || []).find(x => x.id === id);
        return `<button class="gt-node" data-node="${esc(id)}">${esc(id)}${n && n.freq ? `<i>${n.freq}</i>` : ""}</button>`;
      }).join("")}</div></div>`;
  }).join("");
  box.querySelectorAll(".gt-node").forEach(b => b.addEventListener("click", () => {
    selectNode(b.dataset.node);
  }));
}

function selectNode(id) {
  if (!gNodes.find(n => n.id === id)) return;
  gSel = id;
  gAlpha = Math.max(gAlpha, 0.4);
  renderGraphSide(id);
}

function initGraph(g) {
  gCanvas = $("#graph-canvas");
  gNodes = (g.nodes || []).slice();
  gLinks = g.links || [];
  renderGraphTree(g);
  const rect = gCanvas.getBoundingClientRect();
  const W = gCanvas.width = Math.max(320, rect.width);
  const H = gCanvas.height = Math.max(360, rect.height || 460);
  gPos = {}; gVel = {};
  const R = Math.min(W, H) / 2 - 50;
  gNodes.forEach((n, i) => {
    const a = (i / Math.max(1, gNodes.length)) * Math.PI * 2;
    gPos[n.id] = {
      x: W / 2 + Math.cos(a) * R * (0.4 + Math.random() * 0.6),
      y: H / 2 + Math.sin(a) * R * (0.4 + Math.random() * 0.6),
    };
    gVel[n.id] = { x: 0, y: 0 };
  });
  gAlpha = 1;
  if (!gCanvas._bound) {
    gCanvas.addEventListener("mousedown", onCanvasDown);
    gCanvas.addEventListener("mousemove", onCanvasMove);
    gCanvas.addEventListener("click", onCanvasClick);
    gCanvas._bound = true;
  }
  if (gRaF) cancelAnimationFrame(gRaF);
  simLoop();
}

function simLoop() {
  if (gAlpha > 0.02 || gDragId) {
    tick();
    gAlpha = gDragId ? 0.3 : gAlpha * 0.985;
  }
  drawGraph();
  gRaF = requestAnimationFrame(simLoop);
}

function tick() {
  const W = gCanvas.width, H = gCanvas.height, k = gAlpha;
  for (let i = 0; i < gNodes.length; i++) {
    for (let j = i + 1; j < gNodes.length; j++) {
      const pa = gPos[gNodes[i].id], pb = gPos[gNodes[j].id];
      let dx = pa.x - pb.x, dy = pa.y - pb.y;
      const d2 = dx * dx + dy * dy + 0.01, d = Math.sqrt(d2);
      const f = 800 / d2;
      gVel[gNodes[i].id].x += dx / d * f * k; gVel[gNodes[i].id].y += dy / d * f * k;
      gVel[gNodes[j].id].x -= dx / d * f * k; gVel[gNodes[j].id].y -= dy / d * f * k;
    }
  }
  gLinks.forEach(l => {
    const pa = gPos[l.source], pb = gPos[l.target];
    if (!pa || !pb) return;
    let dx = pb.x - pa.x, dy = pb.y - pa.y;
    const d = Math.sqrt(dx * dx + dy * dy) + 0.01;
    const f = (d - 95) * 0.02 * k;
    gVel[l.source].x += dx / d * f; gVel[l.source].y += dy / d * f;
    gVel[l.target].x -= dx / d * f; gVel[l.target].y -= dy / d * f;
  });
  gNodes.forEach(n => {
    const p = gPos[n.id];
    gVel[n.id].x += (W / 2 - p.x) * 0.005 * k;
    gVel[n.id].y += (H / 2 - p.y) * 0.005 * k;
  });
  gNodes.forEach(n => {
    if (n.id === gDragId) return;
    const p = gPos[n.id], v = gVel[n.id];
    p.x = Math.max(18, Math.min(W - 18, p.x + v.x));
    p.y = Math.max(18, Math.min(H - 18, p.y + v.y));
    v.x *= 0.85; v.y *= 0.85;
  });
}

function drawGraph() {
  const ctx = gCanvas.getContext("2d");
  const W = gCanvas.width, H = gCanvas.height;
  ctx.clearRect(0, 0, W, H);
  gLinks.forEach(l => {
    const pa = gPos[l.source], pb = gPos[l.target];
    if (!pa || !pb) return;
    const rel = (l.rels && l.rels[0]) || "related";
    const col = REL_COLOR[rel] || "#8a94a6";
    ctx.strokeStyle = `rgba(140,148,166,${l.directed ? 0.55 : 0.3})`;
    ctx.lineWidth = Math.min(4, 1 + (l.weight || 1) * 0.5);
    ctx.beginPath(); ctx.moveTo(pa.x, pa.y); ctx.lineTo(pb.x, pb.y); ctx.stroke();
    if (l.directed) drawArrow(ctx, pa, pb, col);
  });
  gNodes.forEach(n => {
    const p = gPos[n.id];
    const r = 6 + Math.min(13, (n.freq || 1) * 2.2);
    ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
    const baseCol = CAT_COLOR[n.category] || "#2d6db5";
    ctx.fillStyle = n.id === gSel ? "#0089ff" : baseCol;
    ctx.fill();
    if (n.id === gSel) { ctx.lineWidth = 3; ctx.strokeStyle = "#0089ff"; ctx.stroke(); }
    ctx.fillStyle = "#171a1f"; ctx.font = "12px sans-serif"; ctx.textAlign = "center";
    ctx.fillText(n.label, p.x, p.y - r - 5);
  });
}

function drawArrow(ctx, a, b, color) {
  const dx = b.x - a.x, dy = b.y - a.y, ang = Math.atan2(dy, dx), len = Math.hypot(dx, dy);
  if (len < 16) return;
  const r = 9, sx = b.x - Math.cos(ang) * r, sy = b.y - Math.sin(ang) * r, ah = 8;
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(sx, sy);
  ctx.lineTo(sx - Math.cos(ang - 0.4) * ah, sy - Math.sin(ang - 0.4) * ah);
  ctx.lineTo(sx - Math.cos(ang + 0.4) * ah, sy - Math.sin(ang + 0.4) * ah);
  ctx.closePath(); ctx.fill();
}

function canvasPos(e) {
  const rect = gCanvas.getBoundingClientRect();
  return { x: e.clientX - rect.left, y: e.clientY - rect.top };
}
function nodeAt(x, y) {
  for (const n of gNodes) {
    const p = gPos[n.id], r = 6 + Math.min(13, (n.freq || 1) * 2.2);
    if ((x - p.x) ** 2 + (y - p.y) ** 2 <= (r + 4) ** 2) return n;
  }
  return null;
}
window.addEventListener("mouseup", () => { gDragId = null; });
function onCanvasDown(e) { const p = canvasPos(e), n = nodeAt(p.x, p.y); if (n) { gDragId = n.id; gSel = n.id; gMoved = false; renderGraphSide(n.id); } }
function onCanvasMove(e) { if (!gDragId) return; const p = canvasPos(e); gPos[gDragId].x = p.x; gPos[gDragId].y = p.y; gVel[gDragId] = { x: 0, y: 0 }; gAlpha = Math.max(gAlpha, 0.3); gMoved = true; }
function onCanvasClick(e) {
  // 拖拽释放后浏览器仍会触发 click，此时位置已离开原节点会误清空侧栏 → 抑制
  if (gMoved) { gMoved = false; return; }
  const p = canvasPos(e), n = nodeAt(p.x, p.y);
  if (n) { gSel = n.id; renderGraphSide(n.id); } else { gSel = null; clearGraphSide(); }
}

function renderGraphSide(id) {
  const node = gNodes.find(n => n.id === id);
  if (!node) { clearGraphSide(); return; }
  const side = $("#graph-side");
  const caused = (gData.directed_links || []).filter(l => l.source === id);
  const catCol = CAT_COLOR[node.category] || "#2d6db5";
  let html = `<div class="gs-head"><i class="gs-dot" style="background:${catCol}"></i>${esc(node.label)}
    <span class="gs-cat">${esc(node.category || "")}</span></div>
    <div class="gs-meta">出现 ${node.freq || 0} 次 · 关联文档 ${node.doc_count || 0}</div>`;
  if (caused.length) {
    html += `<div class="gs-sub">⚠️ 可能引发的其他关联病情（点击追溯）</div><div class="gs-list">` +
      caused.map(l => {
        const t = gNodes.find(n => n.id === l.target);
        const tags = (l.rels || []).map(r => `<span class="gs-tag ${r}">${REL_LABEL[r] || r}</span>`).join("");
        return `<button class="gs-item gs-btn" data-node="${esc(l.target)}">→ <b>${esc(t ? t.label : l.target)}</b> ${tags}</button>`;
      }).join("") + `</div>`;
  } else {
    html += `<div class="gs-empty">该病情在知识库中未记录明确的「引发→」指向关系。</div>`;
  }
  const related = gLinks.filter(l => l.source === id || l.target === id).map(l => {
    const other = l.source === id ? l.target : l.source;
    const t = gNodes.find(n => n.id === other);
    const rel = (l.rels && l.rels[0]) || "related";
    return `<button class="gs-item gs-btn" data-node="${esc(other)}">↔ <b>${esc(t ? t.label : other)}</b> <span class="gs-tag ${rel}">${REL_LABEL[rel] || rel}</span></button>`;
  });
  if (related.length) html += `<div class="gs-sub">关联节点（点击追溯）</div><div class="gs-list">${related.join("")}</div>`;
  side.innerHTML = html;
  // 关联项点击 → 选中目标节点并高亮追溯
  side.querySelectorAll(".gs-btn").forEach(b => b.addEventListener("click", () => {
    selectNode(b.dataset.node);
  }));
}
function clearGraphSide() {
  $("#graph-side").innerHTML = `<div class="graph-empty">点击节点查看病情关联与「可能引发的其他关联病情」</div>`;
}

$("#btn-graph-search").addEventListener("click", () => {
  const q = $("#graph-search").value.trim();
  if (!q) return;
  const n = gNodes.find(x => x.label.includes(q)) || gNodes.find(x => x.id.includes(q));
  if (n) { selectNode(n.id); }
  else toast("未找到该病情节点");
});
$("#btn-graph-rebuild").addEventListener("click", async () => {
  toast("正在重建知识图谱…");
  try {
    const g = await api("/api/kb/graph/rebuild", { method: "POST" });
    gData = g; initGraph(g); toast("✅ 图谱已重建");
  } catch (e) { toast(e.message, 4000); }
});
window.addEventListener("resize", () => { if (gCanvas && gNodes.length) { gAlpha = Math.max(gAlpha, 0.2); } });

/* ---------------- 识药（图片 + 视频）---------------- */
let medicineFile = null;       // 图片文件
let medicineVideoFile = null;  // 视频文件
let medicineMode = "image";    // image | video

// 模式切换
$$(".mmtab").forEach(t => t.addEventListener("click", () => {
  $$(".mmtab").forEach(x => x.classList.remove("active"));
  t.classList.add("active");
  medicineMode = t.dataset.mmode;
  $("#mpane-image").classList.toggle("hidden", medicineMode !== "image");
  $("#mpane-video").classList.toggle("hidden", medicineMode !== "video");
}));

// 图片上传
const mDrop = $("#medicine-drop");
mDrop.addEventListener("click", () => $("#medicine-file").click());
["dragover", "dragleave", "drop"].forEach(ev => mDrop.addEventListener(ev, (e) => {
  e.preventDefault();
  mDrop.classList.toggle("dragover", ev === "dragover");
  if (ev === "drop" && e.dataTransfer.files[0]) setMedicineFile(e.dataTransfer.files[0]);
}));
$("#medicine-file").addEventListener("change", (e) => { if (e.target.files[0]) setMedicineFile(e.target.files[0]); });

function setMedicineFile(f) {
  if (!f.type.startsWith("image/")) { toast("请上传图片文件"); return; }
  medicineFile = f;
  const url = URL.createObjectURL(f);
  $("#medicine-preview").src = url;
  $("#medicine-preview").classList.remove("hidden");
  $("#medicine-placeholder").classList.add("hidden");
}

// 视频上传
const mvDrop = $("#medicine-video-drop");
mvDrop.addEventListener("click", () => $("#medicine-video-file").click());
["dragover", "dragleave", "drop"].forEach(ev => mvDrop.addEventListener(ev, (e) => {
  e.preventDefault();
  mvDrop.classList.toggle("dragover", ev === "dragover");
  if (ev === "drop" && e.dataTransfer.files[0]) setMedicineVideoFile(e.dataTransfer.files[0]);
}));
$("#medicine-video-file").addEventListener("change", (e) => { if (e.target.files[0]) setMedicineVideoFile(e.target.files[0]); });

function setMedicineVideoFile(f) {
  if (!f.type.startsWith("video/") && !/\.(mp4|mov|webm|avi)$/i.test(f.name)) {
    toast("请上传视频文件（mp4/mov/webm/avi）"); return;
  }
  if (f.size > 32 * 1024 * 1024) { toast("视频超过 32MB 限制"); return; }
  medicineVideoFile = f;
  const url = URL.createObjectURL(f);
  const v = $("#medicine-video-preview");
  v.src = url; v.classList.remove("hidden");
  $("#medicine-video-placeholder").classList.add("hidden");
}

$("#btn-medicine-reset").addEventListener("click", () => {
  medicineFile = null; medicineVideoFile = null;
  $("#medicine-preview").classList.add("hidden");
  $("#medicine-placeholder").classList.remove("hidden");
  const vp = $("#medicine-video-preview"); vp.src = ""; vp.classList.add("hidden");
  $("#medicine-video-placeholder").classList.remove("hidden");
  ["#medicine-result", "#medicine-interactions", "#medicine-risks", "#medicine-frames"]
    .forEach(s => $(s).classList.add("hidden"));
});

function renderMedicineResult(r) {
  const box = $("#medicine-result");
  box.classList.remove("hidden");
  box.className = "medicine-result" + (r.needs_retake ? " retake" : "");
  box.innerHTML = md(r.result) +
    `<p style="margin-top:10px;color:#5c6470;font-size:12px">识别模型：${esc(r.model)} · 结果仅供参考，用药请遵医嘱</p>`;

  // 不可混合使用的药物
  const iBox = $("#medicine-interactions");
  if (r.interactions && r.interactions.trim()) {
    iBox.classList.remove("hidden");
    iBox.innerHTML = `<div class="mi-head">⚠️ 不可混合使用的药物</div><div class="mi-body">${md(r.interactions)}</div>`;
  } else { iBox.classList.add("hidden"); }

  // 潜在风险
  const rBox = $("#medicine-risks");
  if (r.risks && r.risks.trim()) {
    rBox.classList.remove("hidden");
    rBox.innerHTML = `<div class="mi-head risk">⚡ 潜在风险</div><div class="mi-body">${md(r.risks)}</div>`;
  } else { rBox.classList.add("hidden"); }

  // 视频帧明细
  const fBox = $("#medicine-frames");
  if (r.frames && r.frames.length) {
    fBox.classList.remove("hidden");
    fBox.innerHTML = `<div class="mi-head frame">🎬 抽帧明细（共 ${r.frame_count || r.frames.length} 帧）</div>` +
      r.frames.map(f => `<div class="frame-item ${f.ok ? "ok" : "bad"}"><b>帧 ${f.frame}</b> ${
        f.ok ? `<span class="tag ok">可识别</span>` : `<span class="tag bad">${f.error ? "出错" : "模糊"}</span>`
      }<div class="fsnip">${esc(f.snippet || f.error || "")}</div></div>`).join("");
  } else { fBox.classList.add("hidden"); }
}

$("#btn-medicine-analyze").addEventListener("click", async () => {
  const btn = $("#btn-medicine-analyze");
  btn.disabled = true; btn.textContent = "识别中…";
  try {
    const fd = new FormData();
    if (medicineMode === "video") {
      if (!medicineVideoFile) { toast("请先选择药物视频"); btn.disabled = false; btn.textContent = "开始识别"; return; }
      fd.append("file", medicineVideoFile);
      const r = await api("/api/vision/medicine-video", { method: "POST", body: fd });
      renderMedicineResult(r);
    } else {
      if (!medicineFile) { toast("请先选择药物照片"); btn.disabled = false; btn.textContent = "开始识别"; return; }
      fd.append("file", medicineFile);
      const r = await api("/api/vision/medicine", { method: "POST", body: fd });
      renderMedicineResult(r);
    }
  } catch (e) { toast(e.message, 4500); }
  btn.disabled = false; btn.textContent = "开始识别";
});

/* ---------------- 医学报告库 ---------------- */
let reportsCat = null;       // 当前分类筛选
let reportsKw = "";          // 当前搜索词

$(".nav-icon[data-view=reports]").addEventListener("click", () => {
  loadReportCategories();
  loadReports();
});
$("#report-cats").addEventListener("click", (e) => {
  const b = e.target.closest(".rc-cat");
  if (!b) return;
  reportsCat = b.dataset.cat === "__all" ? null : b.dataset.cat;
  $$("#report-cats .rc-cat").forEach(x => x.classList.remove("active"));
  b.classList.add("active");
  loadReports();
});
$("#reports-search").addEventListener("input", (e) => {
  reportsKw = e.target.value.trim();
  loadReports();
});

async function loadReportCategories() {
  try {
    const cats = await api("/api/reports/categories");
    const total = cats.reduce((s, c) => s + (c.count || 0), 0);
    const all = `<button class="rc-cat ${reportsCat === null ? "active" : ""}" data-cat="__all">全部 <i>${total}</i></button>`;
    $("#report-cats").innerHTML = all + cats.map(c =>
      `<button class="rc-cat ${reportsCat === c.category ? "active" : ""}" data-cat="${esc(c.category)}">
        <span class="rc-dot" style="background:${CAT_COLOR[c.category] || "#8a94a6"}"></span>
        ${esc(c.category)} <i>${c.count}</i></button>`).join("");
  } catch (e) { /* 静默 */ }
}

async function loadReports() {
  try {
    const q = reportsKw ? `&q=${encodeURIComponent(reportsKw)}` : "";
    const c = reportsCat ? `category=${encodeURIComponent(reportsCat)}&` : "";
    const list = await api(`/api/reports?${c}${q}`);
    $("#reports-count").textContent = `共 ${list.length} 份报告`;
    if (!list.length) {
      $("#reports-list").innerHTML = `<div class="reports-empty"><div class="empty-icon">📄</div>
        <p>还没有报告哦。在「智能问诊」里聊完，点一下「整理一份检查报告」，就会自动存到这里。</p></div>`;
      return;
    }
    $("#reports-list").innerHTML = list.map(r => {
      const col = CAT_COLOR[r.category] || "#8a94a6";
      return `<div class="report-card">
        <div class="rc-head"><span class="rc-badge" style="background:${col}">${esc(r.category || "其他")}</span>
          <span class="rc-time">${timeStr(r.created_at)}</span></div>
        <div class="rc-title">${esc(r.title || "未命名报告")}</div>
        <div class="rc-chief">主诉：${esc(r.chief_complaint || "—")}</div>
        ${r.summary ? `<div class="rc-summary">${esc(r.summary)}</div>` : ""}
        <div class="rc-actions">
          <button class="btn-ghost btn-mini" data-view-rid="${esc(r.rid)}">查看</button>
          <button class="btn-ghost btn-mini danger" data-del-rid="${esc(r.rid)}">删除</button>
        </div></div>`;
    }).join("");
    $("#reports-list").querySelectorAll("[data-view-rid]").forEach(b =>
      b.addEventListener("click", () => viewReport(b.dataset.viewRid)));
    $("#reports-list").querySelectorAll("[data-del-rid]").forEach(b =>
      b.addEventListener("click", async () => {
        if (!confirm("确定删除该报告？")) return;
        try {
          await api(`/api/reports/${b.dataset.delRid}`, { method: "DELETE" });
          toast("已删除");
          loadReportCategories(); loadReports();
        } catch (e) { toast(e.message); }
      }));
  } catch (e) { toast(e.message); }
}

async function viewReport(rid) {
  try {
    const r = await api(`/api/reports/${rid}`);
    showReport(r);
  } catch (e) { toast(e.message); }
}

/* ---------------- 健康档案 ---------------- */
async function loadProfile() {
  try {
    const p = await api("/api/profile");
    $$("#profile-form [data-field]").forEach(el => {
      const v = p[el.dataset.field];
      if (v !== null && v !== undefined) el.value = v;
    });
    renderRecords(p.records || []);
    loadSuggestions();
  } catch (e) { /* 未登录时静默 */ }
}

$("#btn-save-profile").addEventListener("click", async () => {
  const body = {};
  $$("#profile-form [data-field]").forEach(el => {
    const v = el.value.trim();
    body[el.dataset.field] = el.type === "number" ? (v ? Number(v) : null) : v;
  });
  try { await api("/api/profile", { method: "PUT", json: body }); toast("✅ 档案已保存"); }
  catch (e) { toast(e.message); }
});

function renderRecords(records) {
  const box = $("#record-list");
  box.innerHTML = records.length ? "" :
    `<div style="color:#5c6470;font-size:13px">暂无记录，添加症状/用药/体检记录可让问诊与建议更精准</div>`;
  records.forEach(r => {
    const el = document.createElement("div");
    el.className = "record";
    el.innerHTML = `<span class="type">${esc(r.type)}</span><span class="content">${esc(r.content)}</span>
      <span class="date">${esc(r.date)}</span><button class="del">✕</button>`;
    el.querySelector(".del").addEventListener("click", async () => {
      try {
        const p = await api(`/api/profile/records/${r.id}`, { method: "DELETE" });
        renderRecords(p.records || []);
      } catch (e) { toast(e.message); }
    });
    box.appendChild(el);
  });
}

$("#btn-add-record").addEventListener("click", async () => {
  const content = $("#record-content").value.trim();
  if (!content) { toast("请输入记录内容"); return; }
  try {
    const p = await api("/api/profile/records", { method: "POST",
      json: { type: $("#record-type").value, content } });
    $("#record-content").value = "";
    renderRecords(p.records || []);
    toast("✅ 已添加健康记录");
  } catch (e) { toast(e.message); }
});

async function loadSuggestions() {
  const box = $("#profile-suggestions");
  box.innerHTML = `<div class="suggestion"><span class="si">⏳</span>正在为你想想有什么可以注意的…</div>`;
  try {
    const r = await api("/api/profile/suggestions");
    const icons = ["💧", "🏃", "💊", "😴", "🥗", "🧘"];
    box.innerHTML = (r.suggestions || []).map((s, i) =>
      `<div class="suggestion"><span class="si">${icons[i % icons.length]}</span>${esc(s)}</div>`).join("");
  } catch (e) { box.innerHTML = `<div class="suggestion">建议加载失败</div>`; }
}
$("#btn-refresh-suggestions").addEventListener("click", loadSuggestions);

/* ---------------- 提醒 ---------------- */
async function loadReminders() {
  try { state.reminders = await api("/api/reminders"); } catch (e) { return; }
  renderReminders();
}

function renderReminders() {
  const box = $("#reminder-list");
  box.innerHTML = state.reminders.length ? "" :
    `<div style="color:#5c6470;font-size:13px">暂无提醒，创建吃药、复诊、喝水等提醒闹钟</div>`;
  state.reminders.forEach(r => {
    const el = document.createElement("div");
    el.className = "reminder" + (r.enabled ? "" : " disabled");
    const timeLabel = r.repeat === "once" ? r.time : `每天 ${r.time}`;
    el.innerHTML = `<span class="r-icon">${r.enabled ? "🔔" : "🔕"}</span>
      <div class="r-info"><div class="r-title">${esc(r.title)}</div>
        <div class="r-meta">${esc(timeLabel)}${r.note ? " · " + esc(r.note) : ""}</div></div>
      <span class="r-time">${esc(r.time.slice(-5))}</span>
      <button class="switch ${r.enabled ? "on" : ""}" title="开关"></button>
      <button class="del">🗑</button>`;
    el.querySelector(".switch").addEventListener("click", async () => {
      try {
        await api(`/api/reminders/${r.id}`, { method: "PATCH", json: { enabled: !r.enabled } });
        loadReminders();
      } catch (e) { toast(e.message); }
    });
    el.querySelector(".del").addEventListener("click", async () => {
      try { await api(`/api/reminders/${r.id}`, { method: "DELETE" }); loadReminders(); }
      catch (e) { toast(e.message); }
    });
    box.appendChild(el);
  });
}

$("#reminder-repeat").addEventListener("change", (e) => {
  $("#reminder-date").hidden = e.target.value !== "once";
});

$("#btn-add-reminder").addEventListener("click", async () => {
  const title = $("#reminder-title").value.trim();
  const repeat = $("#reminder-repeat").value;
  let time = $("#reminder-time").value;
  if (!title) { toast("请输入提醒内容"); return; }
  if (repeat === "once") {
    const d = $("#reminder-date").value;
    if (!d) { toast("单次提醒请选择日期"); return; }
    time = `${d} ${time}`;
  }
  if (!time) { toast("请选择提醒时间"); return; }
  try {
    await api("/api/reminders", { method: "POST",
      json: { title, time, repeat, note: "", enabled: true } });
    $("#reminder-title").value = "";
    loadReminders();
    toast("✅ 提醒已创建");
  } catch (e) { toast(e.message); }
});

/* 到期提醒检查（每 30 秒轮询 + 浏览器通知） */
setInterval(() => {
  if (!state.reminders.length) return;
  const now = new Date();
  const hhmm = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  state.reminders.forEach(r => {
    if (!r.enabled) return;
    const due = r.repeat === "daily" ? hhmm : r.time.slice(0, 10) === today ? r.time.slice(11) : null;
    const key = `${r.id}|${today}|${due}`;
    if (due && due === hhmm && !state.firedReminders.has(key)) {
      state.firedReminders.add(key);
      localStorage.setItem("ma_fired", JSON.stringify([...state.firedReminders]));
      toast(`⏰ 提醒：${r.title}`, 8000);
      if (Notification.permission === "granted") new Notification("健康提醒", { body: r.title });
    }
  });
}, 30000);
if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();

/* ---------------- 启动 ---------------- */
(async function init() {
  if (!state.token) { $("#auth-page").classList.remove("hidden"); return; }
  try {
    const me = await api("/api/auth/me");
    state.user = me.user;
    await enterApp();
  } catch {
    localStorage.removeItem("ma_token");
    state.token = "";
    $("#auth-page").classList.remove("hidden");
  }
})();
