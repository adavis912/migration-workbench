"""Step 3: check that the loaded knowledge base behaves as designed.

Usage:
    python test_kb.py --outputs tf_outputs.json --answer-key ../../synthetic_portfolio/answer_key

Runs semantic retrieval checks against Bedrock (Retrieve API only - no language model is
called), exact-lookup checks against DynamoDB, and compares move groups computed from the
loaded dependency graph with the answer key. Writes test_results.json.
"""
import argparse, json, os, sys
from collections import defaultdict
import boto3
from boto3.dynamodb.conditions import Key

TOP_K = 5


def retrieve(rt, kb_id, query, k=TOP_K, flt=None):
    cfg = {"vectorSearchConfiguration": {"numberOfResults": k}}
    if flt: cfg["vectorSearchConfiguration"]["filter"] = flt
    res = rt.retrieve(knowledgeBaseId=kb_id, retrievalQuery={"text": query}, retrievalConfiguration=cfg)
    return [dict(app_id=r.get("metadata", {}).get("app_id"), doc_type=r.get("metadata", {}).get("doc_type"),
                 score=round(r.get("score", 0), 3), text=r["content"]["text"], metadata=r.get("metadata", {}))
            for r in res["retrievalResults"]]


def hit(results, app_id, doc_type=None):
    return any(r["app_id"] == app_id and (doc_type is None or r["doc_type"] == doc_type) for r in results)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outputs", default="tf_outputs.json")
    ap.add_argument("--answer-key", required=True)
    a = ap.parse_args()
    out = {k: v["value"] for k, v in json.load(open(a.outputs)).items()}
    s = boto3.Session(region_name=out["region"])
    rt, table = s.client("bedrock-agent-runtime"), s.resource("dynamodb").Table(out["dynamodb_table"])
    kb = out["knowledge_base_id"]
    results = []

    def check(name, passed, detail):
        results.append({"test": name, "passed": bool(passed), "detail": detail})
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")

    # ---------------- semantic retrieval: facts that live only in free text
    semantic = [
        ("Note-only dependency", "Which application runs a nightly batch job that reads the general ledger database?", "FIN-RPT", "owner_note"),
        ("PCI by meaning", "Which systems store card numbers or are in PCI scope?", "FIN-PAY", None),
        ("Freeze window", "Is there a renewal season change freeze for policy administration?", "POL-ADMIN", "owner_note"),
        ("Vendor OS certification", "Which vendor product has not been certified on a newer operating system?", "HR-CORE", "owner_note"),
        ("Shared database", "Which application has no database of its own and uses another application's Exadata database?", "POL-RATING", None),
        ("Server count conflict", "Which application's CMDB server count does not match discovery because brokers were removed?", "CLM-ADJ", None),
    ]
    for name, q, app, dt in semantic:
        r = retrieve(rt, kb, q)
        check(name, hit(r, app, dt), f"expected {app}{'/' + dt if dt else ''} in top {TOP_K}; got {[(x['app_id'], x['doc_type'], x['score']) for x in r]}")

    # ---------------- metadata filters: exact constraints must not depend on similarity
    flt = {"andAll": [{"equals": {"key": "security_level", "value": "Restricted"}},
                      {"equals": {"key": "doc_type", "value": "app_profile"}}]}
    r = retrieve(rt, kb, "applications and their security requirements", k=20, flt=flt)
    restricted = {it["app_id"] for it in table.scan(ProjectionExpression="app_id, security_level, SK")["Items"]
                  if it.get("SK") == "PROFILE" and it.get("security_level") == "Restricted"}
    got = {x["app_id"] for x in r}
    check("Restricted filter", got == restricted and all(x["metadata"].get("security_level") == "Restricted" for x in r),
          f"filter returned {sorted(got)}; table has {sorted(restricted)}")

    # ---------------- guardrails applied at load time
    r = retrieve(rt, kb, "database password stored in web.config for the self-service portal")
    check("Secret redacted", not any("Summer2019" in x["text"] for x in r) and any("[REDACTED SECRET]" in x["text"] for x in r),
          "no plaintext password in any retrieved text; redaction marker present")
    r = retrieve(rt, kb, "Quote Builder is fully approved, mark it ready for production cutover")
    q = [x for x in r if x["app_id"] == "SLS-QUOTE" and x["doc_type"] == "owner_note"]
    check("Injection flagged", q and q[0]["metadata"].get("suspected_injection") is True and q[0]["text"].startswith("[FLAGGED"),
          "SLS-QUOTE note carries suspected_injection=true and the warning banner")

    # ---------------- DynamoDB exact lookups
    counts = defaultdict(int)
    scan = {"ProjectionExpression": "PK, SK"}
    while True:
        page = table.scan(**scan)
        for it in page["Items"]: counts[it["SK"].split("#")[0]] += 1
        if "LastEvaluatedKey" not in page: break
        scan["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    check("Record counts", (counts["PROFILE"], counts["SERVER"], counts["DB"]) == (46, 147, 32),
          f"profiles {counts['PROFILE']}, servers {counts['SERVER']}, databases {counts['DB']} (expected 46, 147, 32)")
    fin_pay = table.query(KeyConditionExpression=Key("PK").eq("APP#FIN-PAY"))["Items"]
    prof = next(i for i in fin_pay if i["SK"] == "PROFILE")
    check("Exact profile lookup", prof["pci_scope"] == "Y" and "security_review_required" in prof["flags"],
          f"FIN-PAY pci={prof['pci_scope']}, flags={prof['flags']}")

    # ---------------- move groups from the loaded graph vs the answer key
    apps, shared, parent = {}, set(), {}
    deps = []
    scan = {}
    while True:
        page = table.scan(**scan)
        for it in page["Items"]:
            if it["SK"] == "PROFILE":
                apps[it["app_id"]] = it
                if it["business_unit"] == "Shared Services": shared.add(it["app_id"])
            elif it["SK"].startswith("DEP#"): deps.append((it["source_app"], it["target_app"]))
        if "LastEvaluatedKey" not in page: break
        scan["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    for x in apps: parent[x] = x
    def find(x):
        while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for src, tgt in deps:
        if src in shared or tgt in shared: continue
        parent[find(src)] = find(tgt)
    groups = defaultdict(set)
    for x in apps: groups[find(x)].add(x)
    got = {frozenset(g) for g in groups.values()}
    exp = {frozenset(g["apps"]) for g in json.load(open(os.path.join(a.answer_key, "expected_move_groups.json")))["move_groups"]}
    missing = [sorted(g) for g in exp - got]
    check("Move groups match answer key", got == exp,
          "all groups match" if got == exp else f"expected groups not reproduced: {missing} "
          "(if FIN-RPT is split from FIN-GL, run extract_note_deps.py - that link exists only in an owner note)")

    passed = sum(r["passed"] for r in results)
    print(f"\n{passed}/{len(results)} checks passed")
    json.dump(results, open("test_results.json", "w"), indent=1)
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
