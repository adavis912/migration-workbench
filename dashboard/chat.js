/* "Ask the workbench": a floating Ask button and a right-hand chat panel on every dashboard page.
 * Answers only. Messages go to POST /chat on this server, which asks Claude using the dashboard's data.
 * Answers come back as server-rendered, escaped HTML. The conversation is kept for this browser tab only. */
(function () {
  var KEY = "wb_chat", PAGE = window.__PAGE__ || "pipelines";
  var PAGE_NAME = { pipelines: "App Pipelines", discovery: "Discovery", assessment: "Assessment Phase" }[PAGE] || PAGE;
  var SUGGEST = {
    pipelines: ["Which Wave 1 apps are held, and why?", "What does ALB-TLS mean and how do I fix it?", "Which apps have open policy findings?"],
    discovery: ["Which servers run an end-of-life OS?", "What does this app depend on?", "Which databases need a target decision?"],
    assessment: ["Summarize the rules check for this app.", "What blocks this app from its wave?", "Which apps have been scanned so far?"]
  }[PAGE] || ["What is in Wave 1?"];
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };
  var hist = [];
  try { hist = JSON.parse(sessionStorage.getItem(KEY) || "[]"); if (!Array.isArray(hist)) hist = []; } catch (e) { hist = []; }
  var save = function () { try { sessionStorage.setItem(KEY, JSON.stringify(hist.slice(-20))); } catch (e) {} };

  var btn = document.createElement("button");
  btn.type = "button"; btn.className = "wbc-launch"; btn.setAttribute("aria-haspopup", "dialog"); btn.setAttribute("aria-controls", "wbcPanel");
  btn.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v11H8l-4 4z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg><span>Ask</span>';
  var panel = document.createElement("aside");
  panel.id = "wbcPanel"; panel.className = "wbc-panel"; panel.hidden = true;
  panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", "Ask the workbench");
  panel.innerHTML =
    '<div class="wbc-head"><div><div class="wbc-title">Ask the workbench</div><div class="wbc-ctx" id="wbcCtx"></div></div>' +
    '<div class="wbc-hbtns"><button type="button" class="wbc-ghost" id="wbcNew">New chat</button>' +
    '<button type="button" class="wbc-x" id="wbcClose" aria-label="Close">×</button></div></div>' +
    '<div class="wbc-log" id="wbcLog" aria-live="polite"></div>' +
    '<form class="wbc-form" id="wbcForm"><textarea id="wbcIn" rows="2" maxlength="2000" placeholder="Ask about apps, waves, scans or rules…" aria-label="Your question"></textarea>' +
    '<button type="submit" class="wbc-send" id="wbcSend">Send</button></form>' +
    '<div class="wbc-foot"><span>Answers only, from this dashboard\'s data. Claude can be wrong; check the source page.</span><span id="wbcUse"></span></div>';
  document.body.appendChild(btn); document.body.appendChild(panel);
  var $ = function (id) { return document.getElementById(id); };

  function ctx() {
    var f = window.WBFilter ? window.WBFilter.get() : { wave: "all", app: "all" };
    return { f: f, label: PAGE_NAME + " · " + (window.WBFilter ? window.WBFilter.label() : "all applications") };
  }
  function render() {
    $("wbcCtx").textContent = ctx().label;
    var log = $("wbcLog");
    if (!hist.length) {
      log.innerHTML = '<div class="wbc-empty"><p>Ask anything about the migration: apps, waves, servers, pipelines, scans and rules checks. Answers use what this dashboard knows and say where it came from.</p>' +
        '<div class="wbc-sugg">' + SUGGEST.map(function (s) { return '<button type="button" class="wbc-chip">' + esc(s) + "</button>"; }).join("") + "</div></div>";
    } else {
      log.innerHTML = hist.map(function (m) {
        return m.role === "user" ? '<div class="wbc-msg wbc-me">' + esc(m.content) + "</div>"
          : '<div class="wbc-msg wbc-bot' + (m.error ? " wbc-err" : "") + '">' + (m.html || esc(m.content)) + "</div>";
      }).join("");
    }
    log.scrollTop = log.scrollHeight;
  }
  function usage(u) { if (u && u.limit) $("wbcUse").textContent = u.left + " of " + u.limit + " left today"; }
  function open() {
    panel.hidden = false; btn.setAttribute("aria-expanded", "true"); document.body.classList.add("wbc-open");
    render(); $("wbcIn").focus();
    fetch("/chat/status", { credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : null; }).then(usage).catch(function () {});
  }
  function close() { panel.hidden = true; btn.setAttribute("aria-expanded", "false"); document.body.classList.remove("wbc-open"); btn.focus(); }
  btn.addEventListener("click", function () { panel.hidden ? open() : close(); });
  $("wbcClose").addEventListener("click", close);
  $("wbcNew").addEventListener("click", function () { hist = []; save(); render(); $("wbcIn").focus(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !panel.hidden) close(); });
  $("wbcLog").addEventListener("click", function (e) { var c = e.target.closest(".wbc-chip"); if (c) ask(c.textContent); });
  $("wbcIn").addEventListener("keydown", function (e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("wbcForm").requestSubmit(); } });
  $("wbcForm").addEventListener("submit", function (e) { e.preventDefault(); ask($("wbcIn").value); });
  if (window.WBFilter) window.WBFilter.on(function () { if (!panel.hidden) $("wbcCtx").textContent = ctx().label; });

  var busy = false;
  function ask(q) {
    q = String(q || "").trim();
    if (!q || busy) return;
    busy = true; $("wbcSend").disabled = true; $("wbcIn").value = "";
    hist.push({ role: "user", content: q }); render();
    var log = $("wbcLog"); var wait = document.createElement("div");
    wait.className = "wbc-msg wbc-bot wbc-wait"; wait.innerHTML = "<span></span><span></span><span></span>"; log.appendChild(wait); log.scrollTop = log.scrollHeight;
    var c = ctx();
    var send = hist.filter(function (m) { return !m.error; }).map(function (m) { return { role: m.role, content: m.content }; });
    fetch("/chat", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: send, page: PAGE_NAME, filter: c.f }) })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (x) {
        if (x.ok) hist.push({ role: "assistant", content: x.j.answer, html: x.j.html });
        else hist.push({ role: "assistant", content: x.j.error || "Something went wrong. Try again.", error: true });
        usage(x.j.usage);
      })
      .catch(function () { hist.push({ role: "assistant", content: "Could not reach the dashboard. Check your connection and try again.", error: true }); })
      .then(function () { busy = false; $("wbcSend").disabled = false; save(); render(); $("wbcIn").focus(); });
  }
})();
