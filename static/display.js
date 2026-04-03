/**
 * display.js — Shared rendering logic for index.html and date.html.
 *
 * Data contract (papers in JSON):
 *   { id, title, abstract, authors[], url, matched_topics[], match_method,
 *     best_score, backfilled? }
 *
 * Papers arrive pre-sorted by rank from the pipeline:
 *   1. keyword + semantic  ("both")
 *   2. keyword-only        ("keyword")
 *   3. semantic-only       ("semantic", descending by score)
 */

const DigestDisplay = (() => {

  // ── Constants ──────────────────────────────────────────────────────────────
  const PAGE_SIZE = 10;

  const CHIP_PALETTES = [
    ["#dbeafe","#1d4ed8","#93c5fd","#3b82f6"],
    ["#dcfce7","#15803d","#86efac","#22c55e"],
    ["#fef3c7","#b45309","#fcd34d","#f59e0b"],
    ["#fce7f3","#be185d","#f9a8d4","#ec4899"],
    ["#ede9fe","#6d28d9","#c4b5fd","#8b5cf6"],
    ["#ccfbf1","#0f766e","#5eead4","#14b8a6"],
  ];

  const MISC_STYLE = {
    background: "#f1f5f9", color: "#64748b", borderColor: "#cbd5e1"
  };
  const MISC_ACCENT = "#e2e8f0";

  // ── Match method badge ─────────────────────────────────────────────────────
  function _methodBadge(method) {
    if (method === "keyword" || method === "both") {
      return `<span class="method-badge method-keyword">keyword</span>`;
    }
    if (method === "semantic") {
      return `<span class="method-badge method-semantic">semantic</span>`;
    }
    return "";
  }

  // ── Topic style maps ───────────────────────────────────────────────────────
  let activeStyles   = {};
  let inactiveStyles = {};
  let accentColors   = {};
  let allTopics      = [];

  function buildTopicStyles(topics) {
    allTopics = topics;
    let palIdx = 0;
    topics.forEach((t) => {
      if (t === "misc") {
        activeStyles[t]   = MISC_STYLE;
        inactiveStyles[t] = { background: "#f1f5f9", color: "#94a3b8", borderColor: "#e2e8f0" };
        accentColors[t]   = MISC_ACCENT;
        return;
      }
      const pal = CHIP_PALETTES[palIdx % CHIP_PALETTES.length];
      activeStyles[t]   = { background: pal[0], color: pal[1], borderColor: pal[2] };
      inactiveStyles[t] = { background: "#f1f5f9", color: "#94a3b8", borderColor: "#e2e8f0" };
      accentColors[t]   = pal[3];
      palIdx++;
    });
  }

  // ── Sidebar ────────────────────────────────────────────────────────────────
  function openSidebar() {
    document.getElementById("sidebar").classList.add("open");
    document.getElementById("overlay").classList.add("open");
    document.body.style.overflow = "hidden";
  }

  function closeSidebar() {
    document.getElementById("sidebar").classList.remove("open");
    document.getElementById("overlay").classList.remove("open");
    document.body.style.overflow = "";
  }

  // ── Calendar ───────────────────────────────────────────────────────────────
  // Paginated single-month view with ‹ / › navigation.
  // availableDatesHierarchy: { "2026": { "02": ["27","26",...], "01": [...] }, ... }
  // todayStr: "2026-02-27"
  // onDateClick: function(dateStr)
  function buildCalendar(availableDatesHierarchy, todayStr, onDateClick) {
    const body = document.getElementById("calendar-body");
    if (!body) return;

    // Flatten to a Set for O(1) lookup
    const hasData = new Set();
    Object.entries(availableDatesHierarchy).forEach(([y, months]) => {
      Object.entries(months).forEach(([m, days]) => {
        days.forEach(d => hasData.add(`${y}-${m}-${d}`));
      });
    });

    // Build sorted month list (newest-first), filling any gaps in between
    // so the user can navigate through months that have no data too.
    const dataMonthKeys = [];
    Object.entries(availableDatesHierarchy).forEach(([y, months]) => {
      Object.keys(months).forEach(m => dataMonthKeys.push(`${y}-${String(m).padStart(2,"0")}`));
    });
    dataMonthKeys.sort().reverse();

    const monthKeys = [];
    if (dataMonthKeys.length > 0) {
      // Walk from newest month down to oldest, filling gaps
      let [cy, cm] = dataMonthKeys[0].split("-").map(Number);
      const [oy, om] = dataMonthKeys[dataMonthKeys.length - 1].split("-").map(Number);
      while (cy > oy || (cy === oy && cm >= om)) {
        monthKeys.push(`${cy}-${String(cm).padStart(2,"0")}`);
        cm--;
        if (cm === 0) { cm = 12; cy--; }
      }
    }

    let currentIdx = 0; // 0 = most recent month

    const DOWS = ["Su","Mo","Tu","We","Th","Fr","Sa"];

    function renderMonth() {
      body.innerHTML = "";
      if (!monthKeys.length) return;

      const ym          = monthKeys[currentIdx];
      const [y, m]      = ym.split("-").map(Number);
      const label       = new Date(y, m-1, 1)
        .toLocaleString("en-US", { month: "long", year: "numeric" });
      const firstDow    = new Date(y, m-1, 1).getDay();
      const daysInMonth = new Date(y, m, 0).getDate();

      // ── Nav row: ‹ March 2026 › ──
      const nav = document.createElement("div");
      nav.className = "cal-nav";

      const prevBtn = document.createElement("button");
      prevBtn.className = "cal-nav-btn";
      prevBtn.textContent = "‹";
      prevBtn.title = "Previous month";
      prevBtn.disabled = currentIdx >= monthKeys.length - 1;
      prevBtn.onclick = () => { currentIdx++; renderMonth(); };

      const monthLabel = document.createElement("span");
      monthLabel.className = "cal-nav-label";
      monthLabel.textContent = label;

      const nextBtn = document.createElement("button");
      nextBtn.className = "cal-nav-btn";
      nextBtn.textContent = "›";
      nextBtn.title = "Next month";
      nextBtn.disabled = currentIdx === 0;
      nextBtn.onclick = () => { currentIdx--; renderMonth(); };

      nav.appendChild(prevBtn);
      nav.appendChild(monthLabel);
      nav.appendChild(nextBtn);
      body.appendChild(nav);

      // ── Day grid ──
      const grid = document.createElement("div");
      grid.className = "cal-grid";

      DOWS.forEach(d => {
        const h = document.createElement("div");
        h.className = "cal-dow"; h.textContent = d;
        grid.appendChild(h);
      });

      for (let i = 0; i < firstDow; i++) {
        const e = document.createElement("div");
        e.className = "cal-day empty";
        grid.appendChild(e);
      }

      for (let day = 1; day <= daysInMonth; day++) {
        const ds = `${y}-${String(m).padStart(2,"0")}-${String(day).padStart(2,"0")}`;
        const cell = document.createElement("div");
        cell.textContent = day;

        if (ds === todayStr) {
          cell.className = "cal-day is-today has-data";
          cell.onclick = () => { closeSidebar(); onDateClick(ds); };
        } else if (hasData.has(ds)) {
          cell.className = "cal-day has-data";
          cell.onclick = () => { closeSidebar(); onDateClick(ds); };
        } else {
          cell.className = "cal-day";
        }
        grid.appendChild(cell);
      }

      body.appendChild(grid);
    }

    renderMonth();
  }

  // ── Filter chips ───────────────────────────────────────────────────────────
  function buildFilterChips(containerId, activeTopics, onToggle) {
    const bar = document.getElementById(containerId);
    if (!bar) return;
    bar.innerHTML = "";

    allTopics.forEach(t => {
      const btn = document.createElement("button");
      btn.className = "chip";
      btn.textContent = t;
      _applyChipStyle(btn, t, activeTopics.has(t));
      btn.addEventListener("click", () => {
        activeTopics.has(t) ? activeTopics.delete(t) : activeTopics.add(t);
        _applyChipStyle(btn, t, activeTopics.has(t));
        onToggle();
      });
      bar.appendChild(btn);
    });
  }

  function _applyChipStyle(el, t, on) {
    const s = on ? (activeStyles[t] || MISC_STYLE) : (inactiveStyles[t] || MISC_STYLE);
    el.style.background  = s.background;
    el.style.color       = s.color;
    el.style.borderColor = s.borderColor;
  }

  // ── Paper card ─────────────────────────────────────────────────────────────
  function buildCard(p) {
    const isMisc  = (p.matched_topics || []).length === 1 && p.matched_topics[0] === "misc";
    const preview = p.abstract.slice(0, 320) + (p.abstract.length > 320 ? "…" : "");
    const hasMore = p.abstract.length > 320;
    const accent  = accentColors[(p.matched_topics || [])[0]] || "#cbd5e1";

    const chips = (p.matched_topics || []).map(t => {
        const s = activeStyles[t] || MISC_STYLE;
        return `<span class="topic-chip" style="background:${s.background};color:${s.color};border-color:${s.borderColor}">${t}</span>`;
      }).join("")
      + (isMisc ? `<span class="score-badge">score ${p.best_score}</span>` : "");

    const authorsArr = p.authors || [];
    const authorsStr = authorsArr.slice(0, 3).join(", ") + (authorsArr.length > 3 ? " et al." : "");
    const uid    = p.id.replace(/[^a-zA-Z0-9]/g, "-");
    const pdfUrl = p.url.replace("/abs/", "/pdf/");
    const backfilledBadge = p.backfilled
      ? `<span class="backfilled-badge">⚡ Late addition</span>`
      : "";

    const card = document.createElement("div");
    card.className = "paper" + (isMisc ? " irrelevant" : "");
    card.style.borderLeftColor = accent;
    card.innerHTML = `
      ${backfilledBadge}
      <a class="paper-title" href="${p.url}" target="_blank">${p.title}</a>
      <div class="paper-authors">${authorsStr}</div>
      <div class="abstract-preview" id="pv-${uid}">${preview}</div>
      ${hasMore ? `
        <div class="abstract-full" id="fl-${uid}">${p.abstract}</div>
        <button class="expand-btn" id="btn-${uid}" onclick="DigestDisplay.toggle('${uid}')">Show more ↓</button>
      ` : ""}
      <div class="paper-footer">${chips}<a class="pdf-link" href="${pdfUrl}" target="_blank">PDF →</a></div>
    `;
    return card;
  }

  // ── Expand/collapse abstract ───────────────────────────────────────────────
  function toggle(uid) {
    const pv  = document.getElementById("pv-"  + uid);
    const fl  = document.getElementById("fl-"  + uid);
    const btn = document.getElementById("btn-" + uid);
    if (!fl) return;
    const expanded = fl.style.display === "block";
    pv.style.display  = expanded ? "block" : "none";
    fl.style.display  = expanded ? "none"  : "block";
    btn.textContent   = expanded ? "Show more ↓" : "Show less ↑";
  }

  // ── Render paper lists ─────────────────────────────────────────────────────
  function renderList({ containerId, paginationId, papers, page, onPage }) {
    const list = document.getElementById(containerId);
    if (list) {
      list.innerHTML = "";
      if (!papers.length) {
        list.innerHTML = '<div style="text-align:center;color:#94a3b8;padding:48px 0;font-size:14px">No papers match the selected topics.</div>';
      } else {
        papers
          .slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)
          .forEach(p => list.appendChild(buildCard(p)));
      }
    }
    renderPagination(paginationId, papers.length, page, onPage);
  }

  // ── Pagination ─────────────────────────────────────────────────────────────
  function renderPagination(containerId, total, current, onPage) {
    const pages = Math.ceil(total / PAGE_SIZE);
    const c = document.getElementById(containerId);
    if (!c) return;
    c.innerHTML = "";
    if (pages <= 1) return;

    const prev = document.createElement("button");
    prev.className = "page-btn"; prev.textContent = "← Prev";
    prev.disabled = current === 1;
    prev.onclick = () => onPage(current - 1);
    c.appendChild(prev);

    const range = [];
    for (let i = 1; i <= pages; i++) {
      if (i===1 || i===pages || Math.abs(i-current)<=2) range.push(i);
      else if (range[range.length-1] !== "…") range.push("…");
    }
    range.forEach(item => {
      if (item === "…") {
        const s = document.createElement("span");
        s.className = "page-info"; s.textContent = "…"; c.appendChild(s);
      } else {
        const b = document.createElement("button");
        b.className = "page-btn" + (item===current ? " active" : "");
        b.textContent = item;
        b.onclick = () => onPage(item);
        c.appendChild(b);
      }
    });

    const next = document.createElement("button");
    next.className = "page-btn"; next.textContent = "Next →";
    next.disabled = current === pages;
    next.onclick = () => onPage(current + 1);
    c.appendChild(next);
  }

  // ── Loading / error helpers ────────────────────────────────────────────────
  function showLoading(containerId) {
    const el = document.getElementById(containerId);
    if (el) el.innerHTML = '<div class="loading"><div class="spinner"></div>Loading…</div>';
  }

  function showError(containerId, title, msg) {
    const el = document.getElementById(containerId);
    if (el) el.innerHTML = `<div class="state"><h2>${title}</h2><p>${msg}</p></div>`;
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  return {
    buildTopicStyles,
    buildCalendar,
    buildFilterChips,
    buildCard,
    toggle,
    renderList,
    renderPagination,
    openSidebar,
    closeSidebar,
    showLoading,
    showError,
  };

})();