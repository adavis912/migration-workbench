"""Live check of the inventory MCP server over stdio, as an agent would use it.

Usage:  python live_check.py --outputs ../loader/tf_outputs.json --answer-key ../../synthetic_portfolio/answer_key
Starts server.py as a subprocess, lists its tools, calls each one against the real AWS data,
calibrates the search relevance threshold, and writes live_check.json.
"""
import argparse, asyncio, json, os, sys
from mcp import Client, StdioServerParameters

IN_DOMAIN = [
    "Which application runs a nightly batch job that reads the general ledger database?",
    "Which systems store card numbers or are in PCI scope?",
    "Is there a renewal season change freeze for policy administration?",
    "Which vendor product has not been certified on a newer operating system?",
    "Which application's CMDB server count does not match discovery because brokers were removed?",
    "Which application shares the Policy Administration Exadata database?",
]
OFF_TOPIC = [
    "What is the maximum number of connections for Aurora PostgreSQL?",
    "How do I configure Amazon Cognito user pools for social login?",
    "What were the quarterly earnings for the company last year?",
    "How do I convert a COBOL program to Java?",
    "Which Kubernetes ingress controller should I use on EKS?",
    "What is the best recipe for sourdough bread?",
]


async def main(a):
    params = StdioServerParameters(command=sys.executable, args=[os.path.join(os.path.dirname(__file__), "server.py"),
                                                                 "--outputs", a.outputs])
    report = {}
    async with Client(params) as c:
        async def call(name, args):
            r = await c.call_tool(name, args)
            if r.is_error: return {"error": r.content[0].text}
            return json.loads(r.content[0].text)

        tools = await c.list_tools()
        report["tools"] = [t.name for t in tools.tools]
        print("tools:", report["tools"])

        top = lambda res: res["results"][0]["score"] if res["results"] else 0.0
        ins = [(q, await call("search_apps", {"query": q, "k": 1})) for q in IN_DOMAIN]
        offs = [(q, await call("search_apps", {"query": q, "k": 1})) for q in OFF_TOPIC]
        lo_in, hi_off = min(top(r) for _, r in ins), max(top(r) for _, r in offs)
        report["threshold_calibration"] = {
            "in_domain_top1": {q[:50]: (r["results"][0]["app_id"], top(r)) for q, r in ins},
            "off_topic_top1": {q[:50]: top(r) for q, r in offs},
            "lowest_in_domain": lo_in, "highest_off_topic": hi_off,
            "suggested_min_score": round((lo_in + hi_off) / 2, 3) if lo_in > hi_off else None}
        print(f"threshold: lowest in-domain top-1 {lo_in}, highest off-topic top-1 {hi_off} -> "
              f"suggested min_score {report['threshold_calibration']['suggested_min_score']}")

        r = await call("list_apps", {"security_level": "Restricted"})
        report["list_restricted"] = [x["app_id"] for x in r["apps"]]
        g = await call("get_app", {"app_id": "CLM-ADJ"})
        report["get_app_CLM-ADJ"] = g["totals"]
        d = await call("get_dependencies", {"app_id": "FIN-RPT", "hops": 2})
        report["deps_FIN-RPT"] = {"apps": d["apps"], "needs_owner_confirmation": d["needs_owner_confirmation"]}
        mg = await call("get_move_groups", {})
        exp = {frozenset(x["apps"]) for x in json.load(open(os.path.join(a.answer_key, "expected_move_groups.json")))["move_groups"]}
        report["move_groups_match_answer_key"] = {frozenset(x["apps"]) for x in mg["move_groups"]} == exp
        fin = await call("get_move_groups", {"business_unit": "Finance"})
        report["finance_groups"] = [{k: g[k] for k in ("group_id", "apps", "active_servers", "freeze_windows", "security_reviews")}
                                    for g in fin["move_groups"]]
        report["bad_input"] = await call("get_app", {"app_id": "FIN-GL; DROP TABLE"})
        for k, v in report.items():
            if k != "threshold_calibration": print(f"{k}: {v}")
    json.dump(report, open("live_check.json", "w"), indent=1, default=str)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--outputs", required=True)
    ap.add_argument("--answer-key", required=True)
    asyncio.run(main(ap.parse_args()))
