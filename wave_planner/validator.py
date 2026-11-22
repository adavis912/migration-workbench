"""Deterministic wave validator and scoring (Checkpoint 4.1, Section 3).

Stage 1 - hard checks (a candidate that fails any is pruned and never scored):
  capacity, freeze windows (+ business-day buffer), security review for Restricted/PCI apps,
  shared services only after all their consumers, nothing placed twice.
Stage 2 - computed rubric criteria (80 of 100 points): risk sequencing (25), dependency
  cohesion (20), readiness (20), wave balance (15). Business fit (20) comes from the Critic.

Pure functions over plain data, so they can be unit-tested and exposed as an MCP tool.
"""
import datetime as dt
from dataclasses import dataclass, field

READINESS_FLAGS = {"stale", "cmdb_discovery_conflict", "possibly_decommissioned_server", "discovery_gap",
                   "suspected_injection", "secret_in_config", "secret_redacted"}


@dataclass
class Rules:
    max_apps: int = 10
    max_servers: int = 40
    freeze_buffer_business_days: int = 5
    program_start: dt.date = dt.date(2026, 12, 7)   # Monday
    wave_days: int = 14
    max_waves: int = 8

    def window(self, wave_no):
        start = self.program_start + dt.timedelta(days=(wave_no - 1) * self.wave_days)
        return start, start + dt.timedelta(days=self.wave_days - 1)


@dataclass
class Group:
    group_id: str
    apps: list
    business_units: list
    active_servers: int
    is_shared_service: bool
    tiers: dict                      # app_id -> criticality tier (1 = most critical)
    freeze_windows: list             # [{"app_id","start","end"}]
    blocked_reasons: list = field(default_factory=list)   # e.g. security review missing
    readiness_flags: dict = field(default_factory=dict)   # app_id -> [flags]
    unconfirmed_links: list = field(default_factory=list)
    shared_deps: list = field(default_factory=list)       # shared-service group ids this group uses
    consumers: list = field(default_factory=list)         # for shared groups: group ids that use it


def business_days_before(d, n):
    while n:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5: n -= 1
    return d


def blocked_reasons_for(profile):
    """Reasons an app can't be placed in any wave (independent of timing)."""
    restricted = profile.get("security_level") == "Restricted" or profile.get("pci_scope") == "Y"
    if restricted and profile.get("security_review") != "Complete":
        return [f"{profile['app_id']}: {profile['security_level']}/PCI={profile['pci_scope']} needs a completed "
                f"security review (status: {profile.get('security_review')})"]
    return []


def check_wave(groups_by_id, placed, wave_no, candidate, rules: Rules):
    """Hard checks for adding `candidate` (list of group ids) as wave `wave_no`.
    `placed` maps group_id -> wave number for earlier waves. Returns list of violations."""
    v = []
    if not candidate: return ["empty wave"]
    if wave_no > rules.max_waves: v.append(f"wave {wave_no} exceeds the {rules.max_waves}-wave limit")
    if len(set(candidate)) != len(candidate): v.append("a group appears twice in the wave")
    gs = []
    for gid in candidate:
        g = groups_by_id.get(gid)
        if g is None: v.append(f"unknown group {gid}"); continue
        if gid in placed: v.append(f"{gid} already placed in wave {placed[gid]}")
        if g.blocked_reasons: v.append(f"{gid} is blocked: {'; '.join(g.blocked_reasons)}")
        gs.append(g)
    apps = sum(len(g.apps) for g in gs); servers = sum(g.active_servers for g in gs)
    if apps > rules.max_apps: v.append(f"{apps} apps > {rules.max_apps}")
    if servers > rules.max_servers: v.append(f"{servers} active servers > {rules.max_servers}")
    w_start, w_end = rules.window(wave_no)
    for g in gs:
        for f in g.freeze_windows:
            fs, fe = dt.date.fromisoformat(f["start"]), dt.date.fromisoformat(f["end"])
            guard = business_days_before(fs, rules.freeze_buffer_business_days)
            if w_start <= fe and w_end >= guard:
                v.append(f"{g.group_id}: wave {wave_no} ({w_start}..{w_end}) overlaps {f['app_id']} freeze "
                         f"{fs}..{fe} (with {rules.freeze_buffer_business_days}-business-day buffer from {guard})")
        if g.is_shared_service:
            waiting = [c for c in g.consumers if c not in placed and c not in candidate
                       and not groups_by_id[c].blocked_reasons]
            if waiting:
                v.append(f"{g.group_id} is a shared service; {len(waiting)} consumer group(s) not yet migrated "
                         f"(e.g. {', '.join(waiting[:3])})")
    return v


def wave_scores(groups_by_id, placed_waves, rules: Rules):
    """Computed criteria (0..max points) for a partial or complete plan.
    placed_waves: list of lists of group ids, wave 1 first."""
    apps = [(wi + 1, a, g) for wi, w in enumerate(placed_waves) for gid in w
            for g in [groups_by_id[gid]] for a in g.apps]
    if not apps: return {"risk_sequencing": 0, "dependency_cohesion": 0, "readiness": 0, "wave_balance": 0}

    def risk_ok(wave, tier):
        if tier >= 3: return 1.0
        if tier == 2: return 0.5 if wave == 1 else 1.0
        return 0.0 if wave == 1 else (0.5 if wave == 2 else 1.0)
    risk = 25 * sum(risk_ok(w, g.tiers.get(a, 3)) for w, a, g in apps) / len(apps)

    # Temporary hybrid links: a consumer that moved before its shared service keeps a hybrid link
    # open for (shared wave - consumer wave) waves. Unplaced shared services count as max_waves.
    placed = {gid: wi + 1 for wi, w in enumerate(placed_waves) for gid in w}
    link_waves = possible = 0
    for wi, w in enumerate(placed_waves):
        for gid in w:
            for sid in groups_by_id[gid].shared_deps:
                link_waves += max(0, placed.get(sid, rules.max_waves) - (wi + 1)); possible += rules.max_waves
    cohesion = 20 * (1 - link_waves / possible) if possible else 20

    ready_apps = sum(1 for _, a, g in apps if not (set(g.readiness_flags.get(a, [])) & READINESS_FLAGS)
                     and not g.unconfirmed_links)
    readiness = 20 * ready_apps / len(apps)

    fills = [sum(groups_by_id[gid].active_servers for gid in w) / rules.max_servers for w in placed_waves]
    balance = 15 * sum(max(0.0, 1 - abs(f - 0.8) / 0.8) for f in fills) / len(fills)
    return {"risk_sequencing": round(risk, 2), "dependency_cohesion": round(cohesion, 2),
            "readiness": round(readiness, 2), "wave_balance": round(balance, 2)}
