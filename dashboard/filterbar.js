/* Shared Wave / App filter for every dashboard page.
 * The server injects window.__FILTER__ = {apps:[{id,name,bu,wave}], waves:[{key,label,count}]} and the bar markup.
 * State lives in the URL (?wave=&app=) so a link is shareable, and in localStorage so moving between pages, or
 * between open tabs, keeps the same filter. Pages read WBFilter.get()/matches() and subscribe with WBFilter.on(). */
(function () {
  var C = window.__FILTER__ || { apps: [], waves: [] };
  var KEY = "wb_filter", listeners = [];
  // One color per wave, used for the dot in the wave filter, the key under it and the wave chips on Discovery.
  var WAVE_COLORS = { "1": "#4f46e5", "2": "#0ea5e9", "3": "#10b981", "4": "#f59e0b", "5": "#e11d48", "6": "#8b5cf6", "7": "#64748b",
                      blocked: "#b42323", none: "#94a3b8" };
  var waveColor = function (k) { return WAVE_COLORS[String(k)] || (/^\d+$/.test(String(k)) ? "#64748b" : WAVE_COLORS.none); };
  var byId = {};
  C.apps.forEach(function (a) { byId[a.id] = a; });
  var waveKey = function (a) { return a && a.wave != null ? String(a.wave) : "none"; };
  var waveKeys = C.waves.map(function (w) { return w.key; });

  function norm(s) {
    s = s || {};
    var wave = waveKeys.indexOf(String(s.wave)) >= 0 ? String(s.wave) : "all";
    var app = byId[s.app] ? s.app : "all";
    if (app !== "all" && wave !== "all" && waveKey(byId[app]) !== wave) app = "all";
    return { wave: wave, app: app };
  }
  function read() {
    var q = new URLSearchParams(location.search);
    if (q.has("wave") || q.has("app")) return norm({ wave: q.get("wave"), app: q.get("app") });
    try { return norm(JSON.parse(localStorage.getItem(KEY) || "{}")); } catch (e) { return norm({}); }
  }
  var state = read();

  function query(s) {
    var p = new URLSearchParams();
    if (s.wave !== "all") p.set("wave", s.wave);
    if (s.app !== "all") p.set("app", s.app);
    var t = p.toString();
    return t ? "?" + t : "";
  }
  function persist() {
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {}
    try { history.replaceState(null, "", location.pathname + query(state) + location.hash); } catch (e) {}
    document.querySelectorAll("a[data-filter-link]").forEach(function (a) {
      a.setAttribute("href", a.getAttribute("data-filter-link") + query(state));
    });
  }

  var waveSel = document.getElementById("fWave"), appSel = document.getElementById("fApp"), clr = document.getElementById("fClear");
  function fillApps() {
    if (!appSel) return;
    var list = C.apps.filter(function (a) { return state.wave === "all" || waveKey(a) === state.wave; });
    var groups = {};
    list.forEach(function (a) { (groups[a.bu] = groups[a.bu] || []).push(a); });
    var esc = function (s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };
    var h = '<option value="all">All apps (' + list.length + ")</option>";
    Object.keys(groups).sort().forEach(function (bu) {
      h += '<optgroup label="' + esc(bu) + '">' + groups[bu].sort(function (x, y) { return x.id.localeCompare(y.id); })
        .map(function (a) { return '<option value="' + esc(a.id) + '">' + esc(a.id) + " · " + esc(a.name) + "</option>"; }).join("") + "</optgroup>";
    });
    appSel.innerHTML = h;
    appSel.value = state.app;
  }
  function paint() {
    if (waveSel) waveSel.value = state.wave;
    var dot = document.getElementById("fWaveDot");
    if (dot) dot.style.background = state.wave === "all" ? "transparent" : waveColor(state.wave);
    fillApps();
    if (clr) clr.hidden = state.wave === "all" && state.app === "all";
  }
  function set(next, quiet) {
    var s = norm({ wave: next.wave !== undefined ? next.wave : state.wave, app: next.app !== undefined ? next.app : state.app });
    var changed = s.wave !== state.wave || s.app !== state.app;
    state = s;
    persist();
    paint();
    if (changed && !quiet) listeners.forEach(function (fn) { try { fn(state); } catch (e) { console.error(e); } });
  }

  if (waveSel) {
    waveSel.innerHTML = '<option value="all">All waves</option>' + C.waves.map(function (w) {
      return '<option value="' + w.key + '">' + w.label + " (" + w.count + ")</option>";
    }).join("");
    waveSel.addEventListener("change", function () { set({ wave: waveSel.value, app: "all" }); });  // a new wave shows all of its apps
  }
  if (appSel) appSel.addEventListener("change", function () { set({ app: appSel.value }); });
  var legend = document.getElementById("fLegend");
  if (legend) {
    legend.innerHTML = "<span>Wave colors:</span>" + C.waves.filter(function (w) { return /^\d+$/.test(w.key); }).map(function (w) {
      return '<span><i class="wdot" style="background:' + waveColor(w.key) + '"></i>Wave ' + w.key + "</span>";
    }).join("");
  }
  if (clr) clr.addEventListener("click", function () { set({ wave: "all", app: "all" }); });
  // Another open tab changed the filter: follow it.
  window.addEventListener("storage", function (e) {
    if (e.key !== KEY) return;
    try { set(JSON.parse(e.newValue || "{}")); } catch (err) {}
  });

  window.WBFilter = {
    get: function () { return { wave: state.wave, app: state.app }; },
    set: set,
    on: function (fn) { listeners.push(fn); },
    app: function (id) { return byId[id] || null; },
    waveColor: waveColor,
    waveOf: function (id) { return waveKey(byId[id]); },
    inWave: function (id) { return state.wave === "all" || (byId[id] ? waveKey(byId[id]) === state.wave : false); },
    matches: function (id) {
      if (state.app !== "all") return id === state.app;
      return state.wave === "all" || (byId[id] ? waveKey(byId[id]) === state.wave : false);
    },
    label: function () {
      if (state.app !== "all") return state.app;
      if (state.wave === "all") return "all applications";
      var w = C.waves.filter(function (x) { return x.key === state.wave; })[0];
      return w ? w.label : "this wave";
    }
  };
  persist();
  paint();
})();
