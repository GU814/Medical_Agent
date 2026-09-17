const { request, sseChat } = require("../../utils/api");

Page({
  data: {
    loggedIn: false,
    convs: [],
    conv: null,
    messages: [],
    input: "",
    sending: false,
    providers: [],
    providerIndex: 0,
    intoView: "",
    showReport: false,
    report: null,
    welcome: "你好，我是你的健康小助手。哪里不舒服？慢慢说，我会陪你一步步了解，并尽量给出有依据的建议。",
  },
  onShow() {
    const token = wx.getStorageSync("ma_token");
    this.setData({ loggedIn: !!token });
    if (!token) return;
    this.loadProviders();
    this.loadConvs();
  },
  onPullDownRefresh() {
    if (!this.data.loggedIn) { wx.stopPullDownRefresh(); return; }
    this.loadConvs(() => wx.stopPullDownRefresh());
  },
  loadProviders() {
    request("/api/auth/me").then((r) => {
      const providers = (r.providers || []).filter((p) => p.available);
      this.setData({ providers, providerIndex: 0 });
    }).catch(() => {});
  },
  loadConvs(done) {
    request("/api/conversations").then((convs) => {
      this.setData({ convs: convs || [] });
      if (!this.data.conv && convs && convs.length) this.openConv(convs[0].id);
    }).catch(() => {}).then(() => done && done());
  },
  newConv() {
    request("/api/conversations", { method: "POST" }).then((conv) => {
      this.setData({ conv, messages: [] });
      this.loadConvs();
    }).catch((e) => wx.showToast({ title: e.message, icon: "none" }));
  },
  openConv(id) {
    request(`/api/conversations/${id}`).then((conv) => {
      this.setData({ conv, messages: conv.messages || [] });
      this.scrollBottom();
    }).catch(() => {});
  },
  onConvTap(e) {
    this.openConv(e.currentTarget.dataset.id);
  },
  onProviderChange(e) {
    this.setData({ providerIndex: Number(e.detail.value) });
  },
  onInput(e) {
    this.setData({ input: e.detail.value });
  },
  send() {
    const text = (this.data.input || "").trim();
    if (!text || this.data.sending) return;
    if (!this.data.conv) { this.newConvThenSend(text); return; }
    this.doSend(text);
  },
  newConvThenSend(text) {
    request("/api/conversations", { method: "POST" }).then((conv) => {
      this.setData({ conv });
      this.loadConvs();
      this.doSend(text);
    }).catch((e) => wx.showToast({ title: e.message, icon: "none" }));
  },
  doSend(text) {
    const messages = this.data.messages.concat([{ role: "user", content: text }]);
    const assistant = { role: "assistant", content: "", citations: [] };
    this.setData({ messages: messages.concat([assistant]), input: "", sending: true });
    this.scrollBottom();
    const idx = this.data.messages.length - 1;
    const provider = this.data.providers[this.data.providerIndex];
    sseChat({
      conversation_id: this.data.conv.id,
      message: text,
      provider: provider ? provider.id : null,
    }, {
      onMeta: (citations) => {
        this.setData({ [`messages[${idx}].citations`]: citations || [] });
        this.scrollBottom();
      },
      onDelta: (delta) => {
        this.setData({ [`messages[${idx}].content`]: this.data.messages[idx].content + delta });
        this.scrollBottom();
      },
      onError: (err) => {
        wx.showToast({ title: err.message.slice(0, 40), icon: "none", duration: 3000 });
      },
      onDone: () => {
        this.setData({ sending: false });
        this.loadConvs();
      },
    });
    // 兜底：30 秒仍 sending 且无内容则解锁
    setTimeout(() => {
      if (this.data.sending && !this.data.messages[idx].content) {
        this.setData({ sending: false });
      }
    }, 30000);
  },
  scrollBottom() {
    this.setData({ intoView: "msg-bottom" });
  },
  genReport() {
    if (!this.data.conv || this.data.messages.length < 2) {
      wx.showToast({ title: "对话内容不足", icon: "none" });
      return;
    }
    wx.showLoading({ title: "生成报告中" });
    request(`/api/conversations/${this.data.conv.id}/report`, { method: "POST" })
      .then((report) => {
        wx.hideLoading();
        this.setData({ showReport: true, report });
        wx.showToast({ title: "已存入「我的报告」", icon: "success" });
      })
      .catch((e) => {
        wx.hideLoading();
        wx.showToast({ title: e.message.slice(0, 40), icon: "none", duration: 3000 });
      });
  },
  closeReport() {
    this.setData({ showReport: false });
  },
  goLogin() {
    wx.switchTab({ url: "/pages/me/me" });
  },
});
