// API 封装：BASE_URL + 登录态 + 流式(SSE) + 文件上传
//
// ⚠️ 部署必看：把下面的 BASE_URL 改成你的「已备案 HTTPS 域名」后端地址
//   （小程序要求 request 合法域名必须为 https 且已完成 ICP 备案）。
//   本地调试可先在微信开发者工具右上角「详情 → 本地设置」勾选
//   「不校验合法域名…」，或在下方填入局域网地址（如 http://192.168.1.5:8600）。
//   也支持在 Storage 中写入 ma_baseurl 覆盖默认值，便于切换环境。

// 环境切换：开发用 DEV_BASE，发布前把 USE_PROD 改为 true 并填好 PROD_BASE
const DEV_BASE = "http://127.0.0.1:8600";        // 模拟器调试（需勾选"不校验合法域名"）
const LAN_BASE = "http://192.168.1.5:8600";      // 真机同 WiFi 调试（改成你电脑的局域网 IP）

// 生产后端地址（占位模板：上线前必须替换）：
//   ⚠️ 小程序要求 request 合法域名必须为「已备案 HTTPS 域名」，并在微信公众平台
//      后台「开发 → 开发管理 → 服务器域名 → request 合法域名」中登记该域名。
//   ✅ 推荐做法（免改代码）：在 Storage 写入键 `ma_baseurl` = 你的 HTTPS 域名，
//      会覆盖下方的 PROD_BASE（见下方 BASE_URL 计算）。
const PROD_BASE = "https://api.example.com"; // TODO(上线): 替换为已备案 HTTPS 后端域名
const USE_PROD = false; // 默认开发模式走 DEV_BASE；上线前改 true（或 Storage 写 ma_baseurl）

// 优先级：Storage 中的 ma_baseurl（生产切换首选） > PROD_BASE/DEV_BASE
const STORED = wx.getStorageSync("ma_baseurl");
const BASE_URL = STORED || (USE_PROD ? PROD_BASE : DEV_BASE);

function token() {
  return wx.getStorageSync("ma_token") || "";
}

function request(path, opts = {}) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: BASE_URL + path,
      method: opts.method || "GET",
      data: opts.data,
      header: {
        "Content-Type": "application/json",
        Authorization: "Bearer " + token(),
        ...(opts.header || {}),
      },
      success(res) {
        if (res.statusCode === 401) {
          wx.removeStorageSync("ma_token");
          wx.removeStorageSync("ma_user");
          reject(new Error("登录已过期，请重新登录"));
          return;
        }
        if (res.statusCode >= 400) {
          reject(new Error((res.data && res.data.detail) || "请求失败 " + res.statusCode));
          return;
        }
        resolve(res.data);
      },
      fail(err) {
        reject(new Error("网络连接失败，请确认后端已启动且域名已配置"));
      },
    });
  });
}

function uploadFile(path, filePath, name = "file") {
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: BASE_URL + path,
      filePath,
      name,
      header: { Authorization: "Bearer " + token() },
      success(res) {
        let data = res.data;
        try { data = JSON.parse(data); } catch (e) { /* keep */ }
        if (res.statusCode >= 400) {
          reject(new Error((data && data.detail) || "上传失败"));
          return;
        }
        resolve(data);
      },
      fail() { reject(new Error("上传失败，请检查网络")); },
    });
  });
}

// UTF-8 ArrayBuffer 解码（onChunkReceived 用）
function utf8Decode(buffer) {
  const bytes = new Uint8Array(buffer);
  let out = "", i = 0;
  while (i < bytes.length) {
    const b = bytes[i];
    if (b < 0x80) { out += String.fromCharCode(b); i += 1; }
    else if (b < 0xe0) { out += String.fromCharCode(((b & 0x1f) << 6) | (bytes[i + 1] & 0x3f)); i += 2; }
    else if (b < 0xf0) {
      out += String.fromCharCode(((b & 0x0f) << 12) | ((bytes[i + 1] & 0x3f) << 6) | (bytes[i + 2] & 0x3f));
      i += 3;
    } else {
      const cp = ((b & 0x07) << 18) | ((bytes[i + 1] & 0x3f) << 12) | ((bytes[i + 2] & 0x3f) << 6) | (bytes[i + 3] & 0x3f);
      const off = cp - 0x10000;
      out += String.fromCharCode(0xd800 + (off >> 10), 0xdc00 + (off & 0x3ff));
      i += 4;
    }
  }
  return out;
}

// SSE 流式请求（小程序 wx.request enableChunked + onChunkReceived 原生支持分块读取）
function sseChat(payload, callbacks) {
  const task = wx.request({
    url: BASE_URL + "/api/chat/stream",
    method: "POST",
    enableChunked: true,
    header: {
      "Content-Type": "application/json",
      Authorization: "Bearer " + token(),
    },
    data: payload,
    success() { /* 结束由 chunks 处理 */ },
    fail(err) {
      callbacks.onError && callbacks.onError(new Error("连接失败：" + (err.errMsg || "")));
    },
  });
  let buf = "";
  task.onChunkReceived((res) => {
    buf += utf8Decode(res.data);
    const parts = buf.split("\n\n");
    buf = parts.pop();
    parts.forEach((part) => {
      if (!part.startsWith("data:")) return;
      let ev;
      try { ev = JSON.parse(part.slice(5).trim()); } catch (e) { return; }
      if (ev.type === "delta") callbacks.onDelta && callbacks.onDelta(ev.content);
      else if (ev.type === "meta") callbacks.onMeta && callbacks.onMeta(ev.citations);
      else if (ev.type === "error") callbacks.onError && callbacks.onError(new Error(ev.message));
      else if (ev.type === "done") callbacks.onDone && callbacks.onDone();
    });
  });
  return task;
}

function fmtDate(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

module.exports = { BASE_URL, request, uploadFile, sseChat, fmtDate };
