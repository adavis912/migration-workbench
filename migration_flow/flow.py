"""Migration Orchestrator as a CrewAI Flow (Checkpoint 5.1): build stage for one approved wave.

    load_wave  -> read the recommended wave plan, fetch each app from the inventory MCP server,
                  compute its target spec, open a migration record per app (plan_store)
    gate       -> HUMAN GATE 1a: the migration lead approves the wave (approval tied to the plan's hash)
    owners     -> HUMAN GATE 1b: each application owner approves their app's target spec (tied to the
                  spec's hash); a move group is built only when every owner in it has approved
    build      -> fan out per approved app (max 5 in parallel): Builder writes Terraform + GitLab CI and fixes
                  its own `terraform validate` errors; Reviewer runs policy checks + a Claude review;
                  a rejected change set goes back to the Builder (max 2 review cycles)
    validate   -> Validation agent (dry-run: records planned checks, runs nothing)
    decide     -> move-group integrity, readiness rules, go/no-go summary; HUMAN GATE 2 (cutover) needs
                  the owner's and the lead's approval per app, and is never executed in dry-run

Stage order is fixed in code; Claude writes code, reviews it and drafts the summary, but never
decides whether review happens or moves an app's stage.

Usage (from this folder):
    python flow.py run     --wave 1 --outputs ../loader/tf_outputs.json --plan ../wave_planner/out/wave_plan.json
    python flow.py approve --wave 1 --approver "Your Name" --plan ../wave_planner/out/wave_plan.json
    python flow.py approve-app --wave 1 --app CLM-FRAU1 --approver "Owner Name" [--gate build|cutover] [--reject --reason "..."]
    python flow.py status  --wave 1
"""
import argparse, asyncio, concurrent.futures as cf, datetime as dt, hashlib, json, os, sys, threading
from typing import Any

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from pydantic import BaseModel, PrivateAttr

from agents import Builder, Reviewer, TerraformRunner, Validation
from contracts import Message, Stage, now
from plan_store import PlanStore
from target_spec import build_spec

MAX_PARALLEL, MAX_REVIEW_CYCLES, TASK_TIMEOUT_S = 5, 2, 1800
PROGRAM_START, WAVE_DAYS = dt.date(2026, 12, 7), 14
BLOCKS_CUTOVER = {"database_target_decision_needed": "database target (Oracle Database@AWS vs RDS for Oracle) needs a human decision",
                  "security_review_required_not_complete": "security review not complete"}
CUTOVER_CONDITIONS = {"oracle_database_at_aws_handoff": "Oracle Database@AWS must be provisioned by the database workstream first",
                      "vendor_must_confirm_os": "vendor must confirm supported OS",
                      "end_of_life_os": "end-of-life OS carried over (vendor must confirm; plan the upgrade)",
                      "no_public_ami_import_with_mgn": "server image must be imported with AWS Application Migration Service",
                      "stale_record_over_90_days": "inventory record is stale - owner to re-confirm",
                      "plaintext_secret_move_to_secrets_manager": "move plaintext secret into Secrets Manager before cutover"}


def spec_hash(spec):
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


def owner_approval_path(work, wave, app, gate):
    return os.path.join(work, "approvals", "owners", f"wave-{wave}", f"{app}.{gate}.json")


def owner_approval(work, wave, app, gate, spec):
    """Valid owner decision for this app and gate, or None. A decision on an older spec does not count."""
    p = owner_approval_path(work, wave, app, gate)
    if not os.path.exists(p): return None
    a = json.load(open(p))
    if a.get("spec_sha256") != spec_hash(spec): return None
    return a


def plan_hash(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def wave_dates(n):
    s = PROGRAM_START + dt.timedelta(days=(n - 1) * WAVE_DAYS)
    return s, s + dt.timedelta(days=WAVE_DAYS - 1)


async def fetch_inventory(params, app_ids=None):
    from mcp import Client
    async with Client(params) as c:
        async def call(name, args):
            r = await c.call_tool(name, args)
            if r.is_error: raise RuntimeError(r.content[0].text)
            return json.loads(r.content[0].text)
        mg = await call("get_move_groups", {})
        groups = {g["group_id"]: g for g in mg["move_groups"]}
        apps = {}
        if app_ids is None: app_ids = [a for g in groups.values() for a in g["apps"]]
        for a in app_ids: apps[a] = await call("get_app", {"app_id": a})
    return groups, apps


class WaveState(BaseModel):
    wave: int = 1
    plan_path: str = ""
    plan_sha256: str = ""
    groups: list[str] = []
    apps: list[str] = []
    app_group: dict[str, str] = {}
    approved_by: str = ""
    outcome: str = ""


def make_flow_class():
    """Built lazily so the helpers import without CrewAI installed."""
    from crewai.flow.flow import Flow, listen, router, start

    class _MigrationFlow(Flow[WaveState]):
        _rt: Any = PrivateAttr(default=None)     # runtime deps: store, agents, inventory fetcher, llm, paths

        # ---------------------------------------------------------------- load
        @start()
        def load_wave(self):
            rt, st = self._rt, self.state
            plan = json.load(open(st.plan_path))
            st.plan_sha256 = plan_hash(st.plan_path)
            waves = plan["plans"][0]["waves"]
            if not 1 <= st.wave <= len(waves): raise SystemExit(f"wave {st.wave} not in plan (1..{len(waves)})")
            st.groups = list(waves[st.wave - 1])
            groups, _ = rt["fetch"]([])
            st.app_group = {a: g for g in st.groups for a in groups[g]["apps"]}
            st.apps = sorted(st.app_group)
            _, apps = rt["fetch"](st.apps)
            for a in st.apps:
                spec = build_spec(apps[a])
                rt["store"].create(a, {"wave": f"wave-{st.wave}", "move_group": st.app_group[a], "target_spec": spec,
                                       "owner": apps[a]["profile"].get("owner", "unknown"),
                                       "inventory_ref": f"inventory:get_app:{a}@{apps[a]['profile'].get('last_updated', '')}"})
                if "owner" not in rt["store"].get(a):        # records created before owner gates existed
                    rt["store"].write("orchestrator", a, {"owner": apps[a]["profile"].get("owner", "unknown")})
            rt["log"](f"Wave {st.wave}: {len(st.groups)} move groups, {len(st.apps)} apps: {', '.join(st.apps)}")

        # ---------------------------------------------------------------- human gate 1
        @router(load_wave)
        def gate(self):
            rt, st = self._rt, self.state
            ap = approval_path(rt["work"], st.wave)
            if os.path.exists(ap):
                a = json.load(open(ap))
                if a["plan_sha256"] == st.plan_sha256 and sorted(a["groups"]) == sorted(st.groups):
                    st.approved_by = f"{a['approver']} at {a['approved_at']}"
                    rt["log"](f"Gate 1: wave {st.wave} approved by {st.approved_by}")
                    return "approved"
                rt["log"]("Gate 1: an approval exists but the wave plan changed since - re-approval required")
            return "awaiting_approval"

        @listen("awaiting_approval")
        def request_approval(self):
            rt, st = self._rt, self.state
            path = os.path.join(rt["work"], f"approval_request_wave-{st.wave}.md")
            open(path, "w").write(approval_request(rt["store"], st))
            st.outcome = "awaiting_approval"
            rt["log"](f"Gate 1: waiting for the migration lead. Review {path}, then run:\n"
                      f"  python flow.py approve --wave {st.wave} --approver \"<name>\" --plan {st.plan_path}")
            return st.outcome

        # ---------------------------------------------------------------- build + review (parallel)
        @listen("approved")
        def build(self):
            rt, st = self._rt, self.state
            store = rt["store"]
            # ---- HUMAN GATE 1b: application owners (per app, per move group)
            ok = {}
            for a in st.apps:
                rec = store.get(a)
                appr = owner_approval(rt["work"], st.wave, a, "build", rec["target_spec"])
                ok[a] = bool(appr and appr["decision"] == "approve")
                if appr:
                    send(rt, f"wave-{st.wave}:{a}:owner-build", "human", "orchestrator", "approval", st.wave, a,
                         {"gate": "build", "decision": appr["decision"], "owner": appr["owner"], "approver": appr["approver"],
                          "delegated": appr["delegated"], "reason": appr.get("reason", "")},
                         [f"approval:{os.path.basename(owner_approval_path(rt['work'], st.wave, a, 'build'))}@{appr['spec_sha256'][:16]}"],
                         status=appr["decision"])
            group_ok = {g: all(ok[a] for a in st.apps if st.app_group[a] == g) for g in st.groups}
            for a in st.apps:
                rec = store.get(a)
                if group_ok[st.app_group[a]]:
                    if rec["stage"] == Stage.held.value and str(rec.get("hold_reason", "")).startswith("awaiting owner"):
                        store.write("orchestrator", a, {"stage": Stage.planned.value, "hold_reason": ""})
                elif rec["stage"] == Stage.planned.value or (rec["stage"] == Stage.held.value and str(rec.get("hold_reason", "")).startswith("awaiting owner")):
                    why = (f"awaiting owner approval (build) from {rec['owner']}" if not ok[a] else
                           f"awaiting owner approval of move-group partner(s): {', '.join(m for m in st.apps if st.app_group[m] == st.app_group[a] and not ok[m])}")
                    if rec.get("hold_reason") != why or rec["stage"] != Stage.held.value:
                        store.write("orchestrator", a, {"stage": Stage.held.value, "hold_reason": why})
            path = os.path.join(rt["work"], f"owner_approval_requests_wave-{st.wave}.md")
            open(path, "w").write(owner_requests(store, st, rt["work"]))
            waiting = [a for a in st.apps if not ok[a]]
            rt["log"](f"Gate 1b: {len(st.apps) - len(waiting)} of {len(st.apps)} app owners approved"
                      + (f"; waiting on {', '.join(waiting)} (requests: {path})" if waiting else ""))
            todo = [a for a in st.apps if store.get(a)["stage"] in (Stage.planned.value, Stage.review_rejected.value)]
            done = [a for a in st.apps if a not in todo]
            if done: rt["log"](f"Resuming: {len(done)} app(s) already past build ({', '.join(done)})")
            with cf.ThreadPoolExecutor(max_workers=MAX_PARALLEL) as ex:
                futs = {ex.submit(build_one, rt, st.wave, a): a for a in todo}
                for f in cf.as_completed(futs, timeout=TASK_TIMEOUT_S * MAX_REVIEW_CYCLES):
                    a = futs[f]
                    try:
                        f.result()
                    except Exception as e:      # a failed worker stops only its own app
                        rec = rt["store"].get(a)
                        rt["store"].write("orchestrator", a, {"stage": Stage.blocked.value if Stage.blocked in _next(rec) else Stage.held.value,
                                                             "hold_reason": f"worker error: {str(e)[:300]}"})
                        rt["log"](f"  [{a}] worker failed: {e}")
            return "built"

        # ---------------------------------------------------------------- validation
        @listen(build)
        def validate(self, _):
            rt, st = self._rt, self.state
            for a in st.apps:
                rec = rt["store"].get(a)
                if rec["stage"] != Stage.reviewed.value: continue
                files = {f: open(os.path.join(rec["build"]["workdir"], f)).read() for f in rec["build"]["file_hashes"]}
                tid = f"wave-{st.wave}:{a}:validate"
                send(rt, tid, "orchestrator", "validation", "task", st.wave, a, {"dry_run": rt["dry_run"]})
                res = rt["validation"].run(rec["target_spec"], files, dry_run=rt["dry_run"])
                send(rt, tid, "validation", "orchestrator", "result", st.wave, a, res, [f"validation:{res['status']}"], status=res["status"])
                rt["store"].write("validation", a, {"validation": res})
                rt["store"].write("orchestrator", a, {"stage": Stage.validated.value})
            return "validated"

        # ---------------------------------------------------------------- join and decide
        @listen(validate)
        def decide(self, _):
            rt, st = self._rt, self.state
            recs = {a: rt["store"].get(a) for a in st.apps}
            reasons = {}
            for a, r in recs.items():
                why = []
                if r["stage"] not in (Stage.validated.value, Stage.ready.value): why.append(f"stage {r['stage']}" + (f" ({r.get('hold_reason')})" if r.get("hold_reason") else ""))
                why += [BLOCKS_CUTOVER[f] for f in r["target_spec"]["flags"] if f in BLOCKS_CUTOVER]
                ob = owner_approval(rt["work"], st.wave, a, "build", r["target_spec"])
                if not (ob and ob["decision"] == "approve") and not str(r.get("hold_reason", "")).startswith("awaiting owner"):
                    why.append(f"awaiting owner approval (build) from {r['owner']}")
                reasons[a] = why
            for a, r in recs.items():         # move-group integrity: one failure holds the whole group
                if not reasons[a]:
                    mates = [m for m in st.apps if st.app_group[m] == st.app_group[a] and m != a and reasons[m]]
                    if mates: reasons[a] = [f"move group {st.app_group[a]} partner(s) not ready: {', '.join(mates)}"]
            for a, r in recs.items():
                if reasons[a]:
                    if r["stage"] not in (Stage.held.value, Stage.blocked.value):
                        rt["store"].write("orchestrator", a, {"stage": Stage.held.value, "hold_reason": "; ".join(reasons[a])})
                elif r["stage"] == Stage.validated.value:
                    rt["store"].write("orchestrator", a, {"stage": Stage.ready.value})
            path = os.path.join(rt["work"], f"go_no_go_wave-{st.wave}.md")
            open(path, "w").write(go_no_go(rt, st))
            st.outcome = "go_no_go_ready"
            rt["log"](f"Go/no-go summary: {path}. Gate 2 (cutover) needs a person; not executed in dry-run.")
            return st.outcome

    return _MigrationFlow


def _next(rec):
    from contracts import ALLOWED_TRANSITIONS
    return ALLOWED_TRANSITIONS[Stage(rec["stage"])]


def send(rt, task_id, frm, to, typ, wave, app, payload, evidence=(), status="pending", attempt=1):
    msg = Message(task_id=task_id, **{"from": frm}, to=to, type=typ, wave_id=f"wave-{wave}", app_id=app,
                  payload=payload, evidence=list(evidence), status=status, attempt=attempt,
                  deadline=now() + dt.timedelta(seconds=TASK_TIMEOUT_S))
    return rt["store"].log_message(msg)


def build_one(rt, wave, a):
    """Builder -> Reviewer loop for one app, driven by the Orchestrator (all stage changes are its own)."""
    store, log = rt["store"], rt["log"]
    feedback = None
    for cycle in range(1, MAX_REVIEW_CYCLES + 1):
        rec = store.get(a)
        spec = rec["target_spec"]
        store.write("orchestrator", a, {"stage": Stage.building.value})
        tid = f"wave-{wave}:{a}:build:{cycle}"
        send(rt, tid, "orchestrator", "builder", "task", wave, a, {"spec_ref": f"plan_store:{a}.target_spec", "review_feedback": bool(feedback)}, attempt=cycle)
        b = rt["builder"].run(spec, f"wave-{wave}", review_feedback=feedback, log=log)
        ev = [f"file:{f}@{h}" for f, h in b["file_hashes"].items()] + [f"tf-validate:attempt-{x['attempt']}:{x['validate_ok']}" for x in b["attempts"]]
        build_rec = {k: b[k] for k in ("workdir", "file_hashes", "validate_ok", "attempts", "summary", "assumptions")}
        if b["validate_ok"] is False:
            send(rt, tid, "builder", "orchestrator", "blocked", wave, a, {"reason": "terraform validate failing after 3 attempts"}, ev, status="blocked", attempt=cycle)
            store.write("builder", a, {"build": build_rec})
            store.write("orchestrator", a, {"stage": Stage.blocked.value, "hold_reason": "terraform validate still failing after 3 builder attempts"})
            return
        send(rt, tid, "builder", "orchestrator", "result", wave, a,
             {"validate_ok": b["validate_ok"], "summary": b["summary"][:500]}, ev, status="built", attempt=cycle)
        store.write("builder", a, {"build": build_rec})
        store.write("orchestrator", a, {"stage": Stage.built.value})
        store.write("orchestrator", a, {"stage": Stage.in_review.value})
        rtid = f"wave-{wave}:{a}:review:{cycle}"
        send(rt, rtid, "orchestrator", "reviewer", "review_request", wave, a, {"files": b["file_hashes"]}, ev, attempt=cycle)
        v = rt["reviewer"].run(spec, b["files"], log=log)
        send(rt, rtid, "reviewer", "orchestrator", "review_verdict", wave, a,
             {"verdict": v["verdict"], "policy_failures": v["policy_failures"][:20],
              "high_findings": [f["issue"][:200] for f in v["findings"] if f["severity"] == "high"]},
             [f"policy:{p}" for p in v["policy_passed"]] or ["policy:none-passed"], status=v["verdict"], attempt=cycle)
        store.write("reviewer", a, {"review": {**v, "cycle": cycle}})
        if v["verdict"] == "approve":
            store.write("orchestrator", a, {"stage": Stage.reviewed.value})
            return
        store.write("orchestrator", a, {"stage": Stage.review_rejected.value})
        feedback = {"items": v["policy_failures"] + [f"[{f['severity']}] {f['file']}: {f['issue']} -> {f['fix']}" for f in v["findings"] if f["severity"] == "high"],
                    "files": b["files"]}
    store.write("orchestrator", a, {"stage": Stage.blocked.value,
                                    "hold_reason": f"Reviewer still requesting changes after {MAX_REVIEW_CYCLES} cycles"})


# --------------------------------------------------------------------------------------------- reports
def approval_path(work, wave):
    return os.path.join(work, "approvals", f"wave-{wave}.json")


def approval_request(store, st):
    s, e = wave_dates(st.wave)
    lines = [f"# Approval request: wave {st.wave} ({s} to {e})", "",
             f"Wave plan: `{st.plan_path}` (sha256 `{st.plan_sha256[:16]}`). Nothing is built before approval.", "",
             "| App | Move group | Servers to build | Databases | Flags |", "|---|---|---|---|---|"]
    for a in st.apps:
        sp = store.get(a)["target_spec"]
        srv = ", ".join(f"{x['server_id']} {x['role']} -> {x['target_os']}" for x in sp["servers"] if x["build"] == "ec2")
        dbs = ", ".join(f"{d['db_id']} -> {d['target']}" for d in sp["databases"]) or "-"
        lines.append(f"| {a} | {st.app_group[a]} | {srv} | {dbs} | {', '.join(sp['flags']) or '-'} |")
    return "\n".join(lines) + "\n"


def owner_requests(store, st, work):
    s, e = wave_dates(st.wave)
    out = [f"# Application owner approvals: wave {st.wave} ({s} to {e})", "",
           "Each owner approves their application's target spec before any code is built (gate 1b) and again "
           "before cutover (gate 2). An approval is tied to the spec's hash: if the spec changes, it must be re-approved. "
           "A move group is built only when every owner in it has approved.", ""]
    for a in st.apps:
        r = store.get(a); sp = r["target_spec"]
        appr = owner_approval(work, st.wave, a, "build", sp)
        status = f"{appr['decision']} by {appr['approver']}" + (" (delegated)" if appr["delegated"] else "") if appr else "PENDING"
        out += [f"## {a} - owner: {r['owner']} - build approval: {status}", "",
                *[f"- {x['server_id']} ({x['role']}): {x['target']} on {x['target_os']}, {x.get('instance_type', '')}" for x in sp["servers"] if x["build"] == "ec2"],
                *[f"- {d['db_id']} ({d['engine']} {d['version']}): {d['target']}" for d in sp["databases"]],
                *([f"- cache: {sp['cache']}"] if sp["cache"] else []), *([f"- messaging: {sp['messaging']}"] if sp["messaging"] else []),
                *[f"- excluded: {x['server_id']} ({x['reason']})" for x in sp["excluded_servers"]],
                f"- flags: {', '.join(sp['flags']) or 'none'}", "",
                f"`python flow.py approve-app --wave {st.wave} --app {a} --approver \"{r['owner']}\"`  "
                f"(reject: add `--reject --reason \"...\"`)", ""]
    return "\n".join(out) + "\n"


def go_no_go(rt, st):
    store = rt["store"]
    s, e = wave_dates(st.wave)
    recs = {a: store.get(a) for a in st.apps}
    ready = [a for a in st.apps if recs[a]["stage"] == Stage.ready.value]
    held = [a for a in st.apps if a not in ready]
    rows = []
    for a in st.apps:
        r = recs[a]
        rv = r.get("review", {})
        b = r.get("build", {})
        conds = [CUTOVER_CONDITIONS[f] for f in r["target_spec"]["flags"] if f in CUTOVER_CONDITIONS]
        ob = owner_approval(rt["work"], st.wave, a, "build", r["target_spec"])
        oc = owner_approval(rt["work"], st.wave, a, "cutover", r["target_spec"])
        fmt = lambda x: "pending" if not x else x["decision"] + (" (delegated)" if x["delegated"] else "")
        rows.append(f"| {a} | {r['owner']} | {fmt(ob)} / {fmt(oc)} | {r['stage']} | {len(b.get('attempts', []))} | {rv.get('cycle', '-')} | "
                    f"{len(rv.get('policy_passed', []))}/{len(rv.get('policy_passed', [])) + len({x.split(':')[0] for x in rv.get('policy_failures', [])})} | "
                    f"{sum(f['severity'] == 'high' for f in rv.get('findings', []))}/{sum(f['severity'] != 'high' for f in rv.get('findings', []))} | "
                    f"{r.get('hold_reason') or '; '.join(conds) or '-'} |")
    facts = json.dumps({a: {"stage": recs[a]["stage"], "owner": recs[a]["owner"], "hold_reason": recs[a].get("hold_reason"), "flags": recs[a]["target_spec"]["flags"],
                            "medium_low_findings": [f["issue"][:160] for f in recs[a].get("review", {}).get("findings", []) if f["severity"] != "high"][:4],
                            "builder_summary": recs[a].get("build", {}).get("summary", "")[:300]} for a in st.apps}, indent=1)
    try:
        narrative = rt["llm"].text(
            "You are the Migration Orchestrator writing a go/no-go summary for the migration lead. Use only the facts given. "
            "Plain language, under 250 words: what is ready, what is held and why, what the lead must decide or confirm before the "
            "cutover gate. No headings; at most 6 bullets.", facts, max_tokens=4000)
    except Exception as ex:
        narrative = f"(summary unavailable: {ex})"
    u = rt["llm"].usage()
    return "\n".join([
        f"# Go/no-go: wave {st.wave} ({s} to {e})", "",
        f"Mode: {'DRY-RUN - code generated, validated and reviewed; nothing deployed' if rt['dry_run'] else 'LIVE'}. "
        f"Wave approved by {st.approved_by}. Ready: {len(ready)} of {len(st.apps)}.", "",
        narrative, "",
        "| App | Owner | Owner approval build / cutover | Stage | Builder attempts | Review cycles | Policies passed | Findings high/other | Hold reason or cutover conditions |",
        "|---|---|---|---|---|---|---|---|---|", *rows, "",
        f"Generated code: `{rt['builder'].workroot}/<app_id>/`. Records: `{rt['work']}/records/`. Messages: `{rt['work']}/messages.jsonl`.",
        f"Claude usage: {u['calls']} calls, {u['input_tokens']:,} input / {u['output_tokens']:,} output tokens "
        f"(about ${u['estimated_cost_usd']} at an assumed $3/$15 per million tokens).", "",
        "Gate 2 (cutover) needs, per application, the owner's cutover approval and the migration lead's go. "
        "Ready means ready for gate 2, not approved to move.",
    ]) + "\n"


# --------------------------------------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "approve", "approve-app", "reset-app", "status"])
    ap.add_argument("--app", default="")
    ap.add_argument("--gate", choices=["build", "cutover"], default="build")
    ap.add_argument("--reject", action="store_true")
    ap.add_argument("--reason", default="")
    ap.add_argument("--wave", type=int, required=True)
    ap.add_argument("--plan", default="../wave_planner/out/wave_plan.json")
    ap.add_argument("--outputs", default="../loader/tf_outputs.json")
    ap.add_argument("--secret-id", default="capstone/anthropic-api-key")
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--work", default=None)
    ap.add_argument("--approver", default="")
    ap.add_argument("--plugin-cache", default=os.path.expanduser("~/capstone_kb/kb_aws/terraform/.terraform/providers"))
    a = ap.parse_args()
    work = a.work or f"runs/wave-{a.wave}"
    os.makedirs(os.path.join(work, "approvals"), exist_ok=True)
    store = PlanStore(work)

    if a.cmd == "approve":
        if not a.approver.strip(): sys.exit("--approver is required")
        plan = json.load(open(a.plan))
        rec = {"wave": a.wave, "approver": a.approver.strip(), "approved_at": now().isoformat(timespec="seconds"),
               "plan_sha256": plan_hash(a.plan), "groups": plan["plans"][0]["waves"][a.wave - 1]}
        json.dump(rec, open(approval_path(work, a.wave), "w"), indent=1)
        print(f"Wave {a.wave} approved by {rec['approver']} ({len(rec['groups'])} move groups). Re-run `flow.py run` to build.")
        return
    if a.cmd == "reset-app":
        rec = store.get(a.app)
        if not rec: sys.exit(f"no record for {a.app}")
        if rec["stage"] not in (Stage.blocked.value, Stage.held.value):
            sys.exit(f"{a.app} is {rec['stage']}; only blocked or held apps can be reset")
        store.write("orchestrator", a.app, {"stage": Stage.planned.value, "hold_reason": ""})
        print(f"{a.app}: {rec['stage']} -> planned; it will be rebuilt on the next run (owner approval still required)")
        return
    if a.cmd == "approve-app":
        rec = store.get(a.app)
        if not rec: sys.exit(f"{a.app} has no migration record for wave {a.wave}; run `flow.py run --wave {a.wave}` first")
        if not a.approver.strip(): sys.exit("--approver is required")
        if a.reject and not a.reason.strip(): sys.exit("--reason is required with --reject")
        if a.gate == "cutover" and rec["stage"] != Stage.ready.value:
            sys.exit(f"{a.app} is {rec['stage']}, not ready; cutover approval is only taken for ready apps")
        d = {"wave": a.wave, "app_id": a.app, "gate": a.gate, "owner": rec["owner"], "approver": a.approver.strip(),
             "delegated": a.approver.strip().lower() != rec["owner"].lower(),
             "decision": "reject" if a.reject else "approve", "reason": a.reason.strip(),
             "decided_at": now().isoformat(timespec="seconds"), "spec_sha256": spec_hash(rec["target_spec"])}
        p = owner_approval_path(work, a.wave, a.app, a.gate)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        json.dump(d, open(p, "w"), indent=1)
        print(f"{a.app} ({a.gate}): {d['decision']} by {d['approver']}" + (f" on behalf of owner {rec['owner']} (recorded as delegated)" if d["delegated"] else ""))
        return
    if a.cmd == "status":
        for app_id, r in store.all().items():
            ob = owner_approval(work, a.wave, app_id, "build", r["target_spec"])
            print(f"{app_id:16} {r['stage']:16} owner={r.get('owner', '?'):22} build-approval={'pending' if not ob else ob['decision']:8} {r.get('hold_reason') or ''}")
        return

    import boto3
    from mcp import StdioServerParameters
    from llm import Claude
    here = os.path.dirname(os.path.abspath(__file__))
    params = StdioServerParameters(command=sys.executable, args=[os.path.join(here, "..", "mcp_inventory", "server.py"), "--outputs", a.outputs],
                                   env={**os.environ, "INVENTORY_MIN_SCORE": os.environ.get("INVENTORY_MIN_SCORE", "0.64")})
    cache = {}

    def fetch(app_ids):
        key = tuple(app_ids)
        if key not in cache: cache[key] = asyncio.run(fetch_inventory(params, list(app_ids)))
        return cache[key]

    key = boto3.client("secretsmanager").get_secret_value(SecretId=a.secret_id)["SecretString"].strip()
    llm = Claude(key, a.model)
    lock = threading.Lock()

    def log(m):
        with lock: print(m, flush=True)

    rt = {"store": store, "fetch": fetch, "llm": llm, "work": work, "dry_run": True, "log": log,
          "builder": Builder(llm, TerraformRunner(a.plugin_cache if os.path.isdir(a.plugin_cache) else None), os.path.join(work, "apps")),
          "reviewer": Reviewer(llm), "validation": Validation()}
    flow = make_flow_class()()
    flow._rt = rt
    flow.kickoff(inputs={"wave": a.wave, "plan_path": a.plan})
    print(json.dumps({"outcome": flow.state.outcome, **llm.usage()}))


if __name__ == "__main__":
    main()
