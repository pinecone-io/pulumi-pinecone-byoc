#!/usr/bin/env bash
#
# redrum.sh — deploy, monitor, and kill the Pinecone load generators
#
# Usage:
#   ./redrum.sh deploy    Build images, push to ECR, start ECS services
#   ./redrum.sh status    Show running task status
#   ./redrum.sh logs      Tail live logs from both services
#   ./redrum.sh logs writer|querier   Tail one service only
#   ./redrum.sh kill      Stop both services
#   ./redrum.sh destroy   Stop services AND delete all AWS resources
#
set -euo pipefail

# load .env if present
if [[ -f "$(dirname "${BASH_SOURCE[0]}")/.env" ]]; then
  set -o allexport
  source "$(dirname "${BASH_SOURCE[0]}")/.env"
  set +o allexport
fi

# ---------------------------------------------------------------------------
# Configuration — override any of these with environment variables
# ---------------------------------------------------------------------------
AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_PROFILE="${AWS_PROFILE:-}"
CLUSTER="${CLUSTER:-redrum}"
LOG_GROUP="${LOG_GROUP:-/redrum}"

INDEX_HOST="${INDEX_HOST:-}"
PINECONE_API_KEY="${PINECONE_API_KEY:-}"   # required — set this before running deploy

VECTOR_DIM="${VECTOR_DIM:-1024}"
WRITE_COUNT="${WRITE_COUNT:-200}"
QUERY_COUNT="${QUERY_COUNT:-10}"
TOP_K="${TOP_K:-10}"
MIN_SLEEP_SECONDS="${MIN_SLEEP_SECONDS:-60}"
MAX_SLEEP_SECONDS="${MAX_SLEEP_SECONDS:-600}"
WRITER_COUNT="${WRITER_COUNT:-1}"
QUERIER_COUNT="${QUERIER_COUNT:-1}"

DYNAMO_TABLE="${DYNAMO_TABLE:-redrum-freshness}"
SSM_FLAG_PATH="${SSM_FLAG_PATH:-/redrum/freshness_enabled}"
LAMBDA_FUNCTION="${LAMBDA_FUNCTION:-redrum-tracker}"
TRACKER_TIMEOUT="${TRACKER_TIMEOUT:-200}"   # seconds — probe runs 180s, needs buffer to return

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
AWS="aws --region $AWS_REGION --profile $AWS_PROFILE"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; RESET='\033[0m'

info()  { echo -e "${BLUE}▶ $*${RESET}"; }
ok()    { echo -e "${GREEN}✓ $*${RESET}"; }
err()   { echo -e "${RED}✗ $*${RESET}" >&2; exit 1; }

account_id() { $AWS sts get-caller-identity --query Account --output text; }
ecr_base()   { echo "$(account_id).dkr.ecr.$AWS_REGION.amazonaws.com"; }

# ---------------------------------------------------------------------------
# deploy
# ---------------------------------------------------------------------------
cmd_deploy() {
  [[ -z "$PINECONE_API_KEY" ]] && err "PINECONE_API_KEY is not set. Export it before running deploy."

  ACCOUNT_ID=$(account_id)
  ECR_BASE="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

  # --- DynamoDB table ---
  info "Creating DynamoDB table $DYNAMO_TABLE..."
  $AWS dynamodb describe-table --table-name "$DYNAMO_TABLE" &>/dev/null || \
    $AWS dynamodb create-table \
      --table-name "$DYNAMO_TABLE" \
      --attribute-definitions AttributeName=id,AttributeType=S \
      --key-schema AttributeName=id,KeyType=HASH \
      --billing-mode PAY_PER_REQUEST \
      --stream-specification StreamEnabled=true,StreamViewType=NEW_IMAGE \
      > /dev/null
  ok "DynamoDB table: $DYNAMO_TABLE"

  # --- SSM parameter (default off) ---
  info "Initialising SSM flag $SSM_FLAG_PATH..."
  $AWS ssm put-parameter --name "$SSM_FLAG_PATH" --value "false" \
    --type String --overwrite > /dev/null
  ok "SSM flag: $SSM_FLAG_PATH = false"

  # --- ECR repos ---
  info "Creating ECR repositories..."
  for repo in redrum-writer redrum-querier redrum-tracker; do
    $AWS ecr describe-repositories --repository-names "$repo" &>/dev/null || \
      $AWS ecr create-repository --repository-name "$repo" --image-scanning-configuration scanOnPush=true > /dev/null
    ok "$repo"
  done

  # --- Docker login ---
  info "Logging into ECR..."
  $AWS ecr get-login-password | docker login --username AWS --password-stdin "$ECR_BASE" > /dev/null
  ok "Docker authenticated"

  # --- Build & push ---
  for svc in writer querier tracker; do
    info "Building redrum-$svc..."
    docker build -t "redrum-$svc" "$SCRIPT_DIR/$svc" --platform linux/amd64 -q
    docker tag "redrum-$svc:latest" "$ECR_BASE/redrum-$svc:latest"
    info "Pushing redrum-$svc..."
    docker push "$ECR_BASE/redrum-$svc:latest" > /dev/null
    ok "redrum-$svc pushed"
  done

  # --- CloudWatch log group ---
  info "Creating CloudWatch log group $LOG_GROUP..."
  $AWS logs create-log-group --log-group-name "$LOG_GROUP" 2>/dev/null || true
  ok "Log group ready"

  # --- ECS task execution role ---
  info "Setting up ECS task execution role..."
  ROLE_NAME="redrumTaskExecutionRole"
  TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
  $AWS iam get-role --role-name "$ROLE_NAME" &>/dev/null || \
    $AWS iam create-role --role-name "$ROLE_NAME" --assume-role-policy-document "$TRUST" > /dev/null
  $AWS iam attach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy" 2>/dev/null || true
  EXECUTION_ROLE_ARN="arn:aws:iam::$ACCOUNT_ID:role/$ROLE_NAME"
  ok "Execution role: $EXECUTION_ROLE_ARN"

  # --- ECS task role (writer needs DynamoDB + SSM) ---
  info "Setting up ECS task role for writer..."
  TASK_ROLE_NAME="redrumTaskRole"
  $AWS iam get-role --role-name "$TASK_ROLE_NAME" &>/dev/null || \
    $AWS iam create-role --role-name "$TASK_ROLE_NAME" \
      --assume-role-policy-document "$TRUST" > /dev/null
  DYNAMO_SSM_POLICY=$(cat <<POLICY
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["dynamodb:BatchWriteItem", "dynamodb:PutItem"],
      "Resource": "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/$DYNAMO_TABLE"
    },
    {
      "Effect": "Allow",
      "Action": "ssm:GetParameter",
      "Resource": "arn:aws:ssm:$AWS_REGION:$ACCOUNT_ID:parameter$SSM_FLAG_PATH"
    }
  ]
}
POLICY
)
  $AWS iam put-role-policy --role-name "$TASK_ROLE_NAME" \
    --policy-name "redrumWriterPolicy" \
    --policy-document "$DYNAMO_SSM_POLICY" > /dev/null
  TASK_ROLE_ARN="arn:aws:iam::$ACCOUNT_ID:role/$TASK_ROLE_NAME"
  ok "Task role: $TASK_ROLE_ARN"

  # --- Lambda execution role ---
  info "Setting up Lambda execution role..."
  LAMBDA_ROLE_NAME="redrumLambdaRole"
  LAMBDA_TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
  $AWS iam get-role --role-name "$LAMBDA_ROLE_NAME" &>/dev/null || \
    $AWS iam create-role --role-name "$LAMBDA_ROLE_NAME" \
      --assume-role-policy-document "$LAMBDA_TRUST" > /dev/null
  $AWS iam attach-role-policy --role-name "$LAMBDA_ROLE_NAME" \
    --policy-arn "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole" 2>/dev/null || true

  TABLE_STREAM_ARN=$($AWS dynamodb describe-table --table-name "$DYNAMO_TABLE" \
    --query 'Table.LatestStreamArn' --output text)
  LAMBDA_POLICY=$(cat <<POLICY
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["dynamodb:UpdateItem"],
      "Resource": "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/$DYNAMO_TABLE"
    },
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetRecords", "dynamodb:GetShardIterator",
        "dynamodb:DescribeStream", "dynamodb:ListStreams"
      ],
      "Resource": "$TABLE_STREAM_ARN"
    }
  ]
}
POLICY
)
  $AWS iam put-role-policy --role-name "$LAMBDA_ROLE_NAME" \
    --policy-name "redrumLambdaPolicy" \
    --policy-document "$LAMBDA_POLICY" > /dev/null
  LAMBDA_ROLE_ARN="arn:aws:iam::$ACCOUNT_ID:role/$LAMBDA_ROLE_NAME"
  ok "Lambda role: $LAMBDA_ROLE_ARN"

  # --- Deploy tracker Lambda ---
  info "Deploying tracker Lambda..."
  TRACKER_IMAGE="$ECR_BASE/redrum-tracker:latest"

  EXISTING_LAMBDA=$($AWS lambda get-function --function-name "$LAMBDA_FUNCTION" \
    --query 'Configuration.FunctionName' --output text 2>/dev/null || true)

  if [[ -n "$EXISTING_LAMBDA" ]]; then
    $AWS lambda update-function-code \
      --function-name "$LAMBDA_FUNCTION" \
      --image-uri "$TRACKER_IMAGE" > /dev/null
    # wait for code update to finish before changing config
    for i in $(seq 1 20); do
      STATUS=$($AWS lambda get-function --function-name "$LAMBDA_FUNCTION" \
        --query 'Configuration.LastUpdateStatus' --output text 2>/dev/null || echo "InProgress")
      [[ "$STATUS" == "Successful" ]] && break
      sleep 5
    done
    $AWS lambda update-function-configuration \
      --function-name "$LAMBDA_FUNCTION" \
      --timeout "$TRACKER_TIMEOUT" > /dev/null
    ok "Updated Lambda: $LAMBDA_FUNCTION"
  else
    # IAM propagation can take ~15s — retry until the role is assumable
    info "Creating Lambda (retrying until IAM role is assumable)..."
    for attempt in $(seq 1 10); do
      if $AWS lambda create-function \
          --function-name "$LAMBDA_FUNCTION" \
          --package-type Image \
          --code ImageUri="$TRACKER_IMAGE" \
          --role "$LAMBDA_ROLE_ARN" \
          --timeout "$TRACKER_TIMEOUT" \
          --memory-size 256 \
          --environment "Variables={INDEX_HOST=$INDEX_HOST,PINECONE_API_KEY=$PINECONE_API_KEY,DYNAMO_TABLE=$DYNAMO_TABLE}" \
          > /dev/null 2>&1; then
        break
      fi
      echo "  attempt $attempt/10 — IAM not ready yet, waiting 10s..."
      sleep 10
    done
    ok "Created Lambda: $LAMBDA_FUNCTION"

    # wait for Lambda to be active before adding trigger
    info "Waiting for Lambda to become active..."
    for i in $(seq 1 20); do
      STATE=$($AWS lambda get-function --function-name "$LAMBDA_FUNCTION" \
        --query 'Configuration.State' --output text 2>/dev/null || echo "Pending")
      [[ "$STATE" == "Active" ]] && break
      echo "  state=$STATE, waiting 5s..."
      sleep 5
    done

    # wire DynamoDB Stream → Lambda
    $AWS lambda create-event-source-mapping \
      --function-name "$LAMBDA_FUNCTION" \
      --event-source-arn "$TABLE_STREAM_ARN" \
      --starting-position LATEST \
      --batch-size 10 \
      --bisect-batch-on-function-error \
      > /dev/null
    ok "DynamoDB Stream → Lambda trigger created"
  fi

  # --- ECS cluster ---
  info "Creating ECS cluster $CLUSTER..."
  $AWS ecs create-cluster --cluster-name "$CLUSTER" > /dev/null 2>&1 || true
  ok "Cluster ready"

  # --- networking: use default VPC ---
  info "Resolving default VPC networking..."
  DEFAULT_VPC=$($AWS ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
  SUBNETS=$($AWS ec2 describe-subnets \
    --filters "Name=vpc-id,Values=$DEFAULT_VPC" \
    --query 'Subnets[*].SubnetId' --output text | tr '\t' ',')
  ok "VPC=$DEFAULT_VPC subnets=$SUBNETS"

  # security group
  SG_NAME="redrum-tasks"
  SG_ID=$($AWS ec2 describe-security-groups \
    --filters "Name=group-name,Values=$SG_NAME" "Name=vpc-id,Values=$DEFAULT_VPC" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")
  if [[ "$SG_ID" == "None" || -z "$SG_ID" ]]; then
    SG_ID=$($AWS ec2 create-security-group \
      --group-name "$SG_NAME" \
      --description "redrum ECS tasks - outbound only" \
      --vpc-id "$DEFAULT_VPC" \
      --query 'GroupId' --output text)
    # allow all outbound (HTTPS to Pinecone), no inbound needed
    $AWS ec2 authorize-security-group-egress \
      --group-id "$SG_ID" \
      --protocol -1 --port -1 --cidr 0.0.0.0/0 2>/dev/null || true
  fi
  ok "Security group: $SG_ID"

  # --- register task definitions ---
  info "Registering ECS task definitions..."

  COMMON_ENV='[
    {"name":"INDEX_HOST",          "value":"'"$INDEX_HOST"'"},
    {"name":"PINECONE_API_KEY",    "value":"'"$PINECONE_API_KEY"'"},
    {"name":"VECTOR_DIM",          "value":"'"$VECTOR_DIM"'"},
    {"name":"WRITE_COUNT",         "value":"'"$WRITE_COUNT"'"},
    {"name":"QUERY_COUNT",         "value":"'"$QUERY_COUNT"'"},
    {"name":"TOP_K",               "value":"'"$TOP_K"'"},
    {"name":"MIN_SLEEP_SECONDS",   "value":"'"$MIN_SLEEP_SECONDS"'"},
    {"name":"MAX_SLEEP_SECONDS",   "value":"'"$MAX_SLEEP_SECONDS"'"}
  ]'

  WRITER_ENV='[
    {"name":"INDEX_HOST",          "value":"'"$INDEX_HOST"'"},
    {"name":"PINECONE_API_KEY",    "value":"'"$PINECONE_API_KEY"'"},
    {"name":"AWS_REGION",          "value":"'"$AWS_REGION"'"},
    {"name":"VECTOR_DIM",          "value":"'"$VECTOR_DIM"'"},
    {"name":"WRITE_COUNT",         "value":"'"$WRITE_COUNT"'"},
    {"name":"MIN_SLEEP_SECONDS",   "value":"'"$MIN_SLEEP_SECONDS"'"},
    {"name":"MAX_SLEEP_SECONDS",   "value":"'"$MAX_SLEEP_SECONDS"'"},
    {"name":"DYNAMO_TABLE",        "value":"'"$DYNAMO_TABLE"'"},
    {"name":"SSM_FLAG_PATH",       "value":"'"$SSM_FLAG_PATH"'"}
  ]'

  for svc in writer querier; do
    if [[ "$svc" == "writer" ]]; then
      ENV_JSON="$WRITER_ENV"
      TASK_ROLE_FIELD='"taskRoleArn": "'"$TASK_ROLE_ARN"'",'
    else
      ENV_JSON="$COMMON_ENV"
      TASK_ROLE_FIELD=""
    fi

    TASK_DEF=$(cat <<EOF
{
  "family": "redrum-$svc",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "$EXECUTION_ROLE_ARN",
  $TASK_ROLE_FIELD
  "containerDefinitions": [{
    "name": "redrum-$svc",
    "image": "$ECR_BASE/redrum-$svc:latest",
    "essential": true,
    "environment": $ENV_JSON,
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "$LOG_GROUP",
        "awslogs-region": "$AWS_REGION",
        "awslogs-stream-prefix": "$svc"
      }
    }
  }]
}
EOF
)
    $AWS ecs register-task-definition --cli-input-json "$TASK_DEF" > /dev/null
    ok "Task definition: redrum-$svc"
  done

  # --- create/update ECS services ---
  info "Starting ECS services..."
  NET_CONFIG="awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG_ID],assignPublicIp=ENABLED}"

  for svc in writer querier; do
    if [[ "$svc" == "writer" ]]; then COUNT="$WRITER_COUNT"; else COUNT="$QUERIER_COUNT"; fi
    EXISTS=$($AWS ecs describe-services --cluster "$CLUSTER" --services "redrum-$svc" \
      --query 'services[?status!=`INACTIVE`].serviceName' --output text 2>/dev/null || true)
    if [[ -n "$EXISTS" ]]; then
      $AWS ecs update-service \
        --cluster "$CLUSTER" \
        --service "redrum-$svc" \
        --task-definition "redrum-$svc" \
        --desired-count "$COUNT" > /dev/null
      ok "Updated service: redrum-$svc (count=$COUNT)"
    else
      $AWS ecs create-service \
        --cluster "$CLUSTER" \
        --service-name "redrum-$svc" \
        --task-definition "redrum-$svc" \
        --desired-count "$COUNT" \
        --launch-type FARGATE \
        --network-configuration "$NET_CONFIG" > /dev/null
      ok "Created service: redrum-$svc (count=$COUNT)"
    fi
  done

  echo ""
  ok "Deploy complete! Tasks are starting (may take ~30s)."
  echo ""
  echo "  Monitor:  ./redrum.sh logs"
  echo "  Status:   ./redrum.sh status"
  echo "  Kill:     ./redrum.sh kill"
}

# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
cmd_status() {
  info "ECS service status (cluster: $CLUSTER)"
  echo ""
  for svc in writer querier; do
    echo "--- redrum-$svc ---"
    $AWS ecs describe-services \
      --cluster "$CLUSTER" \
      --services "redrum-$svc" \
      --query 'services[0].{Status:status,Running:runningCount,Desired:desiredCount,Pending:pendingCount,LastDeployment:deployments[0].updatedAt}' \
      --output table 2>/dev/null || echo "(not found)"
    echo ""
  done
}

# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------
cmd_logs() {
  TARGET="${1:-all}"
  info "Tailing logs from CloudWatch (Ctrl-C to stop)..."

  if [[ "$TARGET" == "writer" ]]; then
    $AWS logs tail "$LOG_GROUP" --log-stream-name-prefix "writer" --follow
  elif [[ "$TARGET" == "querier" ]]; then
    $AWS logs tail "$LOG_GROUP" --log-stream-name-prefix "querier" --follow
  else
    # tail both — interleaved
    $AWS logs tail "$LOG_GROUP" --follow
  fi
}

# ---------------------------------------------------------------------------
# kill
# ---------------------------------------------------------------------------
cmd_kill() {
  info "Stopping services (setting desired count to 0)..."
  for svc in writer querier; do
    $AWS ecs update-service --cluster "$CLUSTER" --service "redrum-$svc" --desired-count 0 > /dev/null \
      && ok "Stopped: redrum-$svc" \
      || echo "  (redrum-$svc not found, skipping)"
  done
  ok "Both services stopped. Run './redrum.sh deploy' to restart."
}

# ---------------------------------------------------------------------------
# destroy — full teardown
# ---------------------------------------------------------------------------
cmd_destroy() {
  info "Destroying all redrum AWS resources..."

  # stop and delete services
  for svc in writer querier; do
    $AWS ecs update-service --cluster "$CLUSTER" --service "redrum-$svc" --desired-count 0 > /dev/null 2>&1 || true
    $AWS ecs delete-service --cluster "$CLUSTER" --service "redrum-$svc" --force > /dev/null 2>&1 || true
    ok "Deleted service: redrum-$svc"
  done

  # delete cluster
  $AWS ecs delete-cluster --cluster "$CLUSTER" > /dev/null 2>&1 && ok "Deleted cluster: $CLUSTER" || true

  # delete ECR repos
  for repo in redrum-writer redrum-querier; do
    $AWS ecr delete-repository --repository-name "$repo" --force > /dev/null 2>&1 && ok "Deleted ECR: $repo" || true
  done

  # delete log group
  $AWS logs delete-log-group --log-group-name "$LOG_GROUP" > /dev/null 2>&1 && ok "Deleted log group: $LOG_GROUP" || true

  # delete Lambda + event source mapping
  $AWS lambda delete-function --function-name "$LAMBDA_FUNCTION" > /dev/null 2>&1 && ok "Deleted Lambda: $LAMBDA_FUNCTION" || true

  # delete DynamoDB table
  $AWS dynamodb delete-table --table-name "$DYNAMO_TABLE" > /dev/null 2>&1 && ok "Deleted DynamoDB table: $DYNAMO_TABLE" || true

  # delete SSM parameter
  $AWS ssm delete-parameter --name "$SSM_FLAG_PATH" > /dev/null 2>&1 && ok "Deleted SSM: $SSM_FLAG_PATH" || true

  # delete ECR tracker repo
  $AWS ecr delete-repository --repository-name "redrum-tracker" --force > /dev/null 2>&1 && ok "Deleted ECR: redrum-tracker" || true

  ok "All redrum resources destroyed."
}

# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------
case "${1:-}" in
  deploy)  cmd_deploy ;;
  status)  cmd_status ;;
  logs)    cmd_logs "${2:-all}" ;;
  kill)    cmd_kill ;;
  destroy) cmd_destroy ;;
  *)
    echo "Usage: $0 {deploy|status|logs [writer|querier]|kill|destroy}"
    exit 1
    ;;
esac
