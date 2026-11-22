# Load the knowledge base in your AWS account (CloudShell)

Builds the Bedrock Knowledge Base for the capstone migration agent and loads the synthetic
Harborview Insurance portfolio into it. Everything runs in **AWS CloudShell**, which is already
signed in as you, so no AWS access keys are needed. Region: **us-east-1**.

Expected cost: a few cents for embeddings and storage. A $10 budget alert is created as a guardrail.

## What gets created

| Resource | Purpose |
| --- | --- |
| S3 bucket `hvig-kb-source-<account>` | Prepared chunks + metadata files |
| S3 Vectors bucket and index `inventory` (1024 dims, cosine) | Vector store |
| Bedrock Knowledge Base `hvig-kb-inventory` + data source | Semantic search (Titan Text Embeddings V2) |
| IAM role `hvig-kb-kb-role` | Lets the knowledge base read the bucket, embed, and write vectors - nothing else |
| DynamoDB table `hvig-kb-inventory` | Exact lookups and the dependency graph |
| IAM policy `hvig-kb-agent-read` | Read-only access for the agents' inventory tools |
| AWS Budget `hvig-kb-monthly` | Email alerts at 80% and forecast 100% of $10 |

## Before you start (one time)

1. **Bedrock model access.** Bedrock console, us-east-1: make sure **Titan Text Embeddings V2** is
   available to your account.
2. **Permissions.** Your user needs to create IAM roles, S3 buckets, S3 Vectors, DynamoDB tables,
   Bedrock Knowledge Bases and Budgets. An administrator role in a personal account is fine.
3. Open **CloudShell** from the AWS console header, and make sure the region shown is **N. Virginia (us-east-1)**.

## Step 1 - Upload and unpack

In CloudShell choose **Actions > Upload file** and upload `capstone_kb.zip`. Then:

```bash
unzip -o capstone_kb.zip && cd capstone_kb
```

## Step 2 - Install Terraform and Python packages

CloudShell keeps only your home folder between sessions, so repeat this step in a new session.

```bash
sudo yum install -y yum-utils
sudo yum-config-manager --add-repo https://rpm.releases.hashicorp.com/AmazonLinux/hashicorp.repo
sudo yum -y install terraform
terraform -version

pip3 install -r kb_aws/loader/requirements.txt   # CloudShell Python is a virtualenv: no --user
```

## Step 3 - Create the AWS resources

```bash
cd kb_aws/terraform
cp terraform.tfvars.example terraform.tfvars
nano terraform.tfvars            # set alert_email to your address; save with Ctrl+O, exit Ctrl+X
terraform init
terraform plan                   # read it: 13 resources to add, nothing to change or destroy
terraform apply                  # type "yes"
terraform output -json > ../loader/tf_outputs.json
cd ../loader
```

You will get an email from AWS Budgets asking you to confirm the alert subscription - confirm it.

If `apply` fails with an error that the knowledge base role cannot be assumed, wait one minute and
run `terraform apply` again (new IAM roles take a moment to be usable).

## Step 4 - Prepare the data (local, no AWS calls)

```bash
python3 prepare.py --data ../../synthetic_portfolio/data --out build
```

It prints counts plus what it found: 1 redacted secret, 2 notes flagged for prompt injection,
1 CMDB/discovery conflict, 1 probably decommissioned server, 1 server with no discovery agent.

## Step 5 (recommended) - Let Claude find dependencies that exist only in notes

Uses your Claude API key, paid by your Ultra plan's monthly API credits. Store the key in Secrets
Manager first; `read -s` keeps it out of your shell history and off the screen:

```bash
read -s -p "Anthropic API key: " KEY; echo
aws secretsmanager create-secret --name capstone/anthropic-api-key --secret-string "$KEY"; unset KEY

python3 extract_note_deps.py --data ../../synthetic_portfolio/data --secret-id capstone/anthropic-api-key --out note_edges.json
python3 prepare.py --data ../../synthetic_portfolio/data --out build --note-edges note_edges.json
```

Read `note_edges.json` (kept edges) and `note_edges_dropped.json` (rejected proposals and why).
Edges from notes are stored as "unconfirmed (owner note only)" until a person confirms them.

Without this step everything still loads, but the FIN-RPT to FIN-GL dependency (written only in an
owner note) is missing from the graph, and the move-group check in Step 7 fails on purpose to show it.

## Step 6 - Load into AWS and sync the knowledge base

```bash
python3 load.py --build build --outputs tf_outputs.json
```

Uploads 276 files (138 chunks + their metadata), writes the DynamoDB items, then runs a Bedrock
ingestion job and waits for it. Expect `numberOfDocumentsFailed: 0`. Safe to re-run at any time.

## Step 7 - Test it

```bash
python3 test_kb.py --outputs tf_outputs.json --answer-key ../../synthetic_portfolio/answer_key
```

12 checks: six semantic questions (facts that live only in free text), a metadata filter
(Restricted apps only), redaction, injection flagging, record counts, an exact lookup, and move
groups compared with the answer key. Results are saved to `test_results.json` - useful evidence
for your checkpoint write-ups.

If a semantic check fails, the output shows what was retrieved instead. Send it to me and we'll
adjust the chunk wording or the question.

## Using it from the agents

The agents' inventory tools need only the policy in `terraform output agent_read_policy_arn`:
`bedrock:Retrieve` on this knowledge base and read-only DynamoDB access. They call the
**Retrieve** API (search only) and send results to Claude through your API key - no Bedrock
language model is used.

## Clean up when finished

```bash
cd ../terraform && terraform destroy     # type "yes"
aws secretsmanager delete-secret --secret-id capstone/anthropic-api-key --recovery-window-in-days 7
```

## Do not commit

`terraform.tfvars`, `tf_outputs.json`, Terraform state, `build/` and `note_edges*.json` are listed
in `.gitignore`. The API key lives only in Secrets Manager.
