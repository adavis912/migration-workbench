#!/bin/bash
# First-boot setup for the migration workbench. Log: /var/log/capstone-bootstrap.log
exec > >(tee -a /var/log/capstone-bootstrap.log) 2>&1
set -euxo pipefail
dnf install -y git python3.11 python3.11-pip unzip jq tar gzip
cd /tmp
curl -sSLo tf.zip https://releases.hashicorp.com/terraform/1.9.8/terraform_1.9.8_linux_amd64.zip
unzip -o tf.zip terraform -d /usr/local/bin && rm -f tf.zip
mkdir -p /opt/capstone && cd /opt/capstone
aws s3 cp "s3://${bucket}/${key}" /tmp/capstone_kb.tgz --region us-east-1
tar xzf /tmp/capstone_kb.tgz -C /opt/capstone && rm -f /tmp/capstone_kb.tgz
python3.11 -m venv /opt/capstone/venv
/opt/capstone/venv/bin/pip install --quiet --upgrade pip
/opt/capstone/venv/bin/pip install --quiet crewai anthropic boto3 python-hcl2 pyyaml
/opt/capstone/venv/bin/pip install --quiet "mcp==2.3.0"
mkdir -p /opt/capstone/.tfcache
cat > /etc/profile.d/capstone.sh <<'PROFILE'
export CAPSTONE=/opt/capstone/capstone_kb
export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1
export CREWAI_DISABLE_TELEMETRY=true OTEL_SDK_DISABLED=true
export TF_PLUGIN_CACHE_DIR=/opt/capstone/.tfcache
[ -f /opt/capstone/venv/bin/activate ] && . /opt/capstone/venv/bin/activate
alias cdc='cd $CAPSTONE/kb_aws'
PROFILE
chmod -R a+rwX /opt/capstone
date -u +%FT%TZ > /opt/capstone/BOOTSTRAP_DONE
