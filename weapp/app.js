// 微信小程序 · 知医（医学问询智能体）
App({
  onLaunch() {
    const token = wx.getStorageSync("ma_token") || "";
    const user = wx.getStorageSync("ma_user") || null;
    this.globalData.token = token;
    this.globalData.user = user;
  },
  globalData: {
    token: "",
    user: null,
    provider: "",
  },
});
