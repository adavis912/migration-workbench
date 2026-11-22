"""Answers-only chat for the dashboard ("Ask the workbench").

Claude answers questions from the dashboard's own data, sent as one cached context block: Discovery inventory,
wave plan, pipeline and policy status, code scans, rules checks, architecture status and the migration rules.
It cannot take actions: there are no tools, and the system prompt tells it to point to the page button instead.
The API key stays on the server. Questions are capped per day (CHAT_DAILY_LIMIT); question text is never logged.
"""
import datetime as dt
import json
import os
import threading
import time

import archgen
import assessment
import discovery
import rulescheck

MODEL = os.environ.get("CHAT_MODEL", "claude-sonnet-5-5")
DAILY_LIMIT = int(os.environ.get("CHAT_DAILY_LIMIT", "50"))
STATE = os.environ.get("CHAT_STATE", "/var/lib/capstone-dashboard/chat")
MAX_TURNS, MAX_CHARS = 10, 2000
_lock = threading.Lock()
_ctx = {"key": None, "text": ""}

SYSTEM = """You are "Ask the workbench", the assistant inside a cloud-migration dashboard (OCI to AWS) for a fictional
company. Answer questions using ONLY the dashboard data provided in <dashboard_data>. Rules:
- Be brief and specific: a direct answer first, then at most a few bullets. Use app IDs (e.g. CLM-FRAU1) and exact numbers.
- Keep the whole answer under about 250 words; for many apps, give the top items per app and offer to go deeper on one.
- If the data does not contain the answer, say so plainly and say which page or action would produce it. Never guess.
- You cannot take actions. If asked to start a scan, draw an architecture, run a rules check, change a wave or
  push code, explain which button on which page does it (Assessment Phase: Start scan, Create architecture,
  Create rules report). Do not claim you did anything.
- End with one line "Sources:" naming the parts of the data you used, e.g. "Sources: Discovery, Wave plan, Rules check CLM-FRAU1".
- The user is looking at the page and filter given with their question; use it to resolve "this app" or "this wave".
- Plain Markdown only (short paragraphs, bullets, simple tables). No HTML."""


def _trim_apps(d):
    keep = ("id", "name", "bu", "type", "cots", "security", "pci", "tier", "rto", "deployment", "runtime", "jdk", "web",
            "messaging", "cache", "db_engine", "db_service", "cicd", "secrets", "freeze", "review", "cmdb_servers", "servers", "wave")
    return [dict({k: a.get(k) for k in keep}, findings=[f["t"] for f in a.get("findings", [])]) for a in d["apps"]]


def _trim_servers(d):
    return [{"host": s["host"].split(".")[0], "app": s["app"], "role": s["role"], "service": s["service"], "os": s["os"],
             "vcpu": s["vcpu"], "mem_gb": s["mem"], "storage_gb": s["storage"], "cpu_avg": s["cpu_avg"], "cpu_peak": s["cpu_peak"],
             "agent": s["agent"], "last_seen": s["last_seen"], "target": s["target"], "target_os": s["target_os"],
             "instance": s["instance"], "flags": [f["t"] for f in s["flags"]] + s.get("rule_flags", [])} for s in d["servers"]]


def build_context(live):
    """The dashboard data as compact JSON text. Rebuilt at most every 5 minutes (and then cached by Claude)."""
    key = int(time.time() // 300)
    if _ctx["key"] == key and _ctx["text"]:
        return _ctx["text"]
    d = discovery.build()
    try:
        waves = json.load(open(discovery.WAVE_PLAN))
    except (OSError, ValueError):
        waves = {}
    pipelines = []
    for a in ((live or {}).get("apps") or []):
        pipelines.append({k: a.get(k) for k in ("id", "name", "owner", "profile", "runtime", "wave", "hold", "policy", "fails")})
    pilot = ((live or {}).get("data") or {}).get("pipeline")
    scans = [{"job": s["job"], "app": s["scan"].get("app"), "state": s["state"], "started": s["status"].get("started"),
              "transformation": s["scan"].get("transformation"), "repo": s["scan"].get("repo"),
              "documents": len(s["documents"])} for s in assessment.scans()]
    rules = {}
    for job in rulescheck.status():
        md = rulescheck.read(job, "md")
        if md:
            rules[job] = md[:12000]
    try:
        mapping = open(rulescheck.MAPPING).read()[:10000]
    except OSError:
        mapping = ""
    data = {
        "as_of": d.get("as_of"), "source_note": d.get("source"),
        "wave_plan": waves, "applications": _trim_apps(d), "servers": _trim_servers(d), "databases": d["databases"],
        "dependencies": [{"from": e["src"], "to": e["dst"], "seen_in": e["seen_in"], "per_day": e["per_day"],
                          "via": [n["service"] for n in e["net"]], "cmdb": e.get("cmdb")} for e in d["deps"]],
        "pipelines_wave1": pipelines, "pilot_pipeline_CLM_FRAU1": pilot,
        "policy_rules": rulescheck.policies(), "mapping_rule_ids": rulescheck.MAP_RULES,
        "code_scans": scans, "scan_registry": {k: v["repo"] for k, v in assessment.registry().items()},
        "rules_checks_markdown": rules,
        "architecture_diagrams": {k: {"drawn": v.get("generated_at"), "renderer": v.get("renderer")} for k, v in archgen.status().items()},
    }
    text = ("<dashboard_data>\n" + json.dumps(data, separators=(",", ":"), default=str) + "\n</dashboard_data>\n"
            "<mapping_rules_yaml>\n" + mapping + "\n</mapping_rules_yaml>")
    _ctx.update(key=key, text=text)
    return text


def _usage_path():
    return os.path.join(STATE, "usage.json")


def usage():
    today = dt.date.today().isoformat()
    try:
        u = json.load(open(_usage_path()))
    except (OSError, ValueError):
        u = {}
    used = u.get("count", 0) if u.get("day") == today else 0
    return {"used": used, "limit": DAILY_LIMIT, "left": max(0, DAILY_LIMIT - used)}


def _count():
    today = dt.date.today().isoformat()
    os.makedirs(STATE, exist_ok=True)
    u = usage()
    tmp = _usage_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"day": today, "count": u["used"] + 1}, f)
    os.replace(tmp, _usage_path())


def _clean(messages):
    out = []
    for m in (messages or [])[-MAX_TURNS * 2:]:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            continue
        txt = str(m.get("content", ""))[:MAX_CHARS * 3 if m["role"] == "assistant" else MAX_CHARS].strip()
        if txt and out and out[-1]["role"] == m["role"]:      # e.g. a question whose answer failed: merge
            out[-1]["content"] += "\n\n" + txt
        elif txt:
            out.append({"role": m["role"], "content": txt})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def answer(messages, page, filt, live, api_key):
    """Returns (status, payload). payload has answer (Markdown), html, usage."""
    msgs = _clean(messages)
    if not msgs or msgs[-1]["role"] != "user":
        return 400, {"error": "Ask a question first."}
    with _lock:
        if usage()["left"] <= 0:
            return 429, {"error": f"Today's limit of {DAILY_LIMIT} questions is used up. It resets at midnight.", "usage": usage()}
        _count()
    f = filt if isinstance(filt, dict) else {}
    where = (f"[The user is on the {str(page)[:40]} page with filter wave={str(f.get('wave', 'all'))[:20]}, "
             f"app={str(f.get('app', 'all'))[:40]}.]\n")
    msgs[-1] = {"role": "user", "content": where + msgs[-1]["content"]}
    import anthropic
    client = anthropic.Anthropic(api_key=api_key, max_retries=2)
    msg = client.messages.create(
        model=MODEL, max_tokens=4000,
        system=[{"type": "text", "text": SYSTEM},
                {"type": "text", "text": build_context(live), "cache_control": {"type": "ephemeral"}}],
        messages=msgs)
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip() or "I could not produce an answer."
    if getattr(msg, "stop_reason", None) == "max_tokens":
        text += "\n\n*(Answer cut short. Ask about one app at a time for the rest.)*"
    return 200, {"answer": text, "html": assessment.markdown(text), "usage": usage()}
