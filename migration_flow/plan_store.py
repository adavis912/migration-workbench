"""plan_store: the shared migration record, one per application (Checkpoint 5.1, Section 4).

Rules enforced here, not left to prompts:
  * each agent writes only the fields it owns;
  * only the Orchestrator changes an application's stage, and only along ALLOWED_TRANSITIONS;
  * every write is versioned (optimistic concurrency) and appended to the record's history;
  * every message is validated against the contract and appended to messages.jsonl.

Stored as JSON files on disk so a restarted Orchestrator resumes where it stopped.
"""
import json, os, threading

from contracts import ALLOWED_TRANSITIONS, Message, Stage, now

FIELD_OWNERS = {
    "stage": "orchestrator", "wave": "orchestrator", "move_group": "orchestrator", "target_spec": "orchestrator",
    "inventory_ref": "orchestrator", "hold_reason": "orchestrator", "owner": "orchestrator",
    "build": "builder", "review": "reviewer", "validation": "validation",
}


class OwnershipError(PermissionError): pass
class VersionConflict(RuntimeError): pass


class PlanStore:
    def __init__(self, root):
        self.root = root
        os.makedirs(os.path.join(root, "records"), exist_ok=True)
        self._locks, self._guard = {}, threading.Lock()

    def _lock(self, app_id):
        with self._guard:
            return self._locks.setdefault(app_id, threading.Lock())

    def _path(self, app_id):
        return os.path.join(self.root, "records", f"{app_id}.json")

    def get(self, app_id):
        p = self._path(app_id)
        return json.load(open(p)) if os.path.exists(p) else None

    def all(self):
        d = os.path.join(self.root, "records")
        return {f[:-5]: json.load(open(os.path.join(d, f))) for f in sorted(os.listdir(d)) if f.endswith(".json")}

    def create(self, app_id, fields):
        with self._lock(app_id):
            if self.get(app_id): return self.get(app_id)
            rec = {"app_id": app_id, "version": 1, "stage": Stage.planned.value, **fields,
                   "history": [{"agent": "orchestrator", "time": now().isoformat(), "version": 1, "change": "created"}]}
            self._save(rec)
            return rec

    def write(self, agent, app_id, updates, expected_version=None):
        with self._lock(app_id):
            rec = self.get(app_id)
            if rec is None: raise KeyError(app_id)
            if expected_version is not None and rec["version"] != expected_version:
                raise VersionConflict(f"{app_id}: expected v{expected_version}, found v{rec['version']}")
            for k, v in updates.items():
                owner = FIELD_OWNERS.get(k)
                if owner != agent: raise OwnershipError(f"{agent} may not write {app_id}.{k} (owner: {owner})")
                if k == "stage":
                    old, new = Stage(rec["stage"]), Stage(v)
                    if new != old and new not in ALLOWED_TRANSITIONS[old]:
                        raise ValueError(f"{app_id}: illegal stage change {old.value} -> {new.value}")
            rec.update(updates)
            rec["version"] += 1
            rec["history"].append({"agent": agent, "time": now().isoformat(), "version": rec["version"],
                                   "change": ", ".join(f"{k}={updates[k]}" if k == "stage" else k for k in updates)})
            self._save(rec)
            return rec

    def log_message(self, msg: Message):
        Message.model_validate(msg.model_dump(by_alias=True))      # re-validate before accepting
        with self._guard:
            with open(os.path.join(self.root, "messages.jsonl"), "a") as f:
                f.write(msg.model_dump_json(by_alias=True) + "\n")
        return msg

    def _save(self, rec):
        tmp = self._path(rec["app_id"]) + ".tmp"
        json.dump(rec, open(tmp, "w"), indent=1, default=str)
        os.replace(tmp, self._path(rec["app_id"]))
