/* Vega Chat inject.js — 注入进 LibreChat (sed 进 dist/index.html)
 *
 * 组3 TickB 重写: setInterval → MutationObserver 架构。
 * 模块:
 *   1. loadCss        — 注入 codex-enhance.css
 *   2. codex 折叠引擎  — bash/console 代码块默认折叠 + 手动展开 (MutationObserver
 *                        + 外部状态 Map, React 重渲染后恢复折叠态)
 *   3. admin FAB      — 右下角管理浮动按钮 (Tick5 将改右上角齿轮, 暂留)
 *
 * 铁律: 所有逻辑 try/catch 静默降级, 注入失败绝不破坏 LibreChat 原站。
 * 选择器走语义 Tailwind token + 多 fallback, 防镜像升级。
 */
(function () {
  "use strict";
  if (window.__vegaInjected) return;
  window.__vegaInjected = true;

  // ────── 1. 加载折叠样式 ──────
  function loadCss() {
    try {
      if (document.getElementById("vega-codex-css")) return;
      var l = document.createElement("link");
      l.id = "vega-codex-css";
      l.rel = "stylesheet";
      l.href = "/vega-admin/static/codex-enhance.css";
      (document.head || document.documentElement).appendChild(l);
      if (!document.getElementById("vega-theme-css")) {
        var t = document.createElement("link");
        t.id = "vega-theme-css";
        t.rel = "stylesheet";
        t.href = "/vega-admin/static/vega-theme.css";
        (document.head || document.documentElement).appendChild(t);
      }
    } catch (e) {}
  }

  // ────── 品牌角标 (Vega Chat wordmark, 标明非 stock LibreChat) ──────
  function ensureBrand() {
    try {
      if (document.getElementById("vega-brand-badge") || !document.body) return;
      var b = document.createElement("div");
      b.id = "vega-brand-badge";
      b.title = "Vega Chat — codex 增强版";
      b.innerHTML =
        '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l8.66 5v10L12 22l-8.66-5V7L12 2zm0 2.31L5.34 8.15v7.7L12 19.69l6.66-3.84v-7.7L12 4.31z"/><path d="M8.5 8.5l3.5 7 3.5-7h-1.9L12 11.7 10.4 8.5z"/></svg>' +
        '<span>Vega Chat</span><span class="vbb-sub">· codex</span>';
      document.body.appendChild(b);
    } catch (e) {}
  }

  // ────── 2. codex 命令/工具输出折叠引擎 ──────
  // 只折叠命令/输出语言块 (codex 用 bash 包命令、console 包输出)
  var FOLD_LANGS = { bash: 1, console: 1, sh: 1, shell: 1, "shell-session": 1, zsh: 1 };
  var foldState = new Map(); // key(代码文本) → collapsed bool, 跨 React 重渲染保态
  var CHEVRON =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" ' +
    'stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';

  function blockKey(container) {
    var code = container.querySelector("code") || container;
    return (code.textContent || "").trim().slice(0, 140);
  }

  // 真实结构: div.rounded-xl > [headerWrapper(含 header), codeContent]
  // 内容区 = header 在 container 内的顶层祖先的下一个兄弟
  function findBody(container, header) {
    var top = header;
    while (top.parentElement && top.parentElement !== container) top = top.parentElement;
    return top.nextElementSibling;
  }

  function findHeader(container) {
    return (
      container.querySelector(".bg-surface-primary-alt") ||
      container.querySelector('[class*="bg-surface"][class*="justify-between"]') ||
      container.querySelector("div.flex.items-center.justify-between")
    );
  }

  function enhanceBlock(container) {
    try {
      var header = findHeader(container);
      if (!header) return;
      var span = header.querySelector("span");
      var lang = ((span && span.textContent) || "").trim().toLowerCase();
      if (!FOLD_LANGS[lang]) return; // 非命令/输出块不折叠
      var body = findBody(container, header);
      if (!body || body === header) return;

      var key = blockKey(container);
      container.classList.add("vega-cmd");
      header.classList.add("vega-cmd-header");
      body.classList.add("vega-cmd-body");

      // 默认折叠; 已有用户操作则沿用 Map 里的态 (React 重渲染恢复)
      var collapsed = foldState.has(key) ? foldState.get(key) : true;
      if (!foldState.has(key)) foldState.set(key, true);
      container.classList.toggle("vega-collapsed", collapsed);

      // 加 chevron 折叠按钮 (重渲染会抹掉 → 不存在才加, 幂等)
      if (!header.querySelector(".vega-fold-toggle")) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "vega-fold-toggle";
        btn.setAttribute("aria-label", "折叠或展开命令输出");
        btn.innerHTML = CHEVRON;
        btn.addEventListener("click", function (e) {
          e.stopPropagation();
          e.preventDefault();
          var now = !container.classList.contains("vega-collapsed");
          container.classList.toggle("vega-collapsed", now);
          foldState.set(key, now);
        });
        header.insertBefore(btn, header.firstChild);
      }

      // 命令/内容预览 + 行数: 折叠时也能一眼知道是啥命令/多少行输出
      try {
        var rawText = (body.textContent || "").replace(/\s+/g, " ").trim();
        var nLines = (body.textContent || "").split("\n").filter(function (l) {
          return l.trim();
        }).length;
        var pv = header.querySelector(".vega-cmd-preview");
        if (!pv && span) {
          pv = document.createElement("span");
          pv.className = "vega-cmd-preview";
          span.appendChild(pv);
        }
        if (pv) pv.textContent = rawText.slice(0, 90);
        var lc = header.querySelector(".vega-cmd-lines");
        if (nLines > 1) {
          if (!lc && span) {
            lc = document.createElement("span");
            lc.className = "vega-cmd-lines";
            span.appendChild(lc);
          }
          if (lc) lc.textContent = nLines + " 行";
        } else if (lc) {
          lc.textContent = "";
        }
      } catch (e2) {}

      // header 整体可点折叠 (新节点才绑, 避免重复; 跳过复制按钮)
      if (!container.getAttribute("data-vega-fold")) {
        header.addEventListener("click", function (e) {
          if (e.target.closest && e.target.closest("button:not(.vega-fold-toggle)")) return;
          if (e.target.closest && e.target.closest(".vega-fold-toggle")) return;
          var now = !container.classList.contains("vega-collapsed");
          container.classList.toggle("vega-collapsed", now);
          foldState.set(key, now);
        });
        container.setAttribute("data-vega-fold", "1");
      }
    } catch (e) {
      /* 静默降级 */
    }
  }

  function scanFolds() {
    try {
      var blocks = document.querySelectorAll('div[class*="rounded-xl"]');
      for (var i = 0; i < blocks.length; i++) enhanceBlock(blocks[i]);
    } catch (e) {}
  }

  // ────── token 用量 chip (替换 proxy 吐的 `vega-usage ...` inline code) ──────
  var USAGE_RE = /^vega-usage\s+in=(\d+)\s+out=(\d+)\s+cached=(\d+)\s+total=(\d+)/;
  // 用量计量图标 (脉冲/活动线, 表"用量"非货币; token 是计数单位)
  var USAGE_IC =
    '<svg class="vega-tok-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>';

  function fmtK(n) {
    n = +n || 0;
    if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "k";
    return "" + n;
  }

  function transformChip(code) {
    try {
      if (code.classList.contains("vega-tok-chip")) return; // 已处理
      var m = USAGE_RE.exec((code.textContent || "").trim());
      if (!m) return;
      var inp = +m[1], out = +m[2], cached = +m[3], tot = +m[4];
      var hit = inp > 0 ? Math.round((cached / inp) * 100) : 0;
      code.classList.add("vega-tok-chip");
      code.setAttribute(
        "title",
        "输入 " + inp + " · 输出 " + out + " · 缓存 " + cached +
          " · 缓存命中 " + hit + "% · 合计 " + tot
      );
      code.innerHTML =
        USAGE_IC +
        '<span class="vega-tok-seg">入 ' + fmtK(inp) + "</span>" +
        '<span class="vega-tok-seg">出 ' + fmtK(out) + "</span>" +
        '<span class="vega-tok-seg">缓 ' + fmtK(cached) + "</span>" +
        '<span class="vega-tok-seg vega-tok-hit">命中 ' + hit + "%</span>";
    } catch (e) {}
  }

  function scanChips() {
    try {
      var codes = document.querySelectorAll("code");
      for (var i = 0; i < codes.length; i++) {
        var c = codes[i];
        if (c.classList.contains("vega-tok-chip")) continue;
        var t = (c.textContent || "").trim();
        if (t.indexOf("vega-usage ") === 0) transformChip(c);
      }
    } catch (e) {}
  }

  // ────── 3. admin FAB (暂留, Tick5 改右上角齿轮) ──────
  var isAdminCached = false;
  function isAdmin() {
    if (isAdminCached) return Promise.resolve(true);
    return fetch("/vega-admin/api/me", { credentials: "include" })
      .then(function (r) {
        return r.ok ? r.json() : null;
      })
      .then(function (d) {
        var ok = !!(d && d.loggedIn && d.isAdmin);
        if (ok) isAdminCached = true;
        return ok;
      })
      .catch(function () {
        return false;
      });
  }

  var GEAR =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/>' +
    '<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>';

  function codexApi(path, opts) {
    return fetch(
      "/vega-admin/api/codex/" + path,
      Object.assign({ credentials: "include" }, opts || {})
    )
      .then(function (r) {
        return r.json();
      })
      .catch(function () {
        return { ok: false };
      });
  }

  function vtoast(msg) {
    try {
      var t = document.createElement("div");
      t.className = "vega-toast";
      t.textContent = msg;
      document.body.appendChild(t);
      setTimeout(function () {
        t.classList.add("vega-toast-out");
      }, 1800);
      setTimeout(function () {
        if (t.parentNode) t.parentNode.removeChild(t);
      }, 2300);
    } catch (e) {}
  }

  function refreshPanel() {
    codexApi("info").then(function (d) {
      var s = document.getElementById("vp-status");
      if (!s) return;
      if (d && d.ok) {
        var _on = d.appServerInitialized;
        s.innerHTML = _on ? '<span class="vega-live-dot"></span>在线' : "待命";
        s.className = "vega-panel-v " + (_on ? "vega-ok" : "vega-warn");
        document.getElementById("vp-thread").textContent =
          (d.threadId ? d.threadId.slice(0, 8) : "无会话") +
          (d.archived ? " (已关闭)" : "") +
          " · " + (d.sessionCount || 0) + " 个";
        document.getElementById("vp-ver").textContent = d.version ? "v" + d.version : "—";
      } else {
        s.textContent = "不可达";
        s.className = "vega-panel-v vega-bad";
      }
    });
    refreshHealth(false);
  }

  var HEALTH_LABEL = { ok: "正常", warn: "注意", fail: "异常", unknown: "—" };
  function refreshHealth(force) {
    var el = document.getElementById("vp-health");
    if (!el) return;
    el.innerHTML = '<span class="vega-shimmer">诊断中…</span>';
    el.className = "vega-panel-v";
    onboardApi("diagnose" + (force ? "?force=true" : "")).then(function (d) {
      if (!d || !d.ok) {
        el.textContent = "不可达";
        el.className = "vega-panel-v vega-bad";
        return;
      }
      var ov = d.overall || "unknown";
      var dot = ov === "ok" ? "ok" : ov === "warn" ? "warn" : "bad";
      var cls = ov === "ok" ? "vega-ok" : ov === "warn" ? "vega-warn" : "vega-bad";
      var nfail = (d.items || []).filter(function (i) { return i.status === "fail"; }).length;
      el.innerHTML =
        '<span class="vega-live-dot vega-dot-' + dot + '"></span>' +
        (HEALTH_LABEL[ov] || ov) + (nfail ? "（" + nfail + " 项需处理）" : "");
      el.className = "vega-panel-v " + cls;
    });
  }

  // ── codex 会话管理器 (列表 + 重命名 + 归档) ──
  function renderSessions(list) {
    var box = document.getElementById("vsm-list");
    if (!box) return;
    if (!list || !list.length) {
      box.innerHTML = '<div class="vsm-empty">暂无 codex 会话</div>';
      return;
    }
    box.innerHTML = "";
    list.forEach(function (s) {
      var row = document.createElement("div");
      row.className = "vsm-row" + (s.archived ? " vsm-archived" : "");
      var name = s.label || s.thread_id.slice(0, 12);
      row.innerHTML =
        '<span class="vsm-name">' + name + (s.archived ? ' <span class="vsm-badge">已关闭</span>' : "") + "</span>";
      var ren = document.createElement("button");
      ren.className = "vsm-act"; ren.textContent = "重命名";
      ren.addEventListener("click", function () {
        var nv = window.prompt("会话标签", s.label || "");
        if (nv == null) return;
        codexApi("rename", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ threadId: s.thread_id, label: nv }) })
          .then(function () { vtoast("已重命名"); loadSessions(); });
      });
      var arc = document.createElement("button");
      arc.className = "vsm-act vsm-act-danger"; arc.textContent = s.archived ? "—" : "归档";
      if (!s.archived) {
        arc.addEventListener("click", function () {
          codexApi("close", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ threadId: s.thread_id }) })
            .then(function () { vtoast("已归档"); loadSessions(); });
        });
      } else { arc.disabled = true; }
      row.appendChild(ren); row.appendChild(arc);
      box.appendChild(row);
    });
  }

  function loadSessions() {
    codexApi("list").then(function (d) {
      renderSessions(d && d.ok ? d.sessions : []);
    });
  }

  function openSessionManager() {
    var m = document.getElementById("vega-session-mgr");
    if (!m) {
      m = document.createElement("div");
      m.id = "vega-session-mgr";
      m.className = "vsm-modal";
      m.innerHTML =
        '<div class="vsm-card"><div class="vsm-head">codex 会话管理' +
        '<button class="vsm-close" id="vsm-x" aria-label="关闭"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>' +
        '<div class="vsm-list" id="vsm-list"><div class="vsm-empty vega-shimmer">加载中…</div></div></div>';
      document.body.appendChild(m);
      m.addEventListener("click", function (e) { if (e.target === m) m.style.display = "none"; });
      m.querySelector("#vsm-x").addEventListener("click", function () { m.style.display = "none"; });
    }
    m.style.display = "flex";
    // 重触发入场动画 (display 切换不会自动重跑 animation)
    m.classList.remove("vega-anim-open");
    void m.offsetWidth;
    m.classList.add("vega-anim-open");
    loadSessions();
  }

  // ── 组onboard TickC: codex 接入向导 (检测→登录扫码→就绪) ──
  function _esc(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function onboardApi(path, opts) {
    return codexApi("onboard/" + path, opts);
  }

  var _wizPoll = null;
  var WIZ_STEPS = [["detect", "检测"], ["login", "登录"], ["ready", "就绪"]];

  function wizStopPoll() {
    if (_wizPoll) {
      clearInterval(_wizPoll);
      _wizPoll = null;
    }
  }

  function wizRenderSteps(active) {
    var el = document.getElementById("vow-steps");
    if (!el) return;
    var hit = false;
    el.innerHTML = WIZ_STEPS.map(function (s, i) {
      var done = hit ? "" : (s[0] === active ? "" : " vow-step-done");
      if (s[0] === active) hit = true;
      var cls = "vow-step" + (s[0] === active ? " vow-step-active" : done);
      return '<div class="' + cls + '"><span class="vow-step-dot">' + (i + 1) + "</span>" + s[1] + "</div>";
    }).join('<span class="vow-step-sep"></span>');
  }

  function wizBody(html) {
    var b = document.getElementById("vow-body");
    if (b) b.innerHTML = html;
    return b;
  }

  function wizDetect() {
    wizRenderSteps("detect");
    wizBody('<div class="vow-loading vega-shimmer">正在探测 codex…</div>');
    onboardApi("detect").then(function (d) {
      if (d && d.found) {
        wizBody(
          '<div class="vow-row vow-ok-row"><svg class="vow-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>已找到 codex</div>' +
          '<div class="vow-kv"><span>路径</span><code>' + _esc(d.path) + "</code></div>" +
          '<div class="vow-kv"><span>版本</span><code>' + _esc(d.version) + "</code></div>" +
          '<button type="button" class="vega-panel-btn" id="vow-next">下一步：检测登录</button>'
        );
        var nx = document.getElementById("vow-next");
        if (nx) nx.addEventListener("click", wizCheckLogin);
      } else {
        var cands = ((d && d.candidates) || []).map(function (c) {
          return "<li>" + _esc(c.path) + (c.exists ? "" : ' <span class="vow-dim">(不存在)</span>') + "</li>";
        }).join("");
        wizBody(
          '<div class="vow-row vow-warn-row">未自动找到 codex，手动指定路径</div>' +
          '<div class="vow-hint">已探测的位置：</div><ul class="vow-cands">' + cands + "</ul>" +
          '<input class="vow-input" id="vow-custom" placeholder="如 /usr/local/bin/codex">' +
          '<button type="button" class="vega-panel-btn" id="vow-savepath">保存并重试</button>'
        );
        var sv = document.getElementById("vow-savepath");
        if (sv) sv.addEventListener("click", function () {
          var p = (document.getElementById("vow-custom").value || "").trim();
          if (!p) return;
          onboardApi("config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ codex_bin: p }) })
            .then(function (rr) {
              if (rr && rr.ok) wizDetect();
              else vtoast((rr && rr.error) || "路径无效");
            });
        });
      }
    });
  }

  function wizCheckLogin() {
    wizRenderSteps("login");
    wizBody('<div class="vow-loading vega-shimmer">检测登录态…</div>');
    onboardApi("status").then(function (d) {
      if (d && d.logged_in) wizReady("已登录（" + (d.method || "ChatGPT") + "）");
      else wizLogin();
    });
  }

  function wizLogin() {
    wizRenderSteps("login");
    wizBody('<div class="vow-loading vega-shimmer">正在发起设备登录…</div>');
    onboardApi("login/start", { method: "POST" }).then(function (d) {
      if (d && d.already_logged_in) { wizReady("已登录（" + (d.method || "ChatGPT") + "）"); return; }
      if (!d || !d.url || !d.code) {
        wizBody('<div class="vow-row vow-bad-row">发起登录失败：' + _esc((d && d.error) || "未知") + "</div>");
        return;
      }
      wizBody(
        '<div class="vow-login">' +
          '<div class="vow-qr" id="vow-qr"><div class="vow-loading vega-shimmer">生成二维码…</div></div>' +
          '<div class="vow-login-info">' +
            '<div class="vow-step-line"><span class="vow-num">1</span>手机扫码 或 打开链接</div>' +
            '<a class="vow-url" href="' + _esc(d.url) + '" target="_blank" rel="noopener">' + _esc(d.url) + "</a>" +
            '<div class="vow-step-line"><span class="vow-num">2</span>输入一次性码</div>' +
            '<div class="vow-code">' + _esc(d.code) + "</div>" +
            '<div class="vow-poll" id="vow-poll">等待授权…</div>' +
          "</div>" +
        "</div>" +
        '<button type="button" class="vega-panel-btn vega-panel-danger" id="vow-cancel">取消登录</button>'
      );
      fetch("/vega-admin/api/codex/onboard/qr?data=" + encodeURIComponent(d.url), { credentials: "include" })
        .then(function (r) { return r.text(); })
        .then(function (svg) {
          var q = document.getElementById("vow-qr");
          if (q && svg.indexOf("<svg") >= 0) q.innerHTML = svg;
        }).catch(function () {});
      var cx = document.getElementById("vow-cancel");
      if (cx) cx.addEventListener("click", function () {
        wizStopPoll();
        onboardApi("login/cancel", { method: "POST" });
        wizDetect();
      });
      _wizPoll = setInterval(function () {
        onboardApi("login/poll").then(function (p) {
          var pe = document.getElementById("vow-poll");
          if (!pe) return;
          if (p && p.status === "success") { wizStopPoll(); wizReady("登录成功"); }
          else if (p && p.status === "failed") {
            wizStopPoll();
            pe.className = "vow-poll vow-bad-row";
            pe.textContent = "登录失败：" + ((p.detail || "").slice(0, 60));
          } else { pe.textContent = "等待授权…（" + ((p && p.elapsed) || 0) + "s）"; }
        });
      }, 3000);
    });
  }

  function wizReady(msg) {
    wizStopPoll();
    wizRenderSteps("ready");
    wizBody(
      '<div class="vow-ready">' +
        '<svg class="vow-ready-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M8 12l3 3 5-6"/></svg>' +
        '<div class="vow-ready-title">codex 已就绪</div>' +
        '<div class="vow-hint">' + _esc(msg || "已接入并登录") + "，现在可在聊天里选 Codex 模型对话。</div>" +
        '<button type="button" class="vega-panel-btn" id="vow-done">完成</button>' +
      "</div>"
    );
    var dn = document.getElementById("vow-done");
    if (dn) dn.addEventListener("click", function () {
      var m = document.getElementById("vega-onboard-modal");
      if (m) m.style.display = "none";
    });
  }

  function openOnboardWizard() {
    var m = document.getElementById("vega-onboard-modal");
    if (!m) {
      m = document.createElement("div");
      m.id = "vega-onboard-modal";
      m.className = "vsm-modal vow-modal";
      m.innerHTML =
        '<div class="vsm-card vow-card"><div class="vsm-head">接入 codex' +
        '<button class="vsm-close" id="vow-x" aria-label="关闭"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>' +
        '<div class="vow-steps" id="vow-steps"></div>' +
        '<div class="vow-body" id="vow-body"></div></div>';
      document.body.appendChild(m);
      var _close = function () { wizStopPoll(); onboardApi("login/cancel", { method: "POST" }); m.style.display = "none"; };
      m.addEventListener("click", function (e) { if (e.target === m) _close(); });
      m.querySelector("#vow-x").addEventListener("click", _close);
    }
    m.style.display = "flex";
    m.classList.remove("vega-anim-open");
    void m.offsetWidth;
    m.classList.add("vega-anim-open");
    wizDetect();
  }

  function buildPanel() {
    var panel = document.createElement("div");
    panel.id = "vega-codex-panel";
    panel.className = "vega-panel";
    panel.style.display = "none";
    panel.innerHTML =
      '<div class="vega-panel-head">' +
      '<svg class="vega-hub-logo" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l8.66 5v10L12 22l-8.66-5V7L12 2zm0 2.31L5.34 8.15v7.7L12 19.69l6.66-3.84v-7.7L12 4.31z"/></svg>' +
      "Vega Chat 控制台</div>" +
      '<div class="vega-hub-sec">Codex 会话</div>' +
      '<div class="vega-panel-row"><span class="vega-panel-k">引擎</span><span class="vega-panel-v vega-shimmer" id="vp-status">加载中…</span></div>' +
      '<div class="vega-panel-row"><span class="vega-panel-k">当前会话</span><span class="vega-panel-v" id="vp-thread">—</span></div>' +
      '<div class="vega-panel-row vega-panel-clickable" id="vp-health-row" title="点击重新诊断"><span class="vega-panel-k">codex 健康</span><span class="vega-panel-v" id="vp-health">—</span></div>' +
      '<button type="button" class="vega-panel-btn" id="vp-interrupt">停止当前回答</button>' +
      '<button type="button" class="vega-panel-btn" id="vp-sessions">管理 codex 会话</button>' +
      '<button type="button" class="vega-panel-btn vega-panel-accent" id="vp-onboard">接入 codex（向导）</button>' +
      '<button type="button" class="vega-panel-btn vega-panel-danger" id="vp-close">关闭 Codex 会话</button>' +
      '<div class="vega-hub-sec">管理</div>' +
      '<a class="vega-panel-link" href="/admin-panel/" target="_blank" rel="noopener">用户 / 模型 / 配额 管理 (可选面板)</a>' +
      '<a class="vega-panel-link" href="/vega-admin/" target="_blank" rel="noopener">自建 Admin 后台</a>' +
      '<div class="vega-hub-sec">系统</div>' +
      '<div class="vega-panel-row"><span class="vega-panel-k">版本</span><span class="vega-panel-v" id="vp-ver">—</span></div>';

    panel.querySelector("#vp-interrupt").addEventListener("click", function () {
      codexApi("interrupt", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then(
        function (d) {
          vtoast(d && d.interrupted ? "已停止当前回答" : "无进行中的回答");
        }
      );
    });

    panel.querySelector("#vp-sessions").addEventListener("click", function () {
      openSessionManager();
    });

    panel.querySelector("#vp-onboard").addEventListener("click", function () {
      openOnboardWizard();
    });

    panel.querySelector("#vp-health-row").addEventListener("click", function () {
      refreshHealth(true);
    });

    var closeArmed = false;
    var closeBtn = panel.querySelector("#vp-close");
    closeBtn.addEventListener("click", function () {
      if (!closeArmed) {
        closeArmed = true;
        closeBtn.textContent = "确认关闭？再点一次";
        setTimeout(function () {
          closeArmed = false;
          closeBtn.textContent = "关闭 Codex 会话";
        }, 4000);
        return;
      }
      closeArmed = false;
      closeBtn.textContent = "关闭中…";
      codexApi("close", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then(
        function (d) {
          closeBtn.textContent = "关闭 Codex 会话";
          vtoast(d && d.ok ? "会话已关闭，下条消息起新会话" : "关闭失败");
          refreshPanel();
        }
      );
    });
    return panel;
  }

  function ensureGear() {
    try {
      if (!document.body) return;
      if (document.getElementById("vega-codex-gear")) return;
      isAdmin().then(function (ok) {
        if (!ok || document.getElementById("vega-codex-gear")) return;
        var gear = document.createElement("button");
        gear.id = "vega-codex-gear";
        gear.type = "button";
        gear.title = "Codex 会话管理";
        gear.innerHTML = GEAR;
        var panel = buildPanel();
        gear.addEventListener("click", function (e) {
          e.stopPropagation();
          var open = panel.style.display === "none";
          panel.style.display = open ? "block" : "none";
          if (open) {
            // 重触发开启动画 (强制 reflow 让 animation 每次都跑)
            panel.classList.remove("vega-anim-open");
            void panel.offsetWidth;
            panel.classList.add("vega-anim-open");
            refreshPanel();
          }
        });
        document.addEventListener("click", function (e) {
          if (
            panel.style.display !== "none" &&
            !panel.contains(e.target) &&
            e.target !== gear &&
            !gear.contains(e.target)
          )
            panel.style.display = "none";
        });
        document.body.appendChild(gear);
        document.body.appendChild(panel);
      });
    } catch (e) {}
  }

  // ────── 启动: MutationObserver 驱动折叠 + FAB 守护 ──────
  var scanTimer = null;
  function scheduleScan() {
    if (scanTimer) return;
    scanTimer = setTimeout(function () {
      scanTimer = null;
      scanFolds();
      scanChips();
    }, 150);
  }

  function start() {
    loadCss();
    scanFolds();
    scanChips();
    ensureBrand();
    ensureGear();
    try {
      var obs = new MutationObserver(scheduleScan);
      obs.observe(document.body, { childList: true, subtree: true });
    } catch (e) {}
    // 齿轮 + 品牌角标守护 (低频, 折叠/chip 靠 observer)
    setInterval(function () {
      ensureGear();
      ensureBrand();
    }, 4000);
  }

  if (document.body) start();
  else document.addEventListener("DOMContentLoaded", start);
})();
