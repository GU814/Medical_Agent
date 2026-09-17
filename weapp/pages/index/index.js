const { request } = require("../../utils/api");

function greet() {
  const h = new Date().getHours();
  if (h < 6) return "夜深了，注意休息";
  if (h < 11) return "早上好";
  if (h < 14) return "中午好";
  if (h < 18) return "下午好";
  return "晚上好";
}

Page({
  data: {
    user: null,
    greeting: "",
    suggestions: [],
    reminders: [],
    todayDue: [],
    today: "",
    loading: false,
  },
  onShow() {
    const user = wx.getStorageSync("ma_user");
    this.setData({ user, greeting: greet() });
    if (!user) return;
    this.refresh();
  },
  onPullDownRefresh() {
    if (!this.data.user) { wx.stopPullDownRefresh(); return; }
    this.refresh(() => wx.stopPullDownRefresh());
  },
  refresh(done) {
    this.setData({ loading: true });
    const now = new Date();
    const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    this.setData({ today });
    request("/api/profile/suggestions")
      .then((r) => this.setData({ suggestions: r.suggestions || [] }))
      .catch(() => {});
    request("/api/reminders")
      .then((items) => {
        const due = (items || []).filter((r) => r.enabled &&
          (r.repeat === "daily" || (r.time || "").slice(0, 10) === today));
        this.setData({ reminders: items || [], todayDue: due.slice(0, 4) });
      })
      .catch(() => {})
      .then(() => { this.setData({ loading: false }); done && done(); });
  },
  goLogin() {
    wx.switchTab({ url: "/pages/me/me" });
  },
  go(e) {
    wx.switchTab({ url: e.currentTarget.dataset.url });
  },
});
