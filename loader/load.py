"""Step 2 of the load: upload prepared chunks and items to AWS, then sync the knowledge base.

Usage (from the loader folder, after `terraform apply`):
    terraform -chdir=../terraform output -json > tf_outputs.json
    python load.py --build build --outputs tf_outputs.json

Re-runnable: files and table items are replaced by ID, and anything no longer in the build is
deleted, so the knowledge base always matches the latest prepared data.
"""
import argparse, json, os, sys, time
import boto3


def tf(outputs, name):
    return outputs[name]["value"]


def sync_s3(s3, bucket, local_dir, prefix="kb/"):
    wanted = {}
    for root, _, files in os.walk(local_dir):
        for f in files:
            path = os.path.join(root, f)
            wanted[prefix + os.path.relpath(path, local_dir).replace(os.sep, "/")] = path
    existing = set()
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        existing |= {o["Key"] for o in page.get("Contents", [])}
    for key, path in sorted(wanted.items()):
        ctype = "application/json" if key.endswith(".json") else "text/plain; charset=utf-8"
        s3.upload_file(path, bucket, key, ExtraArgs={"ContentType": ctype})
    stale = sorted(existing - set(wanted))
    for i in range(0, len(stale), 1000):
        s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in stale[i:i + 1000]]})
    return len(wanted), len(stale)


def sync_dynamodb(ddb, table_name, items_path):
    table = ddb.Table(table_name)
    items = [json.loads(line) for line in open(items_path)]
    keys = {(i["PK"], i["SK"]) for i in items}
    existing = set()
    scan = {"ProjectionExpression": "PK, SK"}
    while True:
        page = table.scan(**scan)
        existing |= {(i["PK"], i["SK"]) for i in page["Items"]}
        if "LastEvaluatedKey" not in page: break
        scan["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    with table.batch_writer(overwrite_by_pkeys=["PK", "SK"]) as w:
        for it in items: w.put_item(Item=it)
        for pk, sk in existing - keys: w.delete_item(Key={"PK": pk, "SK": sk})
    return len(items), len(existing - keys)


def ingest(agent, kb_id, ds_id, timeout_s=900):
    job = agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id,
                                    description="capstone synthetic inventory load")["ingestionJob"]
    job_id, start = job["ingestionJobId"], time.time()
    while True:
        job = agent.get_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id, ingestionJobId=job_id)["ingestionJob"]
        status = job["status"]
        print(f"  ingestion {job_id}: {status}")
        if status in ("COMPLETE", "FAILED", "STOPPED"): return job
        if time.time() - start > timeout_s: sys.exit("Ingestion is taking too long; check the Bedrock console.")
        time.sleep(10)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default="build")
    ap.add_argument("--outputs", default="tf_outputs.json", help="terraform output -json")
    a = ap.parse_args()
    out = json.load(open(a.outputs))
    region = tf(out, "region")
    session = boto3.Session(region_name=region)

    n, removed = sync_s3(session.client("s3"), tf(out, "source_bucket"), os.path.join(a.build, "kb"))
    print(f"S3: uploaded {n} files (chunks + metadata), removed {removed} stale")

    n, removed = sync_dynamodb(session.resource("dynamodb"), tf(out, "dynamodb_table"),
                               os.path.join(a.build, "dynamodb_items.jsonl"))
    print(f"DynamoDB: wrote {n} items, removed {removed} stale")

    print("Bedrock: syncing knowledge base")
    job = ingest(session.client("bedrock-agent"), tf(out, "knowledge_base_id"), tf(out, "data_source_id"))
    print(json.dumps(job.get("statistics", {}), indent=1, default=str))
    if job["status"] != "COMPLETE" or job.get("statistics", {}).get("numberOfDocumentsFailed", 0):
        print("FAILURE REASONS:", job.get("failureReasons"))
        sys.exit(1)
    print("Load complete.")


if __name__ == "__main__":
    main()
