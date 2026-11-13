/* 法人籌碼儀錶板 — 讀 data/dashboard.json 後渲染。無外部相依。 */
(() => {
  const $ = (s) => document.querySelector(s);
  const EL = (t, c, txt) => {
    const e = document.createElement(t);
    if (c) e.className = c;
    if (txt != null) e.textContent = txt;
    return e;
  };

  const OKU = 1e8;
  const sign = (v) => (v == null ? "flat" : v > 0 ? "buy" : v < 0 ? "sell" : "flat");
  const signed = (v, dp = 1) =>
    v == null ? "—" : (v >= 0 ? "+" : "−") + Math.abs(v / OKU).toFixed(dp);
  const signedLots = (v) =>
    v == null ? "—" : (v >= 0 ? "+" : "−") + Math.abs(v).toLocaleString("en-US");
  const plain = (v, dp = 1) => (v == null ? "—" : (v / OKU).toFixed(dp));
  const md = (iso) => (iso ? iso.slice(5).replace("-", "/") : "");

  const shortTime = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  };

  let DATA = null, win = 10, cat = "flow", who = "f", rankWin = 1;

  /* ---------------- 提示框 ---------------- */
  const tip = $("#tip");
  function showTip(e, html) {
    tip.innerHTML = html;
    tip.classList.add("on");
    tip.setAttribute("aria-hidden", "false");
    const r = tip.getBoundingClientRect();
    let x = e.clientX + 14, y = e.clientY - r.height - 10;
    if (x + r.width > innerWidth - 8) x = e.clientX - r.width - 14;
    if (y < 8) y = e.clientY + 16;
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }
  function hideTip() {
    tip.classList.remove("on");
    tip.setAttribute("aria-hidden", "true");
  }
  function bindTip(node, html) {
    node.addEventListener("mousemove", (e) => showTip(e, html));
    node.addEventListener("mouseleave", hideTip);
  }

  /* ---------------- 資料來源狀態列 ---------------- */
  function renderFeeds(status) {
    const box = $("#feeds");
    box.textContent = "";
    Object.entries(status || {})
      .filter(([k]) => !k.startsWith("_"))
      .forEach(([, s]) => {
        const el = EL("div", "feed" + (s.ok ? "" : " stale"));
        el.append(EL("b", null, s.label || ""));
        const t = EL("time", null, shortTime(s.updated_at));
        if (!s.ok) {
          t.textContent = "上次成功 " + shortTime(s.updated_at);
          el.title = "最近一次抓取失敗：" + (s.error || "");
        }
        el.append(t);
        box.append(el);
      });
  }

  /* ---------------- 總覽卡片 ---------------- */
  function card(cls, label, dateStr, value, text, unit, sub) {
    const c = EL("div", "s-card cat-" + cls);
    const top = EL("div", "s-top");
    top.append(EL("div", "s-label", label));
    if (dateStr) top.append(EL("div", "s-date", md(dateStr)));
    c.append(top);
    const val = EL("div", "s-val " + sign(value), text);
    if (unit) val.append(EL("small", null, unit));
    c.append(val);
    c.append(EL("div", "s-sub", sub || ""));
    return c;
  }

  function renderSummary() {
    const s = DATA.summary, ser = DATA.series;
    const box = $("#summary");
    box.textContent = "";

    const dates = Object.values(s.dates || {}).filter(Boolean).sort();
    $("#today-date").textContent = dates.length
      ? `最近交易日 ${dates[dates.length - 1]}` : "尚無資料";

    const lastOf = (a) => (a && a.length ? a[a.length - 1].v : null);

    box.append(
      card("flow", "上市外資買賣超", s.dates.twse_foreign, s.twse_foreign,
        signed(s.twse_foreign), "億", "上市現貨"),
      card("flow", "上市投信買賣超", s.dates.twse_trust, s.twse_trust,
        signed(s.twse_trust), "億", "上市現貨"),
      card("margin", "上市融資增減", s.dates.twse_margin, s.twse_margin_change,
        signed(s.twse_margin_change, 2), "億",
        "餘額 " + plain(lastOf(ser.twse_margin)) + " 億"),
      card("margin", "上櫃融資增減", s.dates.tpex_margin, s.tpex_margin_change,
        signed(s.tpex_margin_change, 2), "億",
        "餘額 " + plain(lastOf(ser.tpex_margin)) + " 億"),
      card("oi", "外資期貨多空變化", s.dates.tx_foreign_oi, s.tx_foreign_oi_change,
        signedLots(s.tx_foreign_oi_change), "口",
        "今日淨額 " + (s.tx_foreign_oi == null ? "—" : s.tx_foreign_oi.toLocaleString("en-US")) + " 口")
    );
  }

  /* ---------------- SVG 圖表 ---------------- */
  const NS = "http://www.w3.org/2000/svg";
  const sv = (t, a) => {
    const e = document.createElementNS(NS, t);
    for (const k in a) e.setAttribute(k, a[k]);
    return e;
  };

  // 好看的刻度間距
  function niceTicks(lo, hi, count = 5) {
    if (lo === hi) { lo -= 1; hi += 1; }
    const raw = (hi - lo) / count;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const norm = raw / mag;
    const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
    const start = Math.floor(lo / step) * step;
    const end = Math.ceil(hi / step) * step;
    const out = [];
    for (let v = start; v <= end + step / 2; v += step) out.push(+v.toFixed(10));
    return out;
  }

  const fmtTick = (v) =>
    Math.abs(v) >= 1000 ? v.toLocaleString("en-US") : String(+v.toFixed(2));

  /** 長條圖：零軸置中，紅買綠賣，hover 顯示數值 */
  function barChart(points, unitLabel, toDisplay) {
    const W = 560, H = 230, L = 52, R = 12, T = 14, B = 26;
    const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });
    if (!points.length) return svg;

    const vals = points.map((p) => toDisplay(p.v));
    const ticks = niceTicks(Math.min(0, ...vals), Math.max(0, ...vals));
    const lo = ticks[0], hi = ticks[ticks.length - 1];
    const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);

    ticks.forEach((t) => {
      svg.append(sv("line", {
        x1: L, y1: y(t), x2: W - R, y2: y(t),
        class: t === 0 ? "ax-zero" : "ax-line",
      }));
      const lb = sv("text", { x: L - 7, y: y(t) + 3, class: "ax-text", "text-anchor": "end" });
      lb.textContent = fmtTick(t);
      svg.append(lb);
    });
    const slot = (W - L - R) / points.length;
    const bw = Math.min(26, slot * 0.42);
    points.forEach((p, i) => {
      const cx = L + slot * (i + 0.5);
      const v = toDisplay(p.v);
      const y0 = y(0), y1 = y(v);
      const rect = sv("rect", {
        x: cx - bw / 2, y: Math.min(y0, y1), width: bw,
        height: Math.max(Math.abs(y1 - y0), 1),
        fill: v >= 0 ? "var(--buy)" : "var(--sell)", rx: 1, class: "bar",
      });
      bindTip(rect, `${p.d}<b>${(v >= 0 ? "+" : "−") + Math.abs(v).toFixed(2)} ${unitLabel}</b>`);
      svg.append(rect);

      const lb = sv("text", { x: cx, y: H - B + 15, class: "ax-text", "text-anchor": "middle" });
      lb.textContent = md(p.d);
      svg.append(lb);
    });
    return svg;
  }

  /** 折線圖：hover 圓點顯示數值。wide=true 用於整排寬度的卡片，
   *  viewBox 加寬避免被瀏覽器等比例放大導致文字過大。 */
  function lineChart(points, unitLabel, accent, toDisplay, wide) {
    const W = wide ? 1040 : 560, H = wide ? 200 : 230;
    const L = 58, R = 16, T = 16, B = 26;
    const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });
    if (points.length < 2) return svg;

    const vals = points.map((p) => toDisplay(p.v));
    const ticks = niceTicks(Math.min(...vals), Math.max(...vals));
    const lo = ticks[0], hi = ticks[ticks.length - 1];
    const x = (i) => L + (i * (W - L - R)) / (points.length - 1);
    const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);

    ticks.forEach((t) => {
      svg.append(sv("line", { x1: L, y1: y(t), x2: W - R, y2: y(t), class: "ax-line" }));
      const lb = sv("text", { x: L - 7, y: y(t) + 3, class: "ax-text", "text-anchor": "end" });
      lb.textContent = fmtTick(t);
      svg.append(lb);
    });

    const d = vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
    svg.append(sv("path", { d, fill: "none", stroke: accent, "stroke-width": 1.9 }));

    points.forEach((p, i) => {
      const v = vals[i];
      const c = sv("circle", {
        cx: x(i), cy: y(v), r: 3.6, fill: "#fff",
        stroke: accent, "stroke-width": 1.8, class: "dot",
      });
      bindTip(c, `${p.d}<b>${fmtTick(v)} ${unitLabel}</b>`);
      svg.append(c);
      const lb = sv("text", { x: x(i), y: H - B + 15, class: "ax-text", "text-anchor": "middle" });
      lb.textContent = md(p.d);
      svg.append(lb);
    });
    return svg;
  }

  function chartCard(clsCat, name, marketTag, total, node, full) {
    const c = EL("div", "chart cat-" + clsCat + (full ? " full" : ""));
    const head = EL("div", "chart-head");
    const nm = EL("div", "chart-name");
    if (marketTag) nm.append(EL("em", null, marketTag + "｜"));
    nm.append(document.createTextNode(name));
    head.append(nm, EL("div", "chart-total " + (total.cls || ""), total.text));
    c.append(head, node);
    return c;
  }

  const CATS = {
    flow: { eyebrow: "INSTITUTIONAL FLOW", title: (n) => `近 ${n} 日上市櫃法人買賣超` },
    margin: { eyebrow: "MARGIN BALANCE", title: (n) => `近 ${n} 日上市櫃融資餘額` },
    oi: { eyebrow: "FUTURES OPEN INTEREST", title: (n) => `近 ${n} 日外資期貨未平倉淨額` },
  };

  function renderCharts() {
    const box = $("#charts");
    box.textContent = "";
    hideTip();
    const S = DATA.series, W = DATA.windows[String(win)] || {};
    const cut = (a) => (a || []).slice(-win);
    const toOku = (v) => v / OKU;
    const asIs = (v) => v;

    $("#chart-eyebrow").textContent = CATS[cat].eyebrow;
    $("#chart-title").textContent = CATS[cat].title(win);

    if (cat === "flow") {
      [["上市", "外資買賣超", "twse_foreign"],
       ["上市", "投信買賣超", "twse_trust"],
       ["上櫃", "外資買賣超", "tpex_foreign"],
       ["上櫃", "投信買賣超", "tpex_trust"]].forEach(([mkt, name, key]) => {
        const pts = cut(S[key]), sum = W[key];
        box.append(chartCard("flow", name, mkt,
          { text: (sum == null ? "—" : signed(sum, 2) + " 億元"), cls: sign(sum) },
          barChart(pts, "億元", toOku)));
      });
    } else if (cat === "margin") {
      [["上市融資餘額", "twse_margin"], ["上櫃融資餘額", "tpex_margin"]]
        .forEach(([name, key]) => {
          const pts = cut(S[key]);
          const last = pts.length ? pts[pts.length - 1].v : null;
          box.append(chartCard("margin", name, null,
            { text: plain(last, 2) + " 億元" },
            lineChart(pts, "億元", "var(--c-margin)", toOku, true), true));
        });
    } else {
      const pts = cut(S.tx_foreign_oi);
      const last = pts.length ? pts[pts.length - 1].v : null;
      box.append(chartCard("oi", "外資未平倉淨額", null,
        { text: (last == null ? "—" : last.toLocaleString("en-US")) + " 口",
          cls: sign(last) },
        lineChart(pts, "口", "var(--c-oi)", asIs, true), true));
    }
  }

  /* ---------------- 明細表 ---------------- */
  function renderTable() {
    const tb = $("#detail tbody");
    tb.textContent = "";
    (DATA.table || []).forEach((r) => {
      const tr = EL("tr");
      tr.append(EL("td", null, r.d));
      const cell = (v, sep) => tr.append(EL("td", (sep ? "sep " : "") + sign(v), signed(v)));
      cell(r.tw_f, true); cell(r.tw_t); cell(r.tw_d);
      cell(r.tp_f, true); cell(r.tp_t); cell(r.tp_d);
      tr.append(EL("td", "sep", plain(r.tw_m)));
      tr.append(EL("td", sign(r.tw_mc), signed(r.tw_mc, 2)));
      tr.append(EL("td", null, plain(r.tp_m)));
      tr.append(EL("td", sign(r.tp_mc), signed(r.tp_mc, 2)));
      tr.append(EL("td", "sep " + sign(r.oi),
        r.oi == null ? "—" : r.oi.toLocaleString("en-US")));
      tb.append(tr);
    });
  }

  /* ---------------- 排行 ---------------- */
  function renderRanks() {
    const block = (DATA.rankings || {})[String(rankWin)];
    const note = $("#rank-note");
    const buy = $("#rank-buy"), sell = $("#rank-sell");
    buy.textContent = ""; sell.textContent = "";

    if (!block || !block[who]) {
      note.textContent = "尚無排行資料。";
      return;
    }
    note.textContent = block.complete
      ? `${block.from} ～ ${block.to}　共 ${block.days_used} 個交易日`
      : `資料僅累積 ${block.days_used} 個交易日（不足 ${rankWin} 日），${block.from} ～ ${block.to}`;

    const fill = (tbody, rows, cls) => {
      if (!rows.length) {
        const tr = EL("tr");
        const td = EL("td", "rank-empty", "無符合條件的個股");
        td.colSpan = 3;
        tr.append(td);
        tbody.append(tr);
        return;
      }
      rows.forEach((r, i) => {
        const tr = EL("tr");
        tr.append(EL("td", "rk", String(i + 1)));
        const nm = EL("td", "nm");
        nm.append(EL("code", null, r.c));
        nm.append(EL("span", "nm-text", r.n));
        nm.append(EL("span", "mk " + (r.m === "TWSE" ? "mk-tw" : "mk-tp"),
          r.m === "TWSE" ? "市" : "櫃"));
        tr.append(nm, EL("td", "amt " + cls, signed(r[who], 2)));
        tbody.append(tr);
      });
    };
    fill(buy, block[who].buy || [], "buy");
    fill(sell, block[who].sell || [], "sell");
  }

  /* ---------------- 切換 ---------------- */
  function wire(sel, attr, fn) {
    $(sel).addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      [...e.currentTarget.children].forEach((x) => x.classList.remove("on"));
      b.classList.add("on");
      fn(b.dataset[attr]);
    });
  }

  /* ---------------- 啟動 ---------------- */
  fetch("data/dashboard.json?t=" + Date.now())
    .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then((json) => {
      DATA = json;
      renderFeeds(json.status);
      renderSummary();
      renderCharts();
      renderTable();
      renderRanks();
      $("#built").textContent = "頁面資料產出時間 " + shortTime(json.built_at);

      $("#win-select").addEventListener("change", (e) => {
        win = +e.target.value;
        renderCharts();
      });
      wire("#cat-switch", "cat", (v) => { cat = v; renderCharts(); });
      wire("#who-switch", "who", (v) => { who = v; renderRanks(); });
      wire("#rank-switch", "w", (v) => { rankWin = +v; renderRanks(); });
      addEventListener("scroll", hideTip, { passive: true });
    })
    .catch(() => {
      $("#summary").innerHTML =
        '<p class="rank-empty">還沒有資料檔。到 GitHub 的 Actions 頁面手動執行一次「更新籌碼資料」，' +
        "回補天數填 40，跑完後重新整理這一頁。</p>";
    });
})();
