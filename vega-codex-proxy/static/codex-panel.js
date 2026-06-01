/* codex 状态仪表盘逻辑 — 轮询 /healthz 渲染实时状态。无 emoji 唯 SVG。
 * 用法: CodexPanel.mount(document.getElementById('codex-root'), { endpoint: '/healthz', intervalMs: 5000 })
 */
(function (global) {
  "use strict";

  // ── SVG 图标 (无 emoji) ──
  var ICON = {
    logo:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>',
    server:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="8" rx="2"/><rect x="2" y="13" width="20" height="8" rx="2"/><line x1="6" y1="7" x2="6.01" y2="7"/><line x1="6" y1="17" x2="6.01" y2="17"/></svg>',
    mode:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>',
    users:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
    tag:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>',
    refresh:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>',
  };

  function tile(icon, label, id) {
    return (
      '<div class="tile"><span class="ic">' + icon + "</span>" +
      '<span class="meta"><div class="label">' + label + "</div>" +
      '<div class="val" id="' + id + '">—</div></span></div>'
    );
  }

  function render(root) {
    root.innerHTML =
      '<div class="codex-card">' +
      '<div class="codex-head">' +
      '<span class="logo">' + ICON.logo + "</span>" +
      "<div><h1>Codex 状态</h1><p class=\"sub\">vega-codex-proxy 实时健康</p></div>" +
      '<span class="dot" id="cx-dot"><i></i><span id="cx-dot-text">连接中</span></span>' +
      "</div>" +
      '<div class="codex-grid">' +
      tile(ICON.server, "App-Server", "cx-appserver") +
      tile(ICON.mode, "运行模式", "cx-mode") +
      tile(ICON.users, "活跃会话", "cx-sessions") +
      tile(ICON.tag, "版本", "cx-version") +
      "</div>" +
      '<div class="codex-foot"><span id="cx-updated">等待数据…</span>' +
      '<span class="spin">' + ICON.refresh + "</span></div>" +
      "</div>";
  }

  function set(id, text, cls) {
    var el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    el.className = "val" + (cls ? " " + cls : "");
  }

  function pad(n) { return n < 10 ? "0" + n : "" + n; }
  function now() {
    var d = new Date();
    return pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }

  function applyOnline(data) {
    var dot = document.getElementById("cx-dot");
    var dotText = document.getElementById("cx-dot-text");
    dot.className = "dot online";
    dotText.textContent = "代理在线";
    var ready = !!data.app_server_initialized;
    set("cx-appserver", ready ? "已就绪" : "待命", ready ? "ok" : "warn");
    set("cx-mode", ready ? "app-server" : "exec 待拉起", ready ? "ok" : "");
    var n = data.sessions_cached;
    set("cx-sessions", (n === 0 || n) ? String(n) : "—");
    set("cx-version", data.version ? "v" + data.version : "未知");
    document.getElementById("cx-updated").textContent = "更新于 " + now() + " · 每 5s 刷新";
  }

  function applyOffline(err) {
    var dot = document.getElementById("cx-dot");
    dot.className = "dot offline";
    document.getElementById("cx-dot-text").textContent = "代理离线";
    set("cx-appserver", "不可达", "bad");
    set("cx-mode", "—");
    set("cx-sessions", "—");
    set("cx-version", "—");
    document.getElementById("cx-updated").textContent =
      "无法连接 · " + now() + (err ? " (" + err + ")" : "");
  }

  function poll(endpoint) {
    return fetch(endpoint, { cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(applyOnline)
      .catch(function (e) { applyOffline(e && e.message); });
  }

  var CodexPanel = {
    mount: function (root, opts) {
      opts = opts || {};
      var endpoint = opts.endpoint || "/healthz";
      var intervalMs = opts.intervalMs || 5000;
      render(root);
      poll(endpoint);
      if (intervalMs > 0) setInterval(function () { poll(endpoint); }, intervalMs);
      return CodexPanel;
    },
  };

  global.CodexPanel = CodexPanel;
})(typeof window !== "undefined" ? window : this);
