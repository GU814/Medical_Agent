const { request, uploadFile } = require("../../utils/api");

Page({
  data: {
    loggedIn: false,
    docs: [],
    uploading: false,
    searchQuery: "",
    searching: false,
    results: [],
    searched: false,
  },
  onShow() {
    this.setData({ loggedIn: !!wx.getStorageSync("ma_token") });
    if (this.data.loggedIn) this.loadDocs();
  },
  onPullDownRefresh() {
    if (!this.data.loggedIn) { wx.stopPullDownRefresh(); return; }
    this.loadDocs(() => wx.stopPullDownRefresh());
  },
  loadDocs(done) {
    request("/api/kb/documents")
      .then((docs) => this.setData({ docs: docs || [] }))
      .catch(() => {})
      .then(() => done && done());
  },
  chooseFile() {
    wx.chooseMessageFile({
      count: 5,
      type: "file",
      extension: ["pdf", "docx", "md", "markdown", "txt"],
      success: (res) => this.uploadAll(res.tempFiles.map((f) => f.path)),
      fail: () => wx.showToast({ title: "可从微信聊天记录里选择文档", icon: "none" }),
    });
  },
  uploadAll(paths) {
    if (!paths.length) return;
    this.setData({ uploading: true });
    wx.showLoading({ title: "上传并索引中" });
    const queue = paths.slice();
    const next = () => {
      if (!queue.length) {
        wx.hideLoading();
        this.setData({ uploading: false });
        this.loadDocs();
        return;
      }
      const p = queue.shift();
      uploadFile("/api/kb/upload", p).then((r) => {
        wx.showToast({ title: `已入库 ${r.chunk_count} 块`, icon: "success" });
        next();
      }).catch((e) => {
        wx.hideLoading();
        this.setData({ uploading: false });
        wx.showToast({ title: e.message.slice(0, 40), icon: "none", duration: 3000 });
        next();
      });
    };
    next();
  },
  deleteDoc(e) {
    const { id, name } = e.currentTarget.dataset;
    wx.showModal({
      title: "删除文档",
      content: `确定删除「${name}」？相关引用将失效。`,
      success: (r) => {
        if (!r.confirm) return;
        request(`/api/kb/documents/${id}`, { method: "DELETE" })
          .then(() => this.loadDocs())
          .catch((err) => wx.showToast({ title: err.message, icon: "none" }));
      },
    });
  },
  onSearchInput(e) {
    this.setData({ searchQuery: e.detail.value });
  },
  search() {
    const q = (this.data.searchQuery || "").trim();
    if (!q) { wx.showToast({ title: "请输入要检索的内容", icon: "none" }); return; }
    this.setData({ searching: true, searched: true });
    request("/api/kb/search", { method: "POST", data: { query: q, top_k: 5 } })
      .then((res) => this.setData({ results: res || [] }))
      .catch((e) => wx.showToast({ title: e.message.slice(0, 40), icon: "none" }))
      .then(() => this.setData({ searching: false }));
  },
  clearSearch() {
    this.setData({ searchQuery: "", results: [], searched: false });
  },
  goLogin() {
    wx.switchTab({ url: "/pages/me/me" });
  },
});
