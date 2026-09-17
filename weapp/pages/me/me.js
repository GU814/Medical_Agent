const { request, fmtDate } = require("../../utils/api");

const FIELDS = [
  { key: "nickname", label: "昵称", type: "text" },
  { key: "gender", label: "性别", type: "select", options: ["", "男", "女"] },
  { key: "birthday", label: "出生日期", type: "date" },
  { key: "height_cm", label: "身高(cm)", type: "number" },
  { key: "weight_kg", label: "体重(kg)", type: "number" },
  { key: "blood_type", label: "血型", type: "select", options: ["", "A", "B", "AB", "O"] },
  { key: "allergies", label: "过敏史", type: "text" },
  { key: "chronic", label: "慢性病史", type: "text" },
  { key: "current_medications", label: "在服药物", type: "text" },
];

Page({
  data: {
    loggedIn: false,
    mode: "login",
    username: "",
    password: "",
    fields: FIELDS,
    profile: {},
    records: [],
    reminders: [],
    // 新提醒
    rTitle: "",
    rTime: "08:00",
    rRepeatIndex: 0,
    repeatOptions: ["每天", "单次"],
    rDate: "",
    tab: "profile",   // profile | reminders | reports
    // 报告库
    reports: [],
    reportDetail: null,
    reportLoading: false,
  },
  onShow() {
    const loggedIn = !!wx.getStorageSync("ma_token");
    this.setData({ loggedIn });
    if (loggedIn) { this.loadProfile(); this.loadReminders(); this.loadReports(); }
  },
  onPullDownRefresh() {
    if (!this.data.loggedIn) { wx.stopPullDownRefresh(); return; }
    this.loadProfile(); this.loadReminders(); this.loadReports();
    wx.stopPullDownRefresh();
  },
  // ---------- 登录 ----------
  setMode(e) { this.setData({ mode: e.currentTarget.dataset.mode }); },
  onUser(e) { this.setData({ username: e.detail.value }); },
  onPwd(e) { this.setData({ password: e.detail.value }); },
  submitAuth() {
    const { mode, username, password } = this.data;
    if (!username || !password) {
      wx.showToast({ title: "请输入用户名和密码", icon: "none" });
      return;
    }
    request(`/api/auth/${mode}`, { method: "POST", data: { username, password } })
      .then((r) => {
        wx.setStorageSync("ma_token", r.token);
        wx.setStorageSync("ma_user", r.user);
        this.setData({ loggedIn: true, username: "", password: "" });
        this.loadProfile();
        this.loadReminders();
        this.loadReports();
      })
      .catch((e) => wx.showToast({ title: e.message.slice(0, 40), icon: "none" }));
  },
  logout() {
    wx.removeStorageSync("ma_token");
    wx.removeStorageSync("ma_user");
    this.setData({ loggedIn: false, profile: {}, records: [], reminders: [], reports: [], reportDetail: null });
  },
  // ---------- 档案 ----------
  loadProfile() {
    request("/api/profile").then((p) => {
      this.setData({ profile: p, records: p.records || [] });
    }).catch(() => {});
  },
  onField(e) {
    const key = e.currentTarget.dataset.key;
    this.setData({ [`profile.${key}`]: e.detail.value });
  },
  saveProfile() {
    request("/api/profile", { method: "PUT", data: this.data.profile })
      .then(() => wx.showToast({ title: "已保存", icon: "success" }))
      .catch((e) => wx.showToast({ title: e.message.slice(0, 40), icon: "none" }));
  },
  addRecord() {
    wx.showModal({
      title: "添加健康记录",
      editable: true,
      placeholderText: "如：今晨血压 128/85",
      success: (r) => {
        if (!r.confirm || !r.content) return;
        request("/api/profile/records", { method: "POST",
          data: { type: "其他", content: r.content } })
          .then((p) => { this.setData({ records: p.records || [] }); wx.showToast({ title: "已添加", icon: "success" }); })
          .catch(() => {});
      },
    });
  },
  delRecord(e) {
    request(`/api/profile/records/${e.currentTarget.dataset.id}`, { method: "DELETE" })
      .then((p) => this.setData({ records: p.records || [] }))
      .catch(() => {});
  },
  // ---------- 提醒 ----------
  loadReminders() {
    request("/api/reminders").then((items) => this.setData({ reminders: items || [] })).catch(() => {});
  },
  onRTitle(e) { this.setData({ rTitle: e.detail.value }); },
  onRTime(e) { this.setData({ rTime: e.detail.value }); },
  onRDate(e) { this.setData({ rDate: e.detail.value }); },
  onRRepeat(e) { this.setData({ rRepeatIndex: Number(e.detail.value) }); },
  addReminder() {
    const { rTitle, rTime, rRepeatIndex, rDate } = this.data;
    if (!rTitle) { wx.showToast({ title: "请输入提醒内容", icon: "none" }); return; }
    const repeat = rRepeatIndex === 0 ? "daily" : "once";
    let time = rTime;
    if (repeat === "once") {
      if (!rDate) { wx.showToast({ title: "请选择日期", icon: "none" }); return; }
      time = `${rDate} ${rTime}`;
    }
    request("/api/reminders", { method: "POST",
      data: { title: rTitle, time, repeat, note: "", enabled: true } })
      .then(() => {
        this.setData({ rTitle: "" });
        this.loadReminders();
        wx.showToast({ title: "已创建", icon: "success" });
      })
      .catch((e) => wx.showToast({ title: e.message.slice(0, 40), icon: "none" }));
  },
  toggleReminder(e) {
    const item = this.data.reminders.find((r) => r.id === e.currentTarget.dataset.id);
    if (!item) return;
    request(`/api/reminders/${item.id}`, { method: "PATCH", data: { enabled: !item.enabled } })
      .then(() => this.loadReminders()).catch(() => {});
  },
  delReminder(e) {
    request(`/api/reminders/${e.currentTarget.dataset.id}`, { method: "DELETE" })
      .then(() => this.loadReminders()).catch(() => {});
  },
  // ---------- 报告库 ----------
  loadReports() {
    request("/api/reports").then((items) => this.setData({ reports: items || [] })).catch(() => {});
  },
  viewReport(e) {
    const rid = e.currentTarget.dataset.rid;
    this.setData({ reportLoading: true, reportDetail: null });
    request(`/api/reports/${rid}`).then((rep) => {
      this.setData({ reportDetail: rep });
    }).catch((e) => wx.showToast({ title: e.message.slice(0, 40), icon: "none" }))
      .then(() => this.setData({ reportLoading: false }));
  },
  closeReport() {
    this.setData({ reportDetail: null });
  },
  delReport(e) {
    const rid = e.currentTarget.dataset.rid;
    wx.showModal({
      title: "删除报告",
      content: "确定删除这份报告？删除后不可恢复。",
      success: (r) => {
        if (!r.confirm) return;
        request(`/api/reports/${rid}`, { method: "DELETE" })
          .then(() => { this.loadReports(); if (this.data.reportDetail) this.setData({ reportDetail: null }); })
          .catch((e) => wx.showToast({ title: e.message.slice(0, 40), icon: "none" }));
      },
    });
  },
  fmt(ts) { return fmtDate(ts); },
  setTab(e) { this.setData({ tab: e.currentTarget.dataset.tab }); },
});
