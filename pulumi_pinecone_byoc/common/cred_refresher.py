"""Registry credential refresher cronjob. Supports ECR and GCR."""

import pulumi
import pulumi_kubernetes as k8s

EXTRA_NAMESPACES = "prometheus metering tooling gloo-system kube-system"

REGISTRY_CONFIG = {
    "ecr": {
        "username": "AWS",
        "password_extraction": 'echo "$TOKEN_B64" | base64 -d | cut -d: -f2',
    },
    "gcr": {
        "username": "oauth2accesstoken",
        "password_extraction": 'echo "$TOKEN_B64" | sed "s/^Bearer //"',
    },
}


def _build_refresher_script(registry: str) -> str:
    cfg = REGISTRY_CONFIG[registry]
    username = cfg["username"]
    password_extraction = cfg["password_extraction"]
    return rf"""
set -e

echo "=== Registry Credential Refresher ({registry}) ==="
echo "Time: $(date -Iseconds)"

# 1. Get token from cpgw
echo "Fetching {registry} token from cpgw..."
RESPONSE=$(wget -qO- --header="Content-Type: application/json" \
  --header="api-key: ${{CPGW_API_KEY}}" \
  "${{CPGW_URL}}/internal/cpgw/infra/cr-token?registry={registry}")

# parse json without jq - extract values between quotes after key
extract_json() {{
  echo "$1" | grep -o "\"$2\":\"[^\"]*\"" | cut -d'"' -f4
}}

TOKEN_B64=$(extract_json "$RESPONSE" "token")
REGISTRY=$(extract_json "$RESPONSE" "registry_endpoint")
EXPIRES=$(extract_json "$RESPONSE" "expires_at")
[ -z "$EXPIRES" ] && EXPIRES="unknown"

if [ -z "$TOKEN_B64" ]; then
  echo "ERROR: Failed to get token"
  echo "Response: $RESPONSE"
  exit 1
fi

# Extract password from token
PASSWORD=$({password_extraction})

echo "Got token for registry: $REGISTRY (expires: $EXPIRES)"

# 2. Discover all pc-* namespaces
echo "Discovering namespaces..."
PC_NAMESPACES=$(kubectl get namespaces -o jsonpath='{{.items[*].metadata.name}}' | tr ' ' '\n' | grep '^pc-' || true)

# Combine with extra namespaces (space-separated)
ALL_NAMESPACES=$(echo -e "${{PC_NAMESPACES}}\n$(echo $EXTRA_NAMESPACES | tr ' ' '\n')" | sort -u | grep -v '^$')

echo "Target namespaces:"
echo "$ALL_NAMESPACES" | sed 's/^/  - /'

# 3. Distribute secret to all namespaces
SUCCESS_COUNT=0
FAIL_COUNT=0

for NS in $ALL_NAMESPACES; do
  # Check if namespace exists
  if ! kubectl get namespace "$NS" &>/dev/null; then
    echo "  [$NS] Namespace does not exist, skipping"
    continue
  fi

  echo "  [$NS] Updating regcred..."

  # Delete existing secret if present
  kubectl delete secret regcred -n "$NS" --ignore-not-found &>/dev/null

  # Create new secret
  if kubectl create secret docker-registry regcred \
    --namespace="$NS" \
    --docker-server="$REGISTRY" \
    --docker-username={username} \
    --docker-password="$PASSWORD" &>/dev/null; then
    echo "  [$NS] Success"
    SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
  else
    echo "  [$NS] FAILED"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
done

echo ""
echo "=== Summary ==="
echo "Successful: $SUCCESS_COUNT"
echo "Failed: $FAIL_COUNT"

if [ $FAIL_COUNT -gt 0 ]; then
  echo "WARNING: Some namespaces failed to update"
  exit 1
fi

echo "All namespaces updated successfully!"
"""


class RegistryCredentialRefresher(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        k8s_provider: pulumi.ProviderResource,
        cpgw_url: pulumi.Input[str],
        registry: str = "ecr",
        schedule: str = "* * * * *",
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__(
            f"pinecone:common:RegistryCredentialRefresher-{registry}", name, None, opts
        )

        namespace = "external-secrets"

        cluster_role = k8s.rbac.v1.ClusterRole(
            f"{name}-cluster-role",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name=f"{registry}-credential-refresher",
            ),
            rules=[
                k8s.rbac.v1.PolicyRuleArgs(
                    api_groups=[""],
                    resources=["secrets"],
                    verbs=["create", "delete", "patch", "update", "get"],
                ),
                k8s.rbac.v1.PolicyRuleArgs(
                    api_groups=[""],
                    resources=["namespaces"],
                    verbs=["list", "get"],
                ),
            ],
            opts=pulumi.ResourceOptions(parent=self, provider=k8s_provider),
        )

        service_account = k8s.core.v1.ServiceAccount(
            f"{name}-service-account",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name=f"{registry}-credential-refresher",
                namespace=namespace,
            ),
            opts=pulumi.ResourceOptions(parent=self, provider=k8s_provider),
        )

        cluster_role_binding = k8s.rbac.v1.ClusterRoleBinding(
            f"{name}-cluster-role-binding",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name=f"{registry}-credential-refresher",
            ),
            subjects=[
                k8s.rbac.v1.SubjectArgs(
                    kind="ServiceAccount",
                    name=f"{registry}-credential-refresher",
                    namespace=namespace,
                ),
            ],
            role_ref=k8s.rbac.v1.RoleRefArgs(
                kind="ClusterRole",
                name=f"{registry}-credential-refresher",
                api_group="rbac.authorization.k8s.io",
            ),
            opts=pulumi.ResourceOptions(
                parent=self, provider=k8s_provider, depends_on=[cluster_role]
            ),
        )

        config_map = k8s.core.v1.ConfigMap(
            f"{name}-config",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name=f"{registry}-refresher-config",
                namespace=namespace,
            ),
            data={
                "cpgw-url": cpgw_url,
            },
            opts=pulumi.ResourceOptions(parent=self, provider=k8s_provider),
        )

        cronjob = k8s.batch.v1.CronJob(
            f"{name}-cronjob",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name=f"{registry}-credential-refresher",
                namespace=namespace,
            ),
            spec=k8s.batch.v1.CronJobSpecArgs(
                schedule=schedule,
                successful_jobs_history_limit=3,
                failed_jobs_history_limit=3,
                concurrency_policy="Forbid",
                job_template=k8s.batch.v1.JobTemplateSpecArgs(
                    spec=k8s.batch.v1.JobSpecArgs(
                        backoff_limit=1,
                        ttl_seconds_after_finished=300,
                        template=k8s.core.v1.PodTemplateSpecArgs(
                            spec=k8s.core.v1.PodSpecArgs(
                                service_account_name=f"{registry}-credential-refresher",
                                restart_policy="OnFailure",
                                containers=[
                                    k8s.core.v1.ContainerArgs(
                                        name=f"{registry}-credential-refresher",
                                        image="alpine/k8s:1.31.3",
                                        env=[
                                            k8s.core.v1.EnvVarArgs(
                                                name="CPGW_API_KEY",
                                                value_from=k8s.core.v1.EnvVarSourceArgs(
                                                    secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                                                        name="cpgw-credentials",
                                                        key="api-key",
                                                    ),
                                                ),
                                            ),
                                            k8s.core.v1.EnvVarArgs(
                                                name="CPGW_URL",
                                                value_from=k8s.core.v1.EnvVarSourceArgs(
                                                    config_map_key_ref=k8s.core.v1.ConfigMapKeySelectorArgs(
                                                        name=f"{registry}-refresher-config",
                                                        key="cpgw-url",
                                                    ),
                                                ),
                                            ),
                                            k8s.core.v1.EnvVarArgs(
                                                name="EXTRA_NAMESPACES",
                                                value=EXTRA_NAMESPACES,
                                            ),
                                        ],
                                        command=["/bin/bash", "-c"],
                                        args=[_build_refresher_script(registry)],
                                    ),
                                ],
                            ),
                        ),
                    ),
                ),
            ),
            opts=pulumi.ResourceOptions(
                parent=self,
                provider=k8s_provider,
                depends_on=[
                    service_account,
                    cluster_role_binding,
                    config_map,
                ],
            ),
        )

        self.cronjob_name = cronjob.metadata.name
        self.namespace = namespace

        self.register_outputs(
            {
                "cronjob_name": self.cronjob_name,
                "namespace": self.namespace,
            }
        )
