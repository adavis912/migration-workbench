# GitHub edition of the Migration Workbench

A second, fully separate workbench that runs the Wave 1 pipelines on **GitHub Actions** instead of GitLab.
The GitLab edition (its host, projects, pipelines, secrets and login) is not changed by anything here.

| | GitLab edition | GitHub edition |
| --- | --- | --- |
| Host | `capstone-migration-workbench` | `capstone-gh-migration-workbench` (own EC2, own login) |
| Code for the host | GitLab `migration-workbench` | this repository, `github-edition` branch |
| App pipelines | GitLab CI in the sample projects | GitHub Actions in [`adavis912/migration-pilots`](https://github.com/adavis912/migration-pilots) |
| Human cutover gates | Manual jobs | Environments `cutover-5pct`, `cutover-25pct`, `cutover-100pct` with a required reviewer |
| AWS access | GitLab OIDC → `capstone-gitlab-plan` | GitHub OIDC → `capstone-gh-plan` (read-only, `main` only) |
| Terraform state | `capstone-tfstate-<account>` | `capstone-gh-tfstate-<account>` |
| Secrets | `capstone/*` | `capstone-gh/*` (reads only the shared Claude and Eraser keys) |
| Nightly stop | EventBridge schedule (own) | `capstone-gh-workbench-nightly-stop`, 2:00 am New York |

## Files

| Path | Purpose |
| --- | --- |
| `github/infra/main.tf` | OIDC provider, plan role, state bucket, workbench host, security group, nightly stop |
| `github/install_github_workbench.sh` | Installs the workbench on the host from this branch (run by the host's user data) |
| `github/make_workflow.py` | Writes an app's workflow: same stages, gates and job names as the GitLab pipelines |
| `dashboard/gh_provider.py` | Reads GitHub Actions runs and jobs for the App Pipelines page (`WB_PROVIDER=github`) |

## Set up (CloudShell)

1. `terraform -chdir=github/infra init && terraform -chdir=github/infra apply`
   (set `-var create_oidc_provider=false` if the account already has the GitHub OIDC provider).
2. In `migration-pilots`: add the repository variable `AWS_PLAN_ROLE_ARN` (the `plan_role_arn` output) and the
   three cutover environments, each with a required reviewer.
3. Store a read-only GitHub token as `capstone-gh/github-read-token` so the workbench can poll the Actions API.
4. Open the `workbench_url` output. The login is in `capstone-gh/dashboard-login`.

Code scans (AWS Transform) stay on the GitLab edition for now; the Assessment page here shows no scans.
