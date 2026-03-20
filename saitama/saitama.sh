#!/usr/bin/env bash
#
# saitama.sh — enterprise ECS on EC2 deploy/manage script for the Pinecone gRPC query service
#
# Usage:
#   ./saitama.sh deploy        Build image, push to ECR, provision all AWS infra, start service
#   ./saitama.sh status        Show ECS service health and ALB target status
#   ./saitama.sh logs          Tail live logs (Ctrl-C to stop)
#   ./saitama.sh scale N       Set desired task count to N
#   ./saitama.sh kill          Stop service (desired count -> 0)
#   ./saitama.sh destroy       Tear down all AWS resources
#
set -euo pipefail

# load .env if present
if [[ -f "$(dirname "${BASH_SOURCE[0]}")/.env" ]]; then
  set -o allexport
  source "$(dirname "${BASH_SOURCE[0]}")/.env"
  set +o allexport
fi

# ---------------------------------------------------------------------------
# Configuration — override any of these with environment variables or .env
# ---------------------------------------------------------------------------
AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_PROFILE="${AWS_PROFILE:-}"

CLUSTER="${CLUSTER:-saitama}"
SERVICE="${SERVICE:-saitama}"
ECR_REPO="${ECR_REPO:-saitama}"
LOG_GROUP="${LOG_GROUP:-/saitama}"

PINECONE_API_KEY="${PINECONE_API_KEY:-}"   # required
INDEX_HOST="${INDEX_HOST:-}"               # required
VECTOR_DIM="${VECTOR_DIM:-0}"
THREAD_POOL_SIZE="${THREAD_POOL_SIZE:-100}"

INSTANCE_TYPE="${INSTANCE_TYPE:-c6in.large}"
ASG_MIN="${ASG_MIN:-3}"                    # 3 c6in.large = ~6 tasks at 1 vCPU each
ASG_MAX="${ASG_MAX:-12}"
TASK_COUNT="${TASK_COUNT:-6}"              # desired tasks
TASK_CPU="${TASK_CPU:-1024}"               # millicores (1 vCPU)
TASK_MEMORY="${TASK_MEMORY:-2048}"         # MB
CAPACITY_TARGET="${CAPACITY_TARGET:-80}"   # % utilization target for managed scaling
INSTANCE_WARMUP="${INSTANCE_WARMUP:-90}"   # seconds

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
AWS="aws --region $AWS_REGION --profile $AWS_PROFILE"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; RESET='\033[0m'

info()  { echo -e "${BLUE}[saitama] $*${RESET}"; }
ok()    { echo -e "${GREEN}[ok] $*${RESET}"; }
warn()  { echo -e "${YELLOW}[warn] $*${RESET}"; }
err()   { echo -e "${RED}[err] $*${RESET}" >&2; exit 1; }

account_id() { $AWS sts get-caller-identity --query Account --output text; }
ecr_base()   { echo "$(account_id).dkr.ecr.$AWS_REGION.amazonaws.com"; }

# ---------------------------------------------------------------------------
# deploy
# ---------------------------------------------------------------------------
cmd_deploy() {
  [[ -z "$PINECONE_API_KEY" ]] && err "PINECONE_API_KEY is not set."
  [[ -z "$INDEX_HOST" ]]       && err "INDEX_HOST is not set."

  ACCOUNT_ID=$(account_id)
  ECR_BASE="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

  # --- ECR repo ---
  info "Creating ECR repository..."
  $AWS ecr describe-repositories --repository-names "$ECR_REPO" &>/dev/null || \
    $AWS ecr create-repository --repository-name "$ECR_REPO" \
      --image-scanning-configuration scanOnPush=true > /dev/null
  ok "ECR: $ECR_BASE/$ECR_REPO"

  # --- Docker login + build + push ---
  info "Authenticating Docker with ECR..."
  $AWS ecr get-login-password | docker login --username AWS --password-stdin "$ECR_BASE" > /dev/null

  info "Building saitama image (linux/amd64)..."
  docker build -t saitama "$SCRIPT_DIR" --platform linux/amd64 -q
  docker tag saitama:latest "$ECR_BASE/$ECR_REPO:latest"
  info "Pushing image to ECR..."
  docker push "$ECR_BASE/$ECR_REPO:latest" > /dev/null
  ok "Image pushed: $ECR_BASE/$ECR_REPO:latest"

  # --- CloudWatch log group ---
  info "Creating CloudWatch log group $LOG_GROUP..."
  $AWS logs create-log-group --log-group-name "$LOG_GROUP" 2>/dev/null || true
  $AWS logs put-retention-policy --log-group-name "$LOG_GROUP" --retention-in-days 14 2>/dev/null || true
  ok "Log group ready"

  # --- IAM: ECS task execution role ---
  info "Setting up ECS task execution role..."
  EXEC_ROLE="saitamaTaskExecutionRole"
  TRUST_TASK='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
  $AWS iam get-role --role-name "$EXEC_ROLE" &>/dev/null || \
    $AWS iam create-role --role-name "$EXEC_ROLE" \
      --assume-role-policy-document "$TRUST_TASK" > /dev/null
  $AWS iam attach-role-policy --role-name "$EXEC_ROLE" \
    --policy-arn "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy" 2>/dev/null || true
  EXEC_ROLE_ARN="arn:aws:iam::$ACCOUNT_ID:role/$EXEC_ROLE"
  ok "Task execution role: $EXEC_ROLE_ARN"

  # --- IAM: EC2 instance profile for ECS ---
  info "Setting up EC2 instance profile..."
  INSTANCE_ROLE="saitamaEC2Role"
  TRUST_EC2='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
  $AWS iam get-role --role-name "$INSTANCE_ROLE" &>/dev/null || \
    $AWS iam create-role --role-name "$INSTANCE_ROLE" \
      --assume-role-policy-document "$TRUST_EC2" > /dev/null
  $AWS iam attach-role-policy --role-name "$INSTANCE_ROLE" \
    --policy-arn "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role" 2>/dev/null || true
  $AWS iam attach-role-policy --role-name "$INSTANCE_ROLE" \
    --policy-arn "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" 2>/dev/null || true

  INSTANCE_PROFILE="saitamaInstanceProfile"
  $AWS iam get-instance-profile --instance-profile-name "$INSTANCE_PROFILE" &>/dev/null || {
    $AWS iam create-instance-profile --instance-profile-name "$INSTANCE_PROFILE" > /dev/null
    $AWS iam add-role-to-instance-profile \
      --instance-profile-name "$INSTANCE_PROFILE" \
      --role-name "$INSTANCE_ROLE" > /dev/null
    # short wait for IAM propagation
    sleep 5
  }
  ok "Instance profile: $INSTANCE_PROFILE"

  # --- ECS cluster ---
  info "Creating ECS cluster $CLUSTER..."
  $AWS ecs create-cluster --cluster-name "$CLUSTER" \
    --settings name=containerInsights,value=enabled > /dev/null 2>&1 || true
  ok "Cluster: $CLUSTER"

  # --- Networking: default VPC ---
  info "Resolving default VPC networking..."
  DEFAULT_VPC=$($AWS ec2 describe-vpcs \
    --filters Name=isDefault,Values=true \
    --query 'Vpcs[0].VpcId' --output text)

  SUBNET_IDS_RAW=$($AWS ec2 describe-subnets \
    --filters "Name=vpc-id,Values=$DEFAULT_VPC" \
    --query 'Subnets[*].SubnetId' --output text)
  SUBNETS_CSV=$(echo "$SUBNET_IDS_RAW" | tr '\t' ',')

  # Pick the first 3 subnets for spread placement
  S1=$(echo "$SUBNET_IDS_RAW" | awk '{print $1}')
  S2=$(echo "$SUBNET_IDS_RAW" | awk '{print $2}')
  S3=$(echo "$SUBNET_IDS_RAW" | awk '{print $3}')
  ok "VPC=$DEFAULT_VPC  subnets=$SUBNETS_CSV"

  # --- Security groups ---
  info "Setting up security groups..."

  # ALB SG
  ALB_SG_NAME="saitama-alb"
  ALB_SG_ID=$($AWS ec2 describe-security-groups \
    --filters "Name=group-name,Values=$ALB_SG_NAME" "Name=vpc-id,Values=$DEFAULT_VPC" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")
  if [[ "$ALB_SG_ID" == "None" || -z "$ALB_SG_ID" ]]; then
    ALB_SG_ID=$($AWS ec2 create-security-group \
      --group-name "$ALB_SG_NAME" \
      --description "saitama ALB - inbound HTTP" \
      --vpc-id "$DEFAULT_VPC" \
      --query 'GroupId' --output text)
    $AWS ec2 authorize-security-group-ingress \
      --group-id "$ALB_SG_ID" \
      --protocol tcp --port 80 --cidr 0.0.0.0/0 2>/dev/null || true
  fi
  ok "ALB SG: $ALB_SG_ID"

  # Task SG
  TASK_SG_NAME="saitama-tasks"
  TASK_SG_ID=$($AWS ec2 describe-security-groups \
    --filters "Name=group-name,Values=$TASK_SG_NAME" "Name=vpc-id,Values=$DEFAULT_VPC" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")
  if [[ "$TASK_SG_ID" == "None" || -z "$TASK_SG_ID" ]]; then
    TASK_SG_ID=$($AWS ec2 create-security-group \
      --group-name "$TASK_SG_NAME" \
      --description "saitama ECS tasks - inbound from ALB" \
      --vpc-id "$DEFAULT_VPC" \
      --query 'GroupId' --output text)
    # inbound 8000 from ALB SG
    $AWS ec2 authorize-security-group-ingress \
      --group-id "$TASK_SG_ID" \
      --protocol tcp --port 8000 \
      --source-group "$ALB_SG_ID" 2>/dev/null || true
    # outbound: HTTPS (443) for Pinecone gRPC + SSM
    $AWS ec2 authorize-security-group-egress \
      --group-id "$TASK_SG_ID" \
      --protocol tcp --port 443 --cidr 0.0.0.0/0 2>/dev/null || true
  fi
  ok "Task SG: $TASK_SG_ID"

  # EC2 instance SG
  EC2_SG_NAME="saitama-ec2"
  EC2_SG_ID=$($AWS ec2 describe-security-groups \
    --filters "Name=group-name,Values=$EC2_SG_NAME" "Name=vpc-id,Values=$DEFAULT_VPC" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")
  if [[ "$EC2_SG_ID" == "None" || -z "$EC2_SG_ID" ]]; then
    EC2_SG_ID=$($AWS ec2 create-security-group \
      --group-name "$EC2_SG_NAME" \
      --description "saitama EC2 hosts - ephemeral ports from ALB" \
      --vpc-id "$DEFAULT_VPC" \
      --query 'GroupId' --output text)
    # inbound ephemeral range from ALB SG (awsvpc tasks use random host ports mapped to 8000)
    $AWS ec2 authorize-security-group-ingress \
      --group-id "$EC2_SG_ID" \
      --protocol tcp --port 1024-65535 \
      --source-group "$ALB_SG_ID" 2>/dev/null || true
    # outbound all
    $AWS ec2 authorize-security-group-egress \
      --group-id "$EC2_SG_ID" \
      --protocol -1 --port -1 --cidr 0.0.0.0/0 2>/dev/null || true
  fi
  ok "EC2 host SG: $EC2_SG_ID"

  # --- ECS-optimized AMI (latest Amazon Linux 2) ---
  info "Fetching latest ECS-optimized AMI..."
  ECS_AMI=$($AWS ssm get-parameter \
    --name /aws/service/ecs/optimized-ami/amazon-linux-2/recommended/image_id \
    --query 'Parameter.Value' --output text)
  ok "AMI: $ECS_AMI"

  # --- EC2 Launch Template ---
  info "Creating EC2 launch template..."
  USER_DATA=$(cat <<EOF | base64
#!/bin/bash
echo ECS_CLUSTER=$CLUSTER >> /etc/ecs/ecs.config
echo ECS_ENABLE_CONTAINER_METADATA=true >> /etc/ecs/ecs.config
echo ECS_ENABLE_SPOT_INSTANCE_DRAINING=false >> /etc/ecs/ecs.config
EOF
)

  LT_NAME="saitama-lt"
  # Delete existing LT so we can recreate with fresh config
  EXISTING_LT=$($AWS ec2 describe-launch-templates \
    --filters "Name=launch-template-name,Values=$LT_NAME" \
    --query 'LaunchTemplates[0].LaunchTemplateId' --output text 2>/dev/null || echo "None")
  if [[ "$EXISTING_LT" != "None" && -n "$EXISTING_LT" ]]; then
    $AWS ec2 delete-launch-template --launch-template-id "$EXISTING_LT" > /dev/null 2>&1 || true
  fi

  LT_DATA=$(cat <<EOF
{
  "ImageId": "$ECS_AMI",
  "InstanceType": "$INSTANCE_TYPE",
  "IamInstanceProfile": {"Name": "$INSTANCE_PROFILE"},
  "SecurityGroupIds": ["$EC2_SG_ID"],
  "UserData": "$USER_DATA",
  "TagSpecifications": [{
    "ResourceType": "instance",
    "Tags": [{"Key": "Name", "Value": "saitama-ec2"}]
  }],
  "MetadataOptions": {
    "HttpTokens": "required",
    "HttpPutResponseHopLimit": 2
  }
}
EOF
)

  LT_ID=$($AWS ec2 create-launch-template \
    --launch-template-name "$LT_NAME" \
    --launch-template-data "$LT_DATA" \
    --query 'LaunchTemplate.LaunchTemplateId' --output text)
  ok "Launch template: $LT_ID"

  # --- Auto Scaling Group ---
  info "Creating Auto Scaling Group..."
  ASG_NAME="saitama-asg"

  # Delete existing ASG if present
  EXISTING_ASG=$($AWS autoscaling describe-auto-scaling-groups \
    --auto-scaling-group-names "$ASG_NAME" \
    --query 'AutoScalingGroups[0].AutoScalingGroupName' --output text 2>/dev/null || echo "None")
  if [[ "$EXISTING_ASG" != "None" && "$EXISTING_ASG" != "" ]]; then
    warn "ASG $ASG_NAME already exists, skipping creation"
  else
    $AWS autoscaling create-auto-scaling-group \
      --auto-scaling-group-name "$ASG_NAME" \
      --launch-template "LaunchTemplateId=$LT_ID,Version=\$Latest" \
      --min-size "$ASG_MIN" \
      --max-size "$ASG_MAX" \
      --desired-capacity "$ASG_MIN" \
      --vpc-zone-identifier "$SUBNETS_CSV" \
      --new-instances-protected-from-scale-in \
      --tags "Key=Name,Value=saitama-ec2,PropagateAtLaunch=true" > /dev/null
    ok "ASG: $ASG_NAME (min=$ASG_MIN max=$ASG_MAX)"
  fi

  # --- ECS Capacity Provider ---
  info "Creating ECS Capacity Provider..."
  CP_NAME="saitama-cp"

  $AWS ecs create-capacity-provider \
    --name "$CP_NAME" \
    --auto-scaling-group-provider \
      "autoScalingGroupArn=arn:aws:autoscaling:$AWS_REGION:$ACCOUNT_ID:autoScalingGroup:*:autoScalingGroupName/$ASG_NAME,managedScaling={status=ENABLED,targetCapacity=$CAPACITY_TARGET,minimumScalingStepSize=1,maximumScalingStepSize=3,instanceWarmupPeriod=$INSTANCE_WARMUP},managedTerminationProtection=ENABLED,managedDraining=ENABLED" \
    > /dev/null 2>&1 || warn "Capacity provider $CP_NAME may already exist — continuing"

  # Associate capacity provider with the cluster
  $AWS ecs put-cluster-capacity-providers \
    --cluster "$CLUSTER" \
    --capacity-providers "$CP_NAME" \
    --default-capacity-provider-strategy \
      "capacityProvider=$CP_NAME,weight=1,base=1" > /dev/null
  ok "Capacity provider: $CP_NAME (target=$CAPACITY_TARGET%, warmup=${INSTANCE_WARMUP}s)"

  # --- ALB ---
  info "Creating Application Load Balancer..."
  ALB_NAME="saitama-alb"

  EXISTING_ALB=$($AWS elbv2 describe-load-balancers \
    --names "$ALB_NAME" \
    --query 'LoadBalancers[0].LoadBalancerArn' --output text 2>/dev/null || echo "None")

  if [[ "$EXISTING_ALB" != "None" && -n "$EXISTING_ALB" ]]; then
    ALB_ARN="$EXISTING_ALB"
    warn "ALB $ALB_NAME already exists"
  else
    ALB_ARN=$($AWS elbv2 create-load-balancer \
      --name "$ALB_NAME" \
      --subnets $S1 $S2 $S3 \
      --security-groups "$ALB_SG_ID" \
      --scheme internet-facing \
      --type application \
      --ip-address-type ipv4 \
      --query 'LoadBalancers[0].LoadBalancerArn' --output text)
  fi
  ALB_DNS=$($AWS elbv2 describe-load-balancers \
    --load-balancer-arns "$ALB_ARN" \
    --query 'LoadBalancers[0].DNSName' --output text)
  ok "ALB: $ALB_DNS"

  # --- Target Group ---
  info "Creating target group..."
  TG_NAME="saitama-tg"

  EXISTING_TG=$($AWS elbv2 describe-target-groups \
    --names "$TG_NAME" \
    --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null || echo "None")

  if [[ "$EXISTING_TG" != "None" && -n "$EXISTING_TG" ]]; then
    TG_ARN="$EXISTING_TG"
    warn "Target group $TG_NAME already exists"
  else
    TG_ARN=$($AWS elbv2 create-target-group \
      --name "$TG_NAME" \
      --protocol HTTP \
      --port 8000 \
      --vpc-id "$DEFAULT_VPC" \
      --target-type ip \
      --health-check-protocol HTTP \
      --health-check-path /healthz \
      --health-check-interval-seconds 15 \
      --health-check-timeout-seconds 5 \
      --healthy-threshold-count 2 \
      --unhealthy-threshold-count 3 \
      --load-balancing-algorithm-type least_outstanding_requests \
      --query 'TargetGroups[0].TargetGroupArn' --output text)
  fi
  ok "Target group: $TG_ARN"

  # Enable target group stickiness off, deregistration delay 30s (fast draining)
  $AWS elbv2 modify-target-group-attributes \
    --target-group-arn "$TG_ARN" \
    --attributes "Key=deregistration_delay.timeout_seconds,Value=30" > /dev/null

  # --- ALB Listener ---
  info "Creating ALB listener..."
  EXISTING_LISTENER=$($AWS elbv2 describe-listeners \
    --load-balancer-arn "$ALB_ARN" \
    --query 'Listeners[?Port==`80`].ListenerArn' --output text 2>/dev/null || echo "")
  if [[ -z "$EXISTING_LISTENER" ]]; then
    $AWS elbv2 create-listener \
      --load-balancer-arn "$ALB_ARN" \
      --protocol HTTP \
      --port 80 \
      --default-actions "Type=forward,TargetGroupArn=$TG_ARN" > /dev/null
    ok "Listener: HTTP:80 -> $TG_NAME"
  else
    warn "Listener already exists"
  fi

  # --- ECS task definition ---
  info "Registering ECS task definition..."
  TASK_DEF=$(cat <<EOF
{
  "family": "saitama",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["EC2"],
  "cpu": "$TASK_CPU",
  "memory": "$TASK_MEMORY",
  "executionRoleArn": "$EXEC_ROLE_ARN",
  "containerDefinitions": [{
    "name": "saitama",
    "image": "$ECR_BASE/$ECR_REPO:latest",
    "essential": true,
    "portMappings": [{"containerPort": 8000, "protocol": "tcp"}],
    "environment": [
      {"name": "PINECONE_API_KEY",    "value": "$PINECONE_API_KEY"},
      {"name": "INDEX_HOST",          "value": "$INDEX_HOST"},
      {"name": "VECTOR_DIM",          "value": "$VECTOR_DIM"},
      {"name": "THREAD_POOL_SIZE",    "value": "$THREAD_POOL_SIZE"}
    ],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group":         "$LOG_GROUP",
        "awslogs-region":        "$AWS_REGION",
        "awslogs-stream-prefix": "ecs"
      }
    },
    "healthCheck": {
      "command":     ["CMD-SHELL", "curl -sf http://localhost:8000/healthz || exit 1"],
      "interval":    15,
      "timeout":     5,
      "retries":     3,
      "startPeriod": 30
    },
    "ulimits": [{"name": "nofile", "softLimit": 65536, "hardLimit": 65536}]
  }]
}
EOF
)
  $AWS ecs register-task-definition --cli-input-json "$TASK_DEF" > /dev/null
  ok "Task definition: saitama"

  # --- ECS service ---
  info "Creating/updating ECS service..."

  NET_CONFIG="{\"awsvpcConfiguration\":{\"subnets\":[\"$S1\",\"$S2\",\"$S3\"],\"securityGroups\":[\"$TASK_SG_ID\"],\"assignPublicIp\":\"ENABLED\"}}"
  LB_CONFIG="[{\"targetGroupArn\":\"$TG_ARN\",\"containerName\":\"saitama\",\"containerPort\":8000}]"

  EXISTS=$($AWS ecs describe-services \
    --cluster "$CLUSTER" --services "$SERVICE" \
    --query 'services[?status!=`INACTIVE`].serviceName' --output text 2>/dev/null || true)

  if [[ -n "$EXISTS" ]]; then
    $AWS ecs update-service \
      --cluster "$CLUSTER" \
      --service "$SERVICE" \
      --task-definition saitama \
      --desired-count "$TASK_COUNT" \
      --network-configuration "$NET_CONFIG" \
      --capacity-provider-strategy "capacityProvider=$CP_NAME,weight=1,base=1" \
      --deployment-configuration "minimumHealthyPercent=100,maximumPercent=200" > /dev/null
    ok "Service updated: $SERVICE (count=$TASK_COUNT)"
  else
    $AWS ecs create-service \
      --cluster "$CLUSTER" \
      --service-name "$SERVICE" \
      --task-definition saitama \
      --desired-count "$TASK_COUNT" \
      --capacity-provider-strategy "capacityProvider=$CP_NAME,weight=1,base=1" \
      --network-configuration "$NET_CONFIG" \
      --load-balancers "$LB_CONFIG" \
      --health-check-grace-period-seconds 60 \
      --deployment-configuration "minimumHealthyPercent=100,maximumPercent=200" \
      --scheduling-strategy REPLICA \
      --placement-strategy "type=spread,field=attribute:ecs.availability-zone" > /dev/null
    ok "Service created: $SERVICE (count=$TASK_COUNT)"
  fi

  echo ""
  ok "Deploy complete!"
  echo ""
  echo "  Endpoint: http://$ALB_DNS"
  echo "  Health:   http://$ALB_DNS/healthz"
  echo "  Docs:     http://$ALB_DNS/docs"
  echo ""
  echo "  Monitor:  ./saitama.sh status"
  echo "  Logs:     ./saitama.sh logs"
  echo "  Scale:    ./saitama.sh scale 12"
  echo ""
  warn "Tasks are starting — allow ~2 min for instances to register and pass health checks."
}

# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
cmd_status() {
  info "ECS service status (cluster: $CLUSTER)"
  echo ""

  $AWS ecs describe-services \
    --cluster "$CLUSTER" \
    --services "$SERVICE" \
    --query 'services[0].{Status:status,Running:runningCount,Desired:desiredCount,Pending:pendingCount,TaskDef:taskDefinition}' \
    --output table 2>/dev/null || echo "(service not found)"

  echo ""
  info "ALB target health"
  TG_ARN=$($AWS elbv2 describe-target-groups \
    --names "saitama-tg" \
    --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null || echo "")
  if [[ -n "$TG_ARN" ]]; then
    $AWS elbv2 describe-target-health \
      --target-group-arn "$TG_ARN" \
      --query 'TargetHealthDescriptions[*].{IP:Target.Id,Port:Target.Port,State:TargetHealth.State,Reason:TargetHealth.Reason}' \
      --output table
  else
    echo "(target group not found)"
  fi

  echo ""
  info "ALB DNS"
  $AWS elbv2 describe-load-balancers \
    --names "saitama-alb" \
    --query 'LoadBalancers[0].DNSName' --output text 2>/dev/null || echo "(ALB not found)"
}

# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------
cmd_logs() {
  info "Tailing CloudWatch logs from $LOG_GROUP (Ctrl-C to stop)..."
  $AWS logs tail "$LOG_GROUP" --follow
}

# ---------------------------------------------------------------------------
# scale
# ---------------------------------------------------------------------------
cmd_scale() {
  COUNT="${1:-}"
  [[ -z "$COUNT" ]] && err "Usage: $0 scale <count>"
  info "Scaling $SERVICE to $COUNT tasks..."
  $AWS ecs update-service \
    --cluster "$CLUSTER" \
    --service "$SERVICE" \
    --desired-count "$COUNT" > /dev/null
  ok "Desired count set to $COUNT"
}

# ---------------------------------------------------------------------------
# kill
# ---------------------------------------------------------------------------
cmd_kill() {
  info "Stopping service (desired count -> 0)..."
  $AWS ecs update-service \
    --cluster "$CLUSTER" \
    --service "$SERVICE" \
    --desired-count 0 > /dev/null
  ok "Service stopped. Run './saitama.sh deploy' to restart."
}

# ---------------------------------------------------------------------------
# destroy — full teardown
# ---------------------------------------------------------------------------
cmd_destroy() {
  info "Destroying all saitama AWS resources..."

  # 1. Stop and delete ECS service
  $AWS ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --desired-count 0 > /dev/null 2>&1 || true
  $AWS ecs delete-service --cluster "$CLUSTER" --service "$SERVICE" --force > /dev/null 2>&1 \
    && ok "Deleted ECS service: $SERVICE" || true

  # 2. Remove capacity provider from cluster
  $AWS ecs put-cluster-capacity-providers \
    --cluster "$CLUSTER" \
    --capacity-providers "" \
    --default-capacity-provider-strategy "" > /dev/null 2>&1 || true

  # 3. Delete ECS cluster
  $AWS ecs delete-cluster --cluster "$CLUSTER" > /dev/null 2>&1 \
    && ok "Deleted ECS cluster: $CLUSTER" || true

  # 4. Delete capacity provider (need to wait for ASG to be detached first)
  $AWS ecs delete-capacity-provider --capacity-provider "saitama-cp" > /dev/null 2>&1 \
    && ok "Deleted capacity provider: saitama-cp" || true

  # 5. Delete ASG
  $AWS autoscaling delete-auto-scaling-group \
    --auto-scaling-group-name "saitama-asg" \
    --force-delete > /dev/null 2>&1 \
    && ok "Deleted ASG: saitama-asg" || true

  # 6. Delete launch template
  LT_ID=$($AWS ec2 describe-launch-templates \
    --filters "Name=launch-template-name,Values=saitama-lt" \
    --query 'LaunchTemplates[0].LaunchTemplateId' --output text 2>/dev/null || echo "")
  if [[ -n "$LT_ID" && "$LT_ID" != "None" ]]; then
    $AWS ec2 delete-launch-template --launch-template-id "$LT_ID" > /dev/null 2>&1 \
      && ok "Deleted launch template: $LT_ID" || true
  fi

  # 7. Delete ALB, listener, target group
  ALB_ARN=$($AWS elbv2 describe-load-balancers \
    --names "saitama-alb" \
    --query 'LoadBalancers[0].LoadBalancerArn' --output text 2>/dev/null || echo "")
  if [[ -n "$ALB_ARN" && "$ALB_ARN" != "None" ]]; then
    $AWS elbv2 delete-load-balancer --load-balancer-arn "$ALB_ARN" > /dev/null 2>&1 \
      && ok "Deleted ALB: saitama-alb" || true
    sleep 10  # wait for ALB to release before deleting TG
  fi
  TG_ARN=$($AWS elbv2 describe-target-groups \
    --names "saitama-tg" \
    --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null || echo "")
  if [[ -n "$TG_ARN" && "$TG_ARN" != "None" ]]; then
    $AWS elbv2 delete-target-group --target-group-arn "$TG_ARN" > /dev/null 2>&1 \
      && ok "Deleted target group: saitama-tg" || true
  fi

  # 8. Delete ECR repo
  $AWS ecr delete-repository --repository-name "$ECR_REPO" --force > /dev/null 2>&1 \
    && ok "Deleted ECR: $ECR_REPO" || true

  # 9. Delete log group
  $AWS logs delete-log-group --log-group-name "$LOG_GROUP" > /dev/null 2>&1 \
    && ok "Deleted log group: $LOG_GROUP" || true

  # 10. Security groups (may fail if instances still running — re-run after a few min)
  DEFAULT_VPC=$($AWS ec2 describe-vpcs \
    --filters Name=isDefault,Values=true \
    --query 'Vpcs[0].VpcId' --output text 2>/dev/null || echo "")
  for sg_name in saitama-alb saitama-tasks saitama-ec2; do
    SG_ID=$($AWS ec2 describe-security-groups \
      --filters "Name=group-name,Values=$sg_name" "Name=vpc-id,Values=$DEFAULT_VPC" \
      --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "")
    if [[ -n "$SG_ID" && "$SG_ID" != "None" ]]; then
      $AWS ec2 delete-security-group --group-id "$SG_ID" > /dev/null 2>&1 \
        && ok "Deleted SG: $sg_name" \
        || warn "Could not delete SG $sg_name (may still have dependencies — re-run destroy in a few minutes)"
    fi
  done

  ok "Destroy complete."
}

# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------
case "${1:-}" in
  deploy)  cmd_deploy ;;
  status)  cmd_status ;;
  logs)    cmd_logs ;;
  scale)   cmd_scale "${2:-}" ;;
  kill)    cmd_kill ;;
  destroy) cmd_destroy ;;
  *)
    echo "Usage: $0 {deploy|status|logs|scale <n>|kill|destroy}"
    exit 1
    ;;
esac
