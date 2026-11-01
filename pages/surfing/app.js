/* surfing-the-web dashboard. Titles and thoughts came from the web: they are put in with textContent. */
(function () {
  "use strict";
  const K = window.PanelKit;
  const { el, clear, card, badge, empty, time, clock, ago } = K;
  const $ = (id) => document.getElementById(id);
  const api = K.makeApi("上网逛逛");
  const SOURCE = {
    bilibili_popular: "B站热门", bilibili_ranking: "B站排行", bilibili_search: "B站搜索", web_search: "网页搜索",
    gcores: "机核", ithome: "IT之家", sspai: "少数派", v2ex: "V2EX", github_trending: "GitHub 趋势",
    hackernews: "Hacker News",
  };
  const BILI = { ok: ["登录着", "good"], expired: ["登录过期了", "bad"], none: ["没登录", "warn"], off: ["没开（设置里关了读视频）", ""] };
  let polling = null;
  let goWatch = null;

  function render(d) {
    const notes = [];
    if (!d.enabled) notes.push(el("div", { class: "note" }, "插件设置里关掉了：她不会按计划自己出门（“现在让她出门”仍然能用）。"));
    if (!d.can.chat_model) notes.push(el("div", { class: "note" }, "没有可用的聊天模型：她出不了门。"));
    if (!d.memory_core) notes.push(el("div", { class: "note" }, "没装记忆核心：看到的只记在插件自己的记录里，不会变成她的记忆。"));
    if (d.troubles.length) notes.push(el("div", { class: "note" }, "上网有点不顺：", el("ul", {}, d.troubles.map((t) => el("li", {}, t)))));
    clear($("notes"), notes);

    const planned = d.plan.length, done = d.plan.filter((p) => p.done).length;
    clear($("cards"),
      card("今天按计划出门", `${done} / ${planned}`, planned ? "" : "今天没有计划"),
      card("她自己去的", `${d.self_used} / ${d.self_per_day}`, "每天最多"),
      card(`${d.owner}让她去的`, `${d.asked_used} / ${d.asked_per_day}`, "不占她自己的次数"),
      card("下次能出门", d.resting_until ? clock(d.resting_until) : "现在", d.resting_until ? "刚出过门，在歇着" : "",
        d.resting_until ? "warn" : ""));

    clear($("plan"), d.plan.length
      ? el("div", { class: "chips" }, d.plan.map((p) => el("span", { class: "badge " + (p.done ? "good" : "info") },
        (p.done ? "✓ " : "") + clock(p.t))))
      : empty("今天没有计划（新的一天开始时排）"),
    el("div", { class: "small muted", style: "margin-top:8px" },
      "常去：" + (d.sources.map((s) => SOURCE[s] || s).join("、") || "（没有）")),
    el("div", { class: "small muted" }, "搜索：" + (d.searches.join("、") || "（没开）")));

    const [biliText, biliCls] = BILI[d.can.bili] || [d.can.bili, ""];
    clear($("can"), el("table", {},
      el("tr", {}, el("td", {}, "B站视频的字幕"), el("td", {}, badge(biliText, biliCls))),
      el("tr", {}, el("td", {}, "网页搜索和网页正文"), el("td", {}, d.can.web ? badge("可以（DeepSeek）", "good") : badge("没有 DeepSeek key", "bad"))),
      el("tr", {}, el("td", {}, "没字幕时看视频画面"), el("td", {}, d.can.frames ? badge("可以", "good") : badge("没有能看图的模型", "warn")))));

    renderWishes(d.wishes);

    clear($("recent"), d.recent.length ? d.recent.map((r) => el("div", { class: "item" },
      el("div", { class: "meta" }, time(r.at), badge(r.site)),
      el("div", {}, r.title),
      r.thought ? el("div", { class: "thought" }, "她当时想：" + r.thought) : null,
      el("div", { class: "small muted" }, r.url))) : empty("还没出过门"));

    clear($("outings"), d.outings.length ? el("table", {},
      el("tr", {}, el("th", {}, "时间"), el("th", {}, "去了"), el("th", {}, "搜"), el("th", { class: "num" }, "刷了"),
        el("th", { class: "num" }, "点开"), el("th", {}, "出错")),
      d.outings.map((o) => el("tr", {},
        el("td", { class: "when" }, time(o.at)),
        el("td", {}, (o.sources || []).map((s) => SOURCE[s] || s).join("、")),
        el("td", {}, o.wish ? `「${o.wish}」${o.found || 0} 条` : ""),
        el("td", { class: "num" }, o.shown || 0),
        el("td", { class: "num" }, o.seen || 0),
        el("td", { class: "small muted" }, (o.errors || []).join("；"))))) : empty("还没出过门"));

    renderGo(d.last_go, d.going);
  }

  function renderWishes(wishes) {
    clear($("wishes"), wishes.length ? wishes.map((w) => el("span", { class: "chip", title: w.reason || "" }, w.topic,
      el("button", { "aria-label": "不找了", title: "不找了", on: { click: () => removeWish(w.topic) } }, "×")))
      : empty("现在没有想找的"));
  }

  function renderGo(last, going) {
    $("go").disabled = !!going;
    if (!last || !last.state) { clear($("go-result")); return; }
    if (last.state === "going" || going) {
      clear($("go-result"), badge("出门中", "info"), ` ${last.topic ? "去搜「" + last.topic + "」，" : ""}一般要一两分钟…`);
      watchGo();
      return;
    }
    if (last.state === "failed") { clear($("go-result"), badge("没出成", "bad"), " " + last.error); return; }
    const o = last.outing;
    clear($("go-result"), badge("回来了", "good"),
      ` 去了 ${o.sources.map((s) => SOURCE[s] || s).join("、")}${o.wish ? `，搜「${o.wish}」找到 ${o.found} 条` : ""}，刷了 ${o.shown} 条，点开 ${o.seen.length} 条`,
      o.seen.map((s) => el("div", { class: "item", style: "margin-top:6px" }, el("div", {}, `${s.site}｜${s.title}`),
        s.thought ? el("div", { class: "thought" }, s.thought) : null)),
      o.errors.length ? el("div", { class: "small muted" }, "出错：" + o.errors.join("；")) : null);
  }

  function watchGo() {
    if (goWatch) return;
    goWatch = setInterval(async () => {
      const d = await api("GET", "overview").catch(() => null);
      if (!d) return;
      if (!d.going) { clearInterval(goWatch); goWatch = null; render(d); }
      else renderGo(d.last_go, true);
    }, 4000);
  }

  async function load() { render(await api("GET", "overview")); }

  async function removeWish(topic) {
    renderWishes((await api("POST", "wish_remove", { topic })).wishes);
  }

  $("wish-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const topic = $("wish-topic").value.trim();
    if (!topic) return;
    const res = await api("POST", "wish_add", { topic, reason: $("wish-reason").value.trim() }).catch(() => null);
    if (res) { $("wish-topic").value = ""; $("wish-reason").value = ""; renderWishes(res.wishes); }
  });

  $("go-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const res = await api("POST", "go", { topic: $("go-topic").value.trim() }).catch(() => null);
    if (res) { $("go-topic").value = ""; load(); }
  });

  // ------------------------------------------------------------------ Bilibili login
  const QR_TEXT = {
    waiting: "用手机B站扫这个码，在手机上点确认（建议用小号；她只用它读字幕，不发东西）。三分钟内有效。",
    scanned: "扫到了，在手机上点确认",
    expired: "二维码过期了，再点一次“扫码登 B站”",
  };

  function showQr(st, image) {
    $("qr").hidden = false;
    if (image) $("qr-img").src = image;
    $("qr-img").hidden = !(st.state === "waiting" || st.state === "scanned");
    if (st.state === "done") clear($("qr-text"), badge("登录好了", "good"), st.name ? " " + st.name : "", "。以后她点开B站视频会读字幕。");
    else if (st.state === "failed") clear($("qr-text"), badge("没登录上", "bad"), " " + (st.message || ""));
    else clear($("qr-text"), QR_TEXT[st.state] || "");
  }

  $("bili-qr").addEventListener("click", K.busy($("bili-qr"), async () => {
    const st = await api("POST", "bili_qr");
    showQr(st, st.image);
    clearInterval(polling);
    polling = setInterval(async () => {
      const s = await api("GET", "bili_qr_status").catch(() => null);
      if (!s) return;
      showQr(s);
      if (!["waiting", "scanned"].includes(s.state)) { clearInterval(polling); polling = null; load(); }
    }, 2000);
  }));

  $("bili-check").addEventListener("click", K.busy($("bili-check"), async () => {
    const s = await api("POST", "bili_check");
    $("qr").hidden = false;
    $("qr-img").hidden = true;
    const [t, c] = BILI[s.state] || [s.state, ""];
    clear($("qr-text"), "B站说：", badge(t, c), s.name ? " " + s.name : "");
  }));

  // The view runs in a sandbox without dialogs: confirm by clicking a second time.
  let logoutArmed = null;
  $("bili-logout").addEventListener("click", K.busy($("bili-logout"), async () => {
    const b = $("bili-logout");
    if (!logoutArmed) {
      b.textContent = "再点一次：删掉登录";
      b.classList.add("danger");
      logoutArmed = setTimeout(() => { logoutArmed = null; b.textContent = "退出登录"; b.classList.remove("danger"); }, 5000);
      return;
    }
    clearTimeout(logoutArmed);
    logoutArmed = null;
    b.textContent = "退出登录";
    b.classList.remove("danger");
    await api("POST", "bili_logout");
    $("qr").hidden = true;
    load();
  }));

  $("refresh").addEventListener("click", () => load().catch(() => {}));
  K.onReady("上网逛逛", () => load().catch(() => {}));
})();
