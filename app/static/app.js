/* Zentric ops dashboard.
 *
 * Read-only: every call here is a GET except the ROI simulate POST, which is pure
 * computation server-side. Nothing in this file can change a parcel, send a message,
 * or write an audit row — business changes only ever originate from the agent graph
 * or the proactive loop.
 *
 * Live updates are polled, not pushed. Messages arrive through the mock WhatsApp
 * channel and the proactive scanner, which run as separate processes (app.tools.sim,
 * simulate_outcomes, the notifier) — an in-process event bus would never see them, so
 * Postgres is the only shared truth and we poll it. Threads use an id cursor so a
 * live conversation costs one small query per tick.
 *
 * All customer-supplied text (message bodies, case details, phone numbers) is written
 * with textContent. It is untrusted input that reached us over a webhook.
 */
(() => {
  "use strict";

  // Three tiers, by how fast the underlying data actually changes and how expensive it
  // is to ask. Putting everything on one 4s tick meant re-running a GROUP BY over the
  // whole messages table and a three-table case merge every four seconds, forever.
  const POLL_CONVO_MS = 4000;    // conversation list + open thread — the live surface
  const POLL_CASES_MS = 10000;   // cases + handoffs — change only when something escalates
  const POLL_PERF_MS = 20000;    // KPI tiles + trend (heaviest queries)
  const ROI_DEBOUNCE_MS = 150;
  const TZ = "Asia/Karachi";     // matches BUSINESS_HOURS_TIMEZONE server-side
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  const state = {
    token: sessionStorage.getItem("zentric_token") || "",
    // Separate credential for the write endpoints (Phase 5). Absent by default: with
    // no write token the dashboard behaves exactly as the read-only Phase 4 one did.
    writeToken: sessionStorage.getItem("zentric_write_token") || "",
    days: 14,
    caseType: "",
    handoffStatus: "",
    selectedPhone: null,
    threadCursor: null,
    convoSignature: null,   // cheap change-detect so the poll doesn't rebuild an unchanged list
    series: [],
    roiDefaults: null,
    roiInputs: {},
    timers: {},        // one pending timeout per poll tier, keyed by name
    polling: false,
  };

  const $ = (id) => document.getElementById(id);

  // --- api ---------------------------------------------------------------

  class ApiError extends Error {
    constructor(status, detail) {
      super(detail || `HTTP ${status}`);
      this.status = status;
    }
  }

  async function api(path, options = {}) {
    // `write: true` sends the write credential instead of the read one. The server
    // refuses the read token on write endpoints, so this is not merely cosmetic.
    const { write = false, ...rest } = options;
    const response = await fetch(path, {
      ...rest,
      headers: {
        Authorization: `Bearer ${write ? state.writeToken : state.token}`,
        ...(rest.body ? { "Content-Type": "application/json" } : {}),
        ...(rest.headers || {}),
      },
    });
    if (!response.ok) {
      let detail = "";
      try {
        detail = (await response.json()).detail;
      } catch { /* non-JSON error body */ }
      if (typeof detail !== "string") detail = "";
      throw new ApiError(response.status, detail);
    }
    return response.json();
  }

  // --- formatting --------------------------------------------------------

  const numberFmt = new Intl.NumberFormat("en-US");
  const num = (v) => numberFmt.format(Math.round(v || 0));

  function pkr(value, { compact = true } = {}) {
    const v = value || 0;
    const sign = v < 0 ? "-" : "";
    const abs = Math.abs(v);
    if (!compact) return `${sign}PKR ${num(abs)}`;
    if (abs >= 1e6) return `${sign}PKR ${(abs / 1e6).toFixed(1)}M`;
    if (abs >= 1e4) return `${sign}PKR ${(abs / 1e3).toFixed(0)}K`;
    return `${sign}PKR ${num(abs)}`;
  }

  const pct = (v) => `${(v ?? 0).toFixed(1)}%`;
  const seconds = (ms) => `${((ms || 0) / 1000).toFixed(1)}s`;

  function dayLabel(isoDate) {
    // Bucket dates are already local business dates — format them literally rather
    // than round-tripping through Date, which would shift them by the viewer's zone.
    const [, month, day] = isoDate.split("-");
    return `${Number(day)} ${MONTHS[Number(month) - 1]}`;
  }

  const timeFmt = new Intl.DateTimeFormat("en-GB", {
    timeZone: TZ, day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false,
  });

  const stamp = (iso) => (iso ? timeFmt.format(new Date(iso)) : "");

  const titleCase = (text) =>
    String(text || "").replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

  // --- dom helpers -------------------------------------------------------

  function el(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;      // untrusted-safe
      else if (key === "style") node.setAttribute("style", value);
      else if (value !== null && value !== undefined) node.setAttribute(key, value);
    }
    for (const child of [].concat(children)) if (child) node.appendChild(child);
    return node;
  }

  const SVG_NS = "http://www.w3.org/2000/svg";

  function svg(tag, attrs = {}, parent = null) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "text") node.textContent = value;
      else if (value !== null && value !== undefined) node.setAttribute(key, value);
    }
    if (parent) parent.appendChild(node);
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  /** Round a max up to a clean axis number (1/2/2.5/5/10 x 10^k). */
  function niceMax(value) {
    if (!value || value <= 0) return 1;
    const magnitude = 10 ** Math.floor(Math.log10(value));
    const scaled = value / magnitude;
    const step = [1, 2, 2.5, 5, 10].find((s) => scaled <= s) ?? 10;
    return step * magnitude;
  }

  /** Path for a bar with a 4px rounded data-end and a square baseline. */
  function barPath(x, y, w, h, r) {
    const radius = Math.max(0, Math.min(r, h, w / 2));
    if (h <= 0) return "";
    return `M${x},${y + h}V${y + radius}a${radius},${radius} 0 0 1 ${radius},${-radius}` +
           `h${w - 2 * radius}a${radius},${radius} 0 0 1 ${radius},${radius}V${y + h}Z`;
  }

  // --- tooltip -----------------------------------------------------------

  function makeTooltip(plot) {
    const tip = el("div", { class: "tooltip" });
    plot.appendChild(tip);
    return {
      node: tip,
      show(x, y, dateText, rows) {
        clear(tip);
        tip.appendChild(el("div", { class: "tt-date", text: dateText }));
        for (const row of rows) {
          tip.appendChild(el("div", { class: "tt-row" }, [
            el("span", { class: "tt-key", style: `background:${row.color}` }),
            el("span", { class: "tt-name", text: row.name }),
            el("span", { class: "tt-value", text: row.value }),
          ]));
        }
        tip.dataset.open = "true";
        const bounds = plot.getBoundingClientRect();
        const width = tip.offsetWidth;
        tip.style.left = `${Math.max(4, Math.min(x - width / 2, bounds.width - width - 4))}px`;
        tip.style.top = `${Math.max(4, y - tip.offsetHeight - 12)}px`;
      },
      hide() { tip.dataset.open = "false"; },
    };
  }

  const cssVar = (name) =>
    getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  // --- charts ------------------------------------------------------------

  const PLOT_H = 190;
  const AXIS_BAND = 24;          // the container includes the x-axis band
  const MARGIN = { top: 14, right: 14, bottom: AXIS_BAND, left: 56 };

  function chartFrame(plot, rows, emptyText) {
    clear(plot);
    if (!rows.length || rows.every((r) => r.empty)) {
      plot.appendChild(el("div", { class: "plot-empty", text: emptyText }));
      return null;
    }
    const width = Math.max(plot.clientWidth || 520, 320);
    const height = PLOT_H + MARGIN.top + MARGIN.bottom;
    const root = svg("svg", {
      width, height, viewBox: `0 0 ${width} ${height}`, role: "img",
    }, plot);
    return {
      root,
      width,
      height,
      plotW: width - MARGIN.left - MARGIN.right,
      plotH: PLOT_H,
      x0: MARGIN.left,
      y0: MARGIN.top,
    };
  }

  function drawAxes(frame, ticks, formatTick) {
    const { root, x0, y0, plotW, plotH } = frame;
    const grid = cssVar("--grid");
    const axis = cssVar("--axis");
    const muted = cssVar("--muted");

    for (const tick of ticks) {
      const y = y0 + plotH - tick.ratio * plotH;
      svg("line", {
        x1: x0, y1: y, x2: x0 + plotW, y2: y,
        stroke: grid, "stroke-width": 1, "shape-rendering": "crispEdges",
      }, root);
      svg("text", {
        x: x0 - 8, y: y + 4, "text-anchor": "end", "font-size": 11, fill: muted,
        "font-variant-numeric": "tabular-nums", text: formatTick(tick.value),
      }, root);
    }
    svg("line", {
      x1: x0, y1: y0 + plotH, x2: x0 + plotW, y2: y0 + plotH,
      stroke: axis, "stroke-width": 1, "shape-rendering": "crispEdges",
    }, root);
  }

  function axisTicks(max, count = 4) {
    return Array.from({ length: count + 1 }, (_, i) => ({
      value: (max / count) * i,
      ratio: i / count,
    }));
  }

  /** X labels, thinned so they never collide. */
  function drawXLabels(frame, rows) {
    const { root, x0, y0, plotW, plotH } = frame;
    const muted = cssVar("--muted");
    const band = plotW / rows.length;
    const slots = Math.max(1, Math.floor(plotW / 52));
    const stride = Math.max(1, Math.ceil(rows.length / slots));
    rows.forEach((row, i) => {
      if (i % stride !== 0 && i !== rows.length - 1) return;
      svg("text", {
        x: x0 + band * i + band / 2, y: y0 + plotH + 16,
        "text-anchor": "middle", "font-size": 11, fill: muted, text: dayLabel(row.date),
      }, root);
    });
  }

  /** Stacked columns: support saving + RTO saving per day. */
  function renderSavingsChart(plot, series) {
    const rows = series.map((d) => ({
      date: d.date,
      support: d.support_saving_pkr,
      rto: d.rto_saving_pkr,
      total: d.total_saving_pkr,
      empty: !d.total_saving_pkr,
    }));
    const frame = chartFrame(plot, rows, "No savings recorded in this range.");
    if (!frame) return;

    const { root, x0, y0, plotW, plotH } = frame;
    const max = niceMax(Math.max(...rows.map((r) => r.total)));
    drawAxes(frame, axisTicks(max), (v) => (v >= 1000 ? `${Math.round(v / 1000)}K` : num(v)));
    drawXLabels(frame, rows);

    const band = plotW / rows.length;
    const barW = Math.min(24, Math.max(6, band * 0.6));
    const colors = { support: cssVar("--series-1"), rto: cssVar("--series-2") };
    const tip = makeTooltip(plot);
    const scale = (v) => (v / max) * plotH;

    rows.forEach((row, i) => {
      const cx = x0 + band * i + band / 2;
      const x = cx - barW / 2;
      const supportH = scale(row.support);
      const rtoH = scale(row.rto);
      const baseY = y0 + plotH;

      // The 2px separator is a gap in the surface colour, not a stroke: it comes out
      // of the upper segment so the stack still sums to the true total height.
      const gap = supportH > 0 && rtoH > 0 ? 2 : 0;
      if (supportH > 0) {
        const rounded = rtoH > 0 ? 0 : 4;
        svg("path", {
          d: barPath(x, baseY - supportH, barW, supportH, rounded), fill: colors.support,
        }, root);
      }
      if (rtoH > 0) {
        const top = baseY - supportH - rtoH;
        svg("path", {
          d: barPath(x, top, barW, Math.max(0, rtoH - gap), 4), fill: colors.rto,
        }, root);
      }

      // Label the most recent column only — selective, never a number on every mark.
      if (i === rows.length - 1 && row.total > 0) {
        svg("text", {
          x: cx, y: baseY - supportH - rtoH - 7, "text-anchor": "middle",
          "font-size": 11, "font-weight": 600, fill: cssVar("--text-secondary"),
          text: pkr(row.total),
        }, root);
      }

      // Hit target spans the whole band, so the pointer never has to find a thin bar.
      const hit = svg("rect", {
        x: x0 + band * i, y: y0, width: band, height: plotH,
        fill: "transparent", tabindex: 0, role: "button",
      }, root);
      hit.setAttribute("aria-label",
        `${dayLabel(row.date)}: support ${pkr(row.support)}, RTO ${pkr(row.rto)}`);

      const show = () => {
        tip.show(cx, y0 + plotH - supportH - rtoH, dayLabel(row.date), [
          { color: colors.support, name: "Support deflection", value: pkr(row.support, { compact: false }) },
          { color: colors.rto, name: "RTO prevented", value: pkr(row.rto, { compact: false }) },
          { color: cssVar("--axis"), name: "Total", value: pkr(row.total, { compact: false }) },
        ]);
      };
      hit.addEventListener("pointerenter", show);
      hit.addEventListener("focus", show);
      hit.addEventListener("pointerleave", tip.hide);
      hit.addEventListener("blur", tip.hide);
    });
  }

  /** Single-series line: daily deflection rate. No legend — the title names it. */
  function renderDeflectionChart(plot, series) {
    const rows = series.map((d) => ({
      date: d.date,
      value: d.deflection_rate_pct,
      interactions: d.interactions,
      empty: !d.interactions,
    }));
    const frame = chartFrame(plot, rows, "No interactions in this range.");
    if (!frame) return;

    const { root, x0, y0, plotW, plotH } = frame;
    drawAxes(frame, axisTicks(100), (v) => `${Math.round(v)}%`);
    drawXLabels(frame, rows);

    const color = cssVar("--series-1");
    const surface = cssVar("--surface-1");
    const band = plotW / rows.length;
    const px = (i) => x0 + band * i + band / 2;
    const py = (v) => y0 + plotH - (v / 100) * plotH;
    const tip = makeTooltip(plot);

    // Only days with interactions are real data points; a 0% day with no traffic
    // would draw a false dip to the floor, so the line breaks instead.
    const points = rows.map((r, i) => ({ ...r, i })).filter((r) => r.interactions > 0);
    if (points.length > 1) {
      svg("path", {
        d: points.map((p, n) => `${n ? "L" : "M"}${px(p.i)},${py(p.value)}`).join(""),
        fill: "none", stroke: color, "stroke-width": 2,
        "stroke-linecap": "round", "stroke-linejoin": "round",
      }, root);
    }

    const last = points[points.length - 1];
    for (const point of points) {
      const isLast = point === last;
      svg("circle", {
        cx: px(point.i), cy: py(point.value), r: isLast ? 4.5 : 3,
        fill: color, stroke: surface, "stroke-width": 2,
      }, root);
    }
    if (last) {
      svg("text", {
        x: Math.min(px(last.i) + 10, x0 + plotW), y: py(last.value) - 9,
        "text-anchor": last.i === rows.length - 1 ? "end" : "start",
        "font-size": 11, "font-weight": 600, fill: cssVar("--text-secondary"),
        text: pct(last.value),
      }, root);
    }

    // The crosshair finds the X — readers aim at a date, not at a 2px line.
    const crosshair = svg("line", {
      y1: y0, y2: y0 + plotH, stroke: cssVar("--axis"), "stroke-width": 1,
      "shape-rendering": "crispEdges", opacity: 0,
    }, root);

    rows.forEach((row, i) => {
      const hit = svg("rect", {
        x: x0 + band * i, y: y0, width: band, height: plotH,
        fill: "transparent", tabindex: 0, role: "button",
      }, root);
      hit.setAttribute("aria-label", row.interactions
        ? `${dayLabel(row.date)}: ${pct(row.value)} of ${row.interactions} interactions`
        : `${dayLabel(row.date)}: no interactions`);

      const show = () => {
        crosshair.setAttribute("x1", px(i));
        crosshair.setAttribute("x2", px(i));
        crosshair.setAttribute("opacity", 1);
        tip.show(px(i), row.interactions ? py(row.value) : y0 + plotH / 2, dayLabel(row.date), [
          { color, name: "Deflection rate", value: row.interactions ? pct(row.value) : "no data" },
          { color: cssVar("--axis"), name: "Interactions", value: num(row.interactions) },
        ]);
      };
      const hide = () => { crosshair.setAttribute("opacity", 0); tip.hide(); };
      hit.addEventListener("pointerenter", show);
      hit.addEventListener("focus", show);
      hit.addEventListener("pointerleave", hide);
      hit.addEventListener("blur", hide);
    });
  }

  /** Every chart has a table-view twin — the WCAG-clean equivalent. */
  function renderTable(container, columns, rows) {
    clear(container);
    const table = el("table");
    const head = el("tr");
    for (const col of columns) {
      head.appendChild(el("th", { class: col.num ? "num" : "", text: col.label }));
    }
    table.appendChild(el("thead", {}, [head]));
    const body = el("tbody");
    for (const row of rows) {
      const tr = el("tr");
      for (const col of columns) {
        tr.appendChild(el("td", { class: col.num ? "num" : "", text: col.get(row) }));
      }
      body.appendChild(tr);
    }
    table.appendChild(body);
    container.appendChild(table);
  }

  // --- performance panel -------------------------------------------------

  function renderKpis(report) {
    const grid = $("kpi-grid");
    clear(grid);
    const rto = report.rto_reduction || {};
    const cost = report.cost_per_interaction || {};
    const resolved = (rto.delivered_count || 0) + (rto.still_failed_count || 0);

    const tiles = [
      {
        label: "Deflection rate", value: pct(report.deflection_rate_pct),
        note: `${num(report.total_interactions)} interactions`,
      },
      {
        label: "RTO prevented", value: pct(rto.rto_reduction_pct),
        note: `${num(rto.delivered_count)} of ${num(resolved)} resolved interventions`,
      },
      {
        label: "Saved via prevented RTO", value: pkr(rto.pkr_saved),
        note: "Interventions that reached delivery",
      },
      {
        label: "Cost per interaction", value: pkr(cost.blended_pkr, { compact: false }),
        note: `vs ${pkr(cost.human_only_pkr, { compact: false })} human`,
      },
      {
        label: "Median response", value: seconds((report.response_time_ms || {}).median_ms),
        note: `avg ${seconds((report.response_time_ms || {}).avg_ms)}`,
      },
      {
        label: "After hours", value: pct(report.after_hours_pct),
        note: `${pct(report.language_reach_pct)} Roman Urdu`,
      },
    ];

    for (const tile of tiles) {
      grid.appendChild(el("div", { class: "card" }, [
        el("div", { class: "tile-label", text: tile.label }),
        el("div", { class: "tile-value", text: tile.value }),
        el("div", { class: "tile-note", text: tile.note }),
      ]));
    }
  }

  /** Re-draw both plots from the series already in state — used on resize and on a
   *  theme change, since the marks read their colours from CSS variables at draw time. */
  function redrawCharts() {
    if (!state.series.length) return;
    renderSavingsChart($("chart-savings"), state.series);
    renderDeflectionChart($("chart-deflection"), state.series);
  }

  function renderTrend(payload) {
    state.series = payload.series || [];
    redrawCharts();

    renderTable($("table-savings"), [
      { label: "Day", get: (r) => dayLabel(r.date) },
      { label: "Support", num: true, get: (r) => pkr(r.support_saving_pkr, { compact: false }) },
      { label: "RTO", num: true, get: (r) => pkr(r.rto_saving_pkr, { compact: false }) },
      { label: "Total", num: true, get: (r) => pkr(r.total_saving_pkr, { compact: false }) },
    ], state.series);

    renderTable($("table-deflection"), [
      { label: "Day", get: (r) => dayLabel(r.date) },
      { label: "Interactions", num: true, get: (r) => num(r.interactions) },
      { label: "Deflected", num: true, get: (r) => num(r.deflected) },
      { label: "Rate", num: true, get: (r) => (r.interactions ? pct(r.deflection_rate_pct) : "—") },
    ], state.series);

    $("range-note").textContent =
      `${payload.start_date} to ${payload.end_date} · ${payload.timezone}`;
  }

  async function refreshPerformance() {
    // The trend defines the window, then the tiles are asked for that exact instant —
    // so the cards and the charts can never describe different ranges.
    const trend = await api(`/metrics/timeseries?days=${state.days}`);
    renderTrend(trend);
    const report = await api(`/metrics/report?since=${encodeURIComponent(trend.start_at)}`);
    renderKpis(report);
  }

  // --- operations panel --------------------------------------------------

  function renderConversations(conversations) {
    const list = $("convo-list");

    // The list is a 420px scroll box refreshed every 4s. Tearing it down and
    // rebuilding it unconditionally reset the reader's scroll position (and dropped
    // keyboard focus) on every tick — unusable once there are more threads than fit.
    // Nothing but the rows themselves changes here, so skip the rebuild when the data
    // is identical, and preserve scroll across the rebuilds that do happen.
    const signature = conversations.map((c) => `${c.customer_phone}:${c.last_message_id}`).join("|");
    if (signature === state.convoSignature) return;
    state.convoSignature = signature;
    const scrollTop = list.scrollTop;

    clear(list);
    if (!conversations.length) {
      list.appendChild(el("div", { class: "empty", text: "No conversations yet." }));
      return;
    }
    for (const convo of conversations) {
      const selected = convo.customer_phone === state.selectedPhone;
      const item = el("button", {
        class: "convo-item", type: "button", "aria-selected": String(selected),
        "data-phone": convo.customer_phone,
      }, [
        el("div", { class: "convo-phone", text: convo.customer_phone }),
        el("div", {
          class: "convo-preview",
          text: `${convo.last_direction === "out" ? "→ " : "← "}${convo.last_body || ""}`,
        }),
      ]);
      item.addEventListener("click", () => guarded(() => selectConversation(convo.customer_phone)));
      list.appendChild(item);
    }
    list.scrollTop = scrollTop;
    $("convo-sub").textContent = `${conversations.length} threads · newest activity first`;
  }

  function appendMessages(messages) {
    const thread = $("thread");
    if (!messages.length) return;
    thread.querySelectorAll(".empty").forEach((node) => node.remove());
    for (const message of messages) {
      thread.appendChild(el("div", { class: `bubble ${message.direction === "in" ? "in" : "out"}` }, [
        el("span", { text: message.body }),
        el("span", {
          class: "meta",
          text: message.tracking_number
            ? `${stamp(message.created_at)} · ${message.tracking_number}`
            : stamp(message.created_at),
        }),
      ]));
    }
    if (messages.length) thread.scrollTop = thread.scrollHeight;
  }

  async function selectConversation(phone) {
    state.selectedPhone = phone;
    state.threadCursor = null;
    const thread = $("thread");
    clear(thread);
    const payload = await api(`/ops/conversations/${encodeURIComponent(phone)}?limit=200`);
    state.threadCursor = payload.latest_id;
    if (!payload.messages.length) {
      thread.appendChild(el("div", { class: "empty", text: "No messages." }));
    } else {
      appendMessages(payload.messages);
    }
    document.querySelectorAll(".convo-item").forEach((node) => {
      node.setAttribute("aria-selected", String(node.dataset.phone === phone));
    });
  }

  async function pollThread() {
    if (!state.selectedPhone) return;
    const cursor = state.threadCursor;
    const query = cursor === null || cursor === undefined ? "" : `?since_id=${cursor}`;
    const payload = await api(`/ops/conversations/${encodeURIComponent(state.selectedPhone)}${query}`);
    if (payload.messages.length) {
      state.threadCursor = payload.latest_id;
      appendMessages(payload.messages);
    }
  }

  const STATUS_TONES = {
    delivered: { tone: "good", icon: "✓" },
    still_failed: { tone: "critical", icon: "✕" },
    open: { tone: "warning", icon: "!" },
    requested: { tone: "warning", icon: "!" },
  };

  function statusCell(status) {
    const { tone, icon } = STATUS_TONES[status] || { tone: "serious", icon: "•" };
    // Icon + label, never colour alone.
    return el("span", { class: "status", "data-tone": tone }, [
      el("span", { class: "status-icon", text: icon, "aria-hidden": "true" }),
      el("span", { text: titleCase(status) }),
    ]);
  }

  function renderCases(cases) {
    const container = $("cases");
    clear(container);
    if (!cases.length) {
      container.appendChild(el("div", { class: "empty", text: "No cases recorded yet." }));
      return;
    }
    const table = el("table");
    const head = el("tr");
    for (const label of ["Type", "Reference", "Parcel", "Action", "Status", "Detail", "When"]) {
      head.appendChild(el("th", { text: label }));
    }
    table.appendChild(el("thead", {}, [head]));

    const body = el("tbody");
    for (const item of cases) {
      const row = el("tr");
      row.appendChild(el("td", {}, [el("span", { class: "pill", text: titleCase(item.type) })]));
      row.appendChild(el("td", { class: "mono", text: item.ref_id }));
      row.appendChild(el("td", { class: "mono", text: item.tracking_number || "—" }));
      row.appendChild(el("td", { text: titleCase(item.action) }));
      row.appendChild(el("td", {}, [statusCell(item.status)]));
      row.appendChild(el("td", { text: item.detail || "—" }));
      row.appendChild(el("td", { class: "mono", text: stamp(item.created_at) }));
      body.appendChild(row);
    }
    table.appendChild(body);
    container.appendChild(table);
  }

  // --- handoff queue (the only writing surface) --------------------------

  const HANDOFF_TONES = {
    open: { tone: "critical", icon: "!", label: "Waiting" },
    claimed: { tone: "warning", icon: "◐", label: "In progress" },
    resolved: { tone: "good", icon: "✓", label: "Resolved" },
    expired: { tone: "serious", icon: "×", label: "Lapsed" },
  };

  function handoffAction(handoff, action, label) {
    const button = el("button", { class: "btn small", type: "button" , text: label });
    button.addEventListener("click", async () => {
      const actor = $("actor-input").value.trim();
      const error = $("handoff-error");
      if (!actor) {
        error.textContent = "Enter who you are before taking or resolving a handoff.";
        $("actor-input").focus();
        return;
      }
      if (!state.writeToken) {
        error.textContent = "No write token — sign in again with DASHBOARD_WRITE_TOKEN to act on handoffs.";
        return;
      }
      button.disabled = true;
      try {
        await api(`/ops/handoffs/${handoff.id}/${action}`, {
          method: "POST", write: true, body: JSON.stringify({ actor }),
        });
        error.textContent = "";
        await refreshHandoffs();
      } catch (err) {
        // 409 means someone else already moved it — say so plainly rather than
        // leaving a dead button.
        error.textContent = err.status === 409
          ? `${err.message} Refreshing.`
          : err.status === 401
            ? "That write token was rejected."
            : "Could not update the handoff — retrying shortly.";
        if (err.status === 409) await refreshHandoffs();
      } finally {
        button.disabled = false;
      }
    });
    return button;
  }

  function renderHandoffs(rows) {
    const container = $("handoffs");
    clear(container);
    if (!rows.length) {
      container.appendChild(el("div", { class: "empty", text: "No handoffs — the bot is handling everything." }));
      return;
    }

    const table = el("table");
    const head = el("tr");
    for (const label of ["Status", "Customer", "Reason", "Parcel", "Owner", "Raised", "Action"]) {
      head.appendChild(el("th", { text: label }));
    }
    table.appendChild(el("thead", {}, [head]));

    const body = el("tbody");
    for (const handoff of rows) {
      const { tone, icon, label } = HANDOFF_TONES[handoff.status]
        || { tone: "serious", icon: "•", label: titleCase(handoff.status) };
      const row = el("tr");

      row.appendChild(el("td", {}, [
        el("span", { class: "status", "data-tone": tone }, [
          el("span", { class: "status-icon", text: icon, "aria-hidden": "true" }),
          el("span", { text: label }),
        ]),
      ]));
      row.appendChild(el("td", { class: "mono", text: handoff.customer_phone }));
      row.appendChild(el("td", { text: titleCase(handoff.reason) }));
      row.appendChild(el("td", { class: "mono", text: handoff.tracking_number || "—" }));
      row.appendChild(el("td", {
        text: handoff.resolved_by || handoff.claimed_by || "—",
      }));
      row.appendChild(el("td", { class: "mono", text: stamp(handoff.created_at) }));

      const actions = el("td");
      if (handoff.status === "open") {
        actions.appendChild(handoffAction(handoff, "claim", "Take"));
        actions.appendChild(handoffAction(handoff, "resolve", "Resolve"));
      } else if (handoff.status === "claimed") {
        actions.appendChild(handoffAction(handoff, "resolve", "Resolve"));
      } else {
        actions.appendChild(el("span", { class: "muted", text: "—" }));
      }
      // An un-notified handoff sitting in the queue is exactly what an ops lead needs
      // to notice, so surface the failure rather than hiding it.
      if (handoff.notify_failed) {
        actions.appendChild(el("div", { class: "warn-note", text: "staff alert failed" }));
      }
      row.appendChild(actions);

      body.appendChild(row);
    }
    table.appendChild(body);
    container.appendChild(table);
  }

  async function refreshHandoffs() {
    const query = state.handoffStatus ? `?status=${state.handoffStatus}&limit=50` : "?limit=50";
    const payload = await api(`/ops/handoffs${query}`);
    renderHandoffs(payload.handoffs);
  }

  /** Fast tier: the live conversation surface. */
  async function refreshConversations() {
    const conversations = await api("/ops/conversations?limit=50");
    renderConversations(conversations.conversations);
    await pollThread();
  }

  /** Medium tier: only changes when something escalates or a corrective action lands. */
  async function refreshCaseFeed() {
    const typeQuery = state.caseType ? `?type=${state.caseType}&limit=50` : "?limit=50";
    const cases = await api(`/ops/cases${typeQuery}`);
    renderCases(cases.cases);
    await refreshHandoffs();
  }

  /** Everything — initial load and returning to the tab, where staleness is likeliest. */
  async function refreshOps() {
    await refreshConversations();
    await refreshCaseFeed();
  }

  // --- ROI calculator ----------------------------------------------------

  const ROI_FIELDS = [
    { key: "monthly_parcels", label: "Monthly parcels", min: 10000, max: 1000000, step: 10000, format: num },
    { key: "cod_share_pct", label: "COD share", min: 0, max: 100, step: 1, format: pct },
    { key: "first_attempt_failure_rate_pct", label: "First-attempt failure", min: 0, max: 50, step: 1, format: pct },
    { key: "preventable_share_pct", label: "Preventable share", min: 0, max: 100, step: 1, format: pct },
    { key: "intervention_success_rate_pct", label: "Intervention success", min: 0, max: 100, step: 1, format: pct },
    { key: "wismo_contacts_per_parcel", label: "WISMO contacts / parcel", min: 0, max: 3, step: 0.1, format: (v) => v.toFixed(1) },
    { key: "deflection_rate_pct", label: "Deflection rate", min: 0, max: 100, step: 1, format: pct },
    { key: "human_cost_per_query_pkr", label: "Human cost / query", min: 0, max: 200, step: 1, format: (v) => pkr(v, { compact: false }) },
    { key: "bot_cost_per_query_pkr", label: "Bot cost / query", min: 0, max: 50, step: 0.5, format: (v) => pkr(v, { compact: false }) },
    { key: "rto_cost_pkr", label: "Cost per RTO", min: 0, max: 2000, step: 10, format: (v) => pkr(v, { compact: false }) },
  ];

  function renderRoiInputs(defaults) {
    const container = $("roi-inputs");
    clear(container);
    for (const field of ROI_FIELDS) {
      const value = defaults[field.key];
      state.roiInputs[field.key] = value;
      const readout = el("span", { class: "val", text: field.format(value) });
      const input = el("input", {
        type: "range", min: field.min, max: field.max, step: field.step, value,
        "aria-label": field.label,
      });
      input.addEventListener("input", () => {
        const next = Number(input.value);
        state.roiInputs[field.key] = next;
        readout.textContent = field.format(next);
        scheduleRoi();
      });
      container.appendChild(el("div", { class: "roi-field" }, [
        el("label", {}, [el("span", { text: field.label }), readout]),
        input,
      ]));
    }
  }

  function renderRoiResult(result) {
    const support = result.support_lever;
    const rto = result.rto_lever;

    $("roi-hero").textContent = pkr(result.totals.annual_saving_pkr);
    $("roi-hero-sub").textContent =
      `${pkr(result.totals.monthly_saving_pkr)} per month · RTO is ${pct(result.totals.rto_share_of_total_pct)} of it`;

    const levers = $("roi-levers");
    clear(levers);
    for (const lever of [
      { name: "Support deflection", color: "var(--series-1)", value: support.annual_saving_pkr },
      { name: "RTO prevented", color: "var(--series-2)", value: rto.annual_saving_pkr },
    ]) {
      levers.appendChild(el("div", { class: "lever" }, [
        el("span", { class: "swatch", style: `background:${lever.color}` }),
        el("span", { text: lever.name }),
        el("span", { class: "amount", text: pkr(lever.value) }),
      ]));
    }

    renderTable($("roi-funnel"), [
      { label: "Monthly funnel", get: (r) => r[0] },
      { label: "Parcels", num: true, get: (r) => r[1] },
    ], [
      ["COD parcels", num(rto.monthly_cod_parcels)],
      ["Failed first attempts", num(rto.monthly_failed_first_attempts)],
      ["Preventable failures", num(rto.monthly_preventable_failures)],
      ["RTOs prevented", num(rto.monthly_rto_prevented)],
      ["WISMO queries deflected", num(support.monthly_deflected)],
    ]);
  }

  let roiTimer = null;

  function scheduleRoi() {
    clearTimeout(roiTimer);
    // Guarded: this fires from a timer with no caller to catch it, so a failed
    // request would otherwise be an unhandled rejection — the sliders would move and
    // the figures would silently keep showing the last good result.
    roiTimer = setTimeout(() => guarded(runRoi), ROI_DEBOUNCE_MS);
  }

  async function runRoi() {
    const result = await api("/ops/roi/simulate", {
      method: "POST",
      body: JSON.stringify(state.roiInputs),
    });
    renderRoiResult(result);
  }

  async function loadRoi() {
    const assumptions = await api("/ops/roi/assumptions");
    state.roiDefaults = assumptions.defaults;
    renderRoiInputs(assumptions.defaults);
    $("roi-note").textContent = assumptions.note;

    // Measured rates travel with their sample size — a 100% rate over 3 interactions
    // must not read like a 100% rate over 3,000.
    const measured = assumptions.measured;
    const note = $("roi-measured");
    clear(note);
    if (measured) {
      note.appendChild(el("span", { text: "Measured so far: " }));
      note.appendChild(el("b", { text: pct(measured.deflection_rate_pct) }));
      note.appendChild(el("span", { text: ` deflection over ${num(measured.sample_interactions)} interactions · ` }));
      note.appendChild(el("b", { text: pct(measured.intervention_success_rate_pct) }));
      note.appendChild(el("span", {
        text: ` intervention success over ${num(measured.sample_resolved_interventions)} resolved. Sliders above use the model defaults.`,
      }));
    }
    await runRoi();
  }

  // --- polling -----------------------------------------------------------

  function setLive(stateName, text) {
    $("live").dataset.state = stateName;
    $("live-text").textContent = text;
  }

  function markLoading(on) {
    document.querySelectorAll("main .card").forEach((card) => card.classList.toggle("loading", on));
  }

  async function guarded(task, { dim = false } = {}) {
    if (dim) markLoading(true);
    try {
      await task();
      setLive("live", "Live");
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        signOut("Session rejected — enter the token again.");
        return;
      }
      setLive("error", error instanceof ApiError && error.status === 503
        ? "DASHBOARD_TOKEN not set"
        : "Connection lost — retrying");
    } finally {
      if (dim) markLoading(false);
    }
  }

  /** Self-scheduling poll: the next run is booked only once the previous one has
   *  finished, so the gap is measured from completion rather than from a fixed wall
   *  clock. setInterval did the latter and never awaited the request, so whenever the
   *  database was slower than the interval — routine here, the pooler is remote and
   *  this machine drops DNS — ticks overlapped and in-flight requests piled up
   *  without bound. One request per tier is in flight at a time now. */
  function pollLoop(name, task, delay) {
    state.timers[name] = setTimeout(async () => {
      if (!state.polling) return;
      if (!document.hidden) await guarded(task);
      if (state.polling) pollLoop(name, task, delay);
    }, delay);
  }

  function startPolling() {
    stopPolling();
    state.polling = true;
    pollLoop("conversations", refreshConversations, POLL_CONVO_MS);
    pollLoop("cases", refreshCaseFeed, POLL_CASES_MS);
    pollLoop("performance", refreshPerformance, POLL_PERF_MS);
  }

  function stopPolling() {
    state.polling = false;   // also stops a loop whose timer has already fired
    Object.values(state.timers).forEach(clearTimeout);
    state.timers = {};
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      setLive("paused", "Paused");
    } else if (state.token) {
      guarded(refreshOps);
      guarded(refreshPerformance);
    }
  });

  // --- boot --------------------------------------------------------------

  function signOut(message) {
    stopPolling();
    state.token = "";
    state.writeToken = "";
    state.convoSignature = null;   // force a rebuild on the next sign-in
    sessionStorage.removeItem("zentric_token");
    sessionStorage.removeItem("zentric_write_token");
    $("gate").hidden = false;
    $("gate-error").textContent = message || "";
    $("token-input").value = "";
    $("write-token-input").value = "";
  }

  async function start() {
    $("gate").hidden = true;
    await guarded(async () => {
      await refreshPerformance();
      await refreshOps();
      await loadRoi();
    }, { dim: true });
    startPolling();
  }

  $("gate-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const token = $("token-input").value.trim();
    if (!token) return;
    state.token = token;
    // Optional. Without it everything still loads; only the handoff buttons are
    // unusable, which is the honest representation of a read-only credential.
    state.writeToken = $("write-token-input").value.trim();
    try {
      await api("/ops/conversations?limit=1");
      sessionStorage.setItem("zentric_token", token);
      if (state.writeToken) sessionStorage.setItem("zentric_write_token", state.writeToken);
      $("gate-error").textContent = "";
      start();
    } catch (error) {
      state.token = "";
      $("gate-error").textContent = error.status === 503
        ? "DASHBOARD_TOKEN is not set on the server — the ops API is closed."
        : "That token was rejected.";
    }
  });

  $("sign-out").addEventListener("click", () => signOut());

  $("range-seg").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-days]");
    if (!button) return;
    state.days = Number(button.dataset.days);
    $("range-seg").querySelectorAll("button").forEach((b) =>
      b.setAttribute("aria-pressed", String(b === button)));
    guarded(refreshPerformance, { dim: true });
  });

  $("handoff-seg").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-status]");
    if (!button) return;
    state.handoffStatus = button.dataset.status;
    $("handoff-seg").querySelectorAll("button").forEach((b) =>
      b.setAttribute("aria-pressed", String(b === button)));
    guarded(refreshHandoffs);
  });

  $("case-seg").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-type]");
    if (!button) return;
    state.caseType = button.dataset.type;
    $("case-seg").querySelectorAll("button").forEach((b) =>
      b.setAttribute("aria-pressed", String(b === button)));
    guarded(refreshCaseFeed);   // only the case feed changed — don't re-poll everything
  });

  document.querySelectorAll("[data-table-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const name = button.dataset.tableToggle;
      const table = $(`table-${name}`);
      const chart = $(`chart-${name}`);
      const showTable = table.hidden;
      table.hidden = !showTable;
      chart.hidden = showTable;
      button.textContent = showTable ? "Chart" : "Table";
    });
  });

  $("theme-toggle").addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme");
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const next = current ? (current === "dark" ? "light" : "dark") : (prefersDark ? "light" : "dark");
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("zentric_theme", next);
    redrawCharts();
  });

  const savedTheme = localStorage.getItem("zentric_theme");
  if (savedTheme) document.documentElement.setAttribute("data-theme", savedTheme);

  // Charts are drawn at real pixel sizes (so strokes stay 2px); re-draw on resize.
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(redrawCharts, 150);
  });

  if (state.token) start();
})();
