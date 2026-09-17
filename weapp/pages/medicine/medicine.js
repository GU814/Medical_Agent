const { uploadFile } = require("../../utils/api");

Page({
  data: {
    loggedIn: false,
    mode: "image",          // image | video
    mediaPath: "",
    analyzing: false,
    result: "",
    interactions: "",       // 不可混用药物
    risks: "",              // 潜在风险
    needsRetake: false,
  },
  onShow() {
    this.setData({ loggedIn: !!wx.getStorageSync("ma_token") });
  },
  setMode(e) {
    const m = e.currentTarget.dataset.mode;
    if (m === this.data.mode) return;
    this.setData({ mode: m, mediaPath: "", result: "", interactions: "", risks: "", needsRetake: false });
  },
  choose() {
    const isVideo = this.data.mode === "video";
    wx.chooseMedia({
      count: 1,
      mediaType: isVideo ? ["video"] : ["image"],
      sourceType: ["camera", "album"],
      sizeType: ["compressed"],
      maxDuration: 30,
      success: (res) => {
        this.setData({
          mediaPath: res.tempFiles[0].tempFilePath,
          result: "", interactions: "", risks: "", needsRetake: false,
        });
      },
    });
  },
  analyze() {
    if (!this.data.mediaPath) {
      wx.showToast({ title: this.data.mode === "video" ? "请先录制或选择视频" : "请先拍照或选择图片", icon: "none" });
      return;
    }
    const path = this.data.mode === "video" ? "/api/vision/medicine-video" : "/api/vision/medicine";
    this.setData({ analyzing: true, result: "", interactions: "", risks: "", needsRetake: false });
    uploadFile(path, this.data.mediaPath)
      .then((r) => this.setData({
        result: r.result || "",
        interactions: r.interactions || "",
        risks: r.risks || "",
        needsRetake: !!r.needs_retake,
      }))
      .catch((e) => wx.showToast({ title: e.message.slice(0, 40), icon: "none", duration: 3500 }))
      .then(() => this.setData({ analyzing: false }));
  },
  reset() {
    this.setData({ mediaPath: "", result: "", interactions: "", risks: "", needsRetake: false });
  },
  goLogin() {
    wx.switchTab({ url: "/pages/me/me" });
  },
});
