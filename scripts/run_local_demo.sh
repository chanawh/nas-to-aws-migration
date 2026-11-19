#!/usr/bin/env bash
# Full live demo against moto on :5000: Terraform apply, sync, verify,
# corrupt, detect, repair, destroy. No Docker, no AWS account, $0.
# Run from the project root: bash scripts/run_local_demo.sh
# Requires: Terraform >= 1.9, the venv at ~/workspace/nas-to-aws-migration-venv/
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Portable paths: in this VM the venv is ~/workspace/nas-to-aws-migration-venv/;
# on your machine use python/.venv (created below if missing) or system python.
if [ -x "$HOME/workspace/nas-to-aws-migration-venv/bin/python" ]; then
  VENV="$HOME/workspace/nas-to-aws-migration-venv/bin/python"
  MOTO_SERVER="$HOME/workspace/nas-to-aws-migration-venv/bin/moto_server"
elif [ -x "$ROOT/python/.venv/bin/python" ]; then
  VENV="$ROOT/python/.venv/bin/python"
  MOTO_SERVER="$ROOT/python/.venv/bin/moto_server"
else
  if [ ! -x "$ROOT/python/.venv/bin/python" ]; then
    python3 -m venv "$ROOT/python/.venv"
    "$ROOT/python/.venv/bin/pip" install -q boto3 "moto[server]"
  fi
  VENV="$ROOT/python/.venv/bin/python"
  MOTO_SERVER="$ROOT/python/.venv/bin/moto_server"
fi
if [ -n "${TF:-}" ]; then
  TF="$TF"
elif [ -x /tmp/tf/terraform ]; then
  TF=/tmp/tf/terraform
else
  TF=terraform
fi
# 127.0.0.1, not localhost: moto binds IPv4 only, and localhost can
# resolve to ::1 first, which Terraform then fails to dial.
ENDPOINT="http://127.0.0.1:5000"
DEMO=/tmp/nas2aws-demo
SOURCE="$DEMO/source"

cleanup() {
  echo "== cleanup: terraform destroy, stop moto =="
  (cd "$ROOT/terraform" && "$TF" destroy -auto-approve -input=false -var="endpoint_url=$ENDPOINT" >/dev/null 2>&1 || true)
  kill "$MOTO_PID" 2>/dev/null || true
}
trap cleanup EXIT

echo "== start moto on :5000 =="
"$MOTO_SERVER" -p 5000 -H 127.0.0.1 > /tmp/nas2aws-moto.log 2>&1 &
MOTO_PID=$!
for i in $(seq 1 30); do
  if curl -s "$ENDPOINT/moto-api/" >/dev/null 2>&1; then break; fi
  sleep 1
done

echo "== terraform init / validate / apply =="
cd "$ROOT/terraform"
"$TF" init -input=false >/dev/null
"$TF" fmt -check
"$TF" validate
"$TF" apply -auto-approve -input=false -var="endpoint_url=$ENDPOINT"

echo
echo "== generate =="
cd "$ROOT/python"
"$VENV" -m migrate_s3.cli generate --root "$SOURCE"
echo
echo "== dry-run =="
"$VENV" -m migrate_s3.cli sync --source "$SOURCE" --endpoint-url "$ENDPOINT" --dry-run
echo
echo "== sync (with DynamoDB audit) =="
"$VENV" -m migrate_s3.cli sync --source "$SOURCE" --endpoint-url "$ENDPOINT" --dynamodb-table nas2aws-migration-audit
echo
echo "== verify (expect PASS) =="
"$VENV" -m migrate_s3.cli verify --source "$SOURCE" --endpoint-url "$ENDPOINT"
echo
echo "== audit check (expect 24 records in DynamoDB) =="
"$VENV" -c "
import boto3
c = boto3.client('dynamodb', region_name='us-west-2', endpoint_url='$ENDPOINT', aws_access_key_id='test', aws_secret_access_key='test')
print('audit records:', c.scan(TableName='nas2aws-migration-audit')['Count'])
s = boto3.client('sns', region_name='us-west-2', endpoint_url='$ENDPOINT', aws_access_key_id='test', aws_secret_access_key='test')
print('sns topics:', [t['TopicArn'] for t in s.list_topics()['Topics']])
q = boto3.client('sqs', region_name='us-west-2', endpoint_url='$ENDPOINT', aws_access_key_id='test', aws_secret_access_key='test')
print('sqs queues:', q.list_queues().get('QueueUrls', []))
"
echo
echo "== corrupt one object =="
"$VENV" -c "
import boto3
s3 = boto3.client('s3', region_name='us-west-2', endpoint_url='$ENDPOINT', aws_access_key_id='test', aws_secret_access_key='test')
s3.put_object(Bucket='nas2aws-migration-finance', Key='budget-2027-draft.csv', Body=b'corrupted\n')
print('corrupted nas2aws-migration-finance/budget-2027-draft.csv')
"
echo
echo "== verify (expect FAIL on that object) =="
"$VENV" -m migrate_s3.cli verify --source "$SOURCE" --endpoint-url "$ENDPOINT" || true
echo
echo "== re-sync (expect 1 object re-uploaded) =="
"$VENV" -m migrate_s3.cli sync --source "$SOURCE" --endpoint-url "$ENDPOINT"
echo
echo "== verify (expect PASS) =="
"$VENV" -m migrate_s3.cli verify --source "$SOURCE" --endpoint-url "$ENDPOINT"
echo
echo "== report =="
"$VENV" -m migrate_s3.cli report --source "$SOURCE" --endpoint-url "$ENDPOINT"
echo
echo "== evidence (saved to evidence/) =="
mkdir -p "$ROOT/evidence"
"$VENV" -m migrate_s3.cli report --source "$SOURCE" --endpoint-url "$ENDPOINT" > "$ROOT/evidence/report.json"
"$VENV" -c "
import boto3, json
c = boto3.client('dynamodb', region_name='us-west-2', endpoint_url='$ENDPOINT', aws_access_key_id='test', aws_secret_access_key='test')
items = c.scan(TableName='nas2aws-migration-audit')['Items']
json.dump({'audit_records': len(items), 'table': 'nas2aws-migration-audit'}, open('$ROOT/evidence/audit.json', 'w'), indent=2)
print('evidence/report.json and evidence/audit.json written')
"
