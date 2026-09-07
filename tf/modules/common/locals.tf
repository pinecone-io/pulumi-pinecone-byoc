locals {
  external_ns      = "external-secrets"
  control_plane_ns = "pc-control-plane"

  registry_config = {
    ecr = {
      username            = "AWS"
      password_extraction = "echo \"$TOKEN_B64\" | base64 -d | cut -d: -f2"
    }
    gcr = {
      username            = "oauth2accesstoken"
      password_extraction = "echo \"$TOKEN_B64\" | sed \"s/^Bearer //\""
    }
    acr = {
      username            = "00000000-0000-0000-0000-000000000000"
      password_extraction = "echo \"$TOKEN_B64\""
    }
  }

  registry_username            = local.registry_config[var.registry_type].username
  registry_password_extraction = local.registry_config[var.registry_type].password_extraction
  extra_namespaces             = "prometheus metering tooling gloo-system kube-system"

  control_db_url          = "postgres://${var.db_credentials.control.username}:${var.db_credentials.control.password}@${var.db_credentials.control.host}:${var.db_credentials.control.port}/${var.db_credentials.control.dbname}"
  control_db_readonly_url = "postgres://${var.db_credentials.control.username}:${var.db_credentials.control.password}@${var.db_credentials.control.readonly_host}:${var.db_credentials.control.port}/${var.db_credentials.control.dbname}"
  system_db_url           = "postgres://${var.db_credentials.system.username}:${var.db_credentials.system.password}@${var.db_credentials.system.host}:${var.db_credentials.system.port}/${var.db_credentials.system.dbname}"
  system_db_readonly_url  = "postgres://${var.db_credentials.system.username}:${var.db_credentials.system.password}@${var.db_credentials.system.readonly_host}:${var.db_credentials.system.port}/${var.db_credentials.system.dbname}"

  control_db_secret = {
    url           = local.control_db_url
    readonly_url  = local.control_db_readonly_url
    host          = var.db_credentials.control.host
    readonly_host = var.db_credentials.control.readonly_host
    port          = var.db_credentials.control.port
    username      = var.db_credentials.control.username
    password      = var.db_credentials.control.password
    dbname        = var.db_credentials.control.dbname
  }

  system_db_secret = {
    url           = local.system_db_url
    readonly_url  = local.system_db_readonly_url
    host          = var.db_credentials.system.host
    readonly_host = var.db_credentials.system.readonly_host
    port          = var.db_credentials.system.port
    username      = var.db_credentials.system.username
    password      = var.db_credentials.system.password
    dbname        = var.db_credentials.system.dbname
  }

  shards_json = jsonencode({
    "control-1" = merge(local.control_db_secret, {
      port = tonumber(var.db_credentials.control.port)
    })
    system = merge(local.system_db_secret, {
      port = tonumber(var.db_credentials.system.port)
    })
  })

  registry_refresher_script = <<-SCRIPT
set -e

echo "=== Registry Credential Refresher (${var.registry_type}) ==="
echo "Time: $(date -Iseconds)"

RESPONSE=$(wget -qO- --header="Content-Type: application/json" \
  --header="api-key: $${CPGW_API_KEY}" \
  "$${CPGW_URL}/internal/cpgw/infra/cr-token?registry=${var.registry_type}")

extract_json() {
  echo "$1" | grep -o "\"$2\":\"[^\"]*\"" | cut -d'"' -f4
}

TOKEN_B64=$(extract_json "$RESPONSE" "token")
REGISTRY=$(extract_json "$RESPONSE" "registry_endpoint")
EXPIRES=$(extract_json "$RESPONSE" "expires_at")
[ -z "$EXPIRES" ] && EXPIRES="unknown"

if [ -z "$TOKEN_B64" ]; then
  echo "ERROR: Failed to get token"
  echo "Response: $RESPONSE"
  exit 1
fi

PASSWORD=$(${local.registry_password_extraction})
echo "Got token for registry: $REGISTRY (expires: $EXPIRES)"

PC_NAMESPACES=$(kubectl get namespaces -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n' | grep '^pc-' || true)
ALL_NAMESPACES=$(printf '%s\n%s\n' "$${PC_NAMESPACES}" "$(echo "$${EXTRA_NAMESPACES}" | tr ' ' '\n')" | sort -u | grep -v '^$')

SUCCESS_COUNT=0
FAIL_COUNT=0
for NS in $ALL_NAMESPACES; do
  if ! kubectl get namespace "$NS" >/dev/null 2>&1; then
    echo "  [$NS] Namespace does not exist, skipping"
    continue
  fi
  kubectl delete secret regcred -n "$NS" --ignore-not-found >/dev/null 2>&1
  if kubectl create secret docker-registry regcred \
    --namespace="$NS" \
    --docker-server="$REGISTRY" \
    --docker-username=${local.registry_username} \
    --docker-password="$PASSWORD" >/dev/null 2>&1; then
    SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
done

echo "Successful: $SUCCESS_COUNT"
echo "Failed: $FAIL_COUNT"
[ "$FAIL_COUNT" -eq 0 ]
SCRIPT

  wait_for_regcred_script = <<-SCRIPT
echo "Waiting for regcred secret in pc-control-plane namespace..."
for i in $(seq 1 60); do
  if kubectl get secret regcred -n pc-control-plane >/dev/null 2>&1; then
    echo "regcred secret found!"
    exit 0
  fi
  echo "Attempt $i/60: regcred not found, waiting 10s..."
  sleep 10
done
echo "ERROR: regcred secret not found after 10 minutes"
exit 1
SCRIPT

  pinetools_job_name = "pinetools-install-${trim(replace(lower(var.pinecone_version), "/[^a-z0-9-]/", ""), "-")}"
}
