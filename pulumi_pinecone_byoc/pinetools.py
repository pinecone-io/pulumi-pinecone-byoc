"""
Pinetools setup for cluster management.

Creates:
- CronJob for periodic cluster maintenance (suspended by default)
- One-time installation Job

Job naming includes version suffix, so:
- Same version = same job name = Pulumi skips (no change)
- New version = new job name = old job deleted, new one created
- Failed jobs auto-cleanup after 60s, then retry with `pulumi up`
"""

from typing import Optional

import pulumi
import pulumi_kubernetes as k8s


# script to wait for regcred secret before running pinetools
WAIT_FOR_REGCRED_SCRIPT = """
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
"""


def _job_name(pinecone_version: str) -> str:
    import re

    return f"pinetools-install-{re.sub(r'[^a-z0-9\-]', '', pinecone_version.lower())}".strip(
        "-"
    )


class Pinetools(pulumi.ComponentResource):
    """
    Deploys pinetools to pc-control-plane namespace.

    Creates:
    - ServiceAccount with imagePullSecrets for ECR
    - ClusterRoleBinding for cluster-admin access
    - CronJob for periodic maintenance (suspended)
    - Job for cluster installation

    The installation Job name includes the pinecone_version suffix, so:
    - Same version = same job name = Pulumi sees no change (skips)
    - New version = new job name = old job replaced
    - Job has TTL of 60s after completion for automatic cleanup
    """

    def __init__(
        self,
        name: str,
        k8s_provider: pulumi.ProviderResource,
        pinecone_version: pulumi.Input[str],
        pinetools_image: str = "843333058014.dkr.ecr.us-east-1.amazonaws.com/unstable/pinecone/v4/pinetools:latest",
        schedule: str = "0 * * * *",
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        super().__init__("pinecone:byoc:Pinetools", name, None, opts)

        namespace = "pc-control-plane"
        self._pinetools_image = pinetools_image
        self._pinecone_version = pinecone_version

        ns = k8s.core.v1.Namespace(
            f"{name}-namespace",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name=namespace,
            ),
            opts=pulumi.ResourceOptions(parent=self, provider=k8s_provider),
        )

        ns_opts = pulumi.ResourceOptions(
            parent=self,
            provider=k8s_provider,
            depends_on=[ns],
        )

        sa = k8s.core.v1.ServiceAccount(
            f"{name}-sa",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="pinetools",
                namespace=namespace,
            ),
            image_pull_secrets=[
                k8s.core.v1.LocalObjectReferenceArgs(name="regcred"),
            ],
            opts=ns_opts,
        )

        k8s.rbac.v1.ClusterRoleBinding(
            f"{name}-cluster-admin",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="pinetools-cluster-admin",
            ),
            role_ref=k8s.rbac.v1.RoleRefArgs(
                api_group="rbac.authorization.k8s.io",
                kind="ClusterRole",
                name="cluster-admin",
            ),
            subjects=[
                k8s.rbac.v1.SubjectArgs(
                    kind="ServiceAccount",
                    name="pinetools",
                    namespace=namespace,
                ),
            ],
            opts=ns_opts,
        )

        # shared config for cronjob and install job
        tolerations = [
            k8s.core.v1.TolerationArgs(
                key="node.kubernetes.io/disk-pressure",
                operator="Exists",
                effect="NoSchedule",
            ),
        ]

        pinetools_container = k8s.core.v1.ContainerArgs(
            name="pinetools",
            image=pinetools_image,
            command=["/bin/sh", "-c"],
            # restart cluster-autoscaler after install so it discovers all node groups with correct taints
            args=[
                "pinetools cluster install && kubectl rollout restart deployment -n kube-system -l app.kubernetes.io/name=cluster-autoscaler && pinetools cluster check"
            ],
            env=[
                k8s.core.v1.EnvVarArgs(
                    name="PINECONE_IMAGE_VERSION",
                    value=pinecone_version,
                ),
            ],
            resources=k8s.core.v1.ResourceRequirementsArgs(
                requests={"ephemeral-storage": "1Gi", "memory": "512Mi", "cpu": "100m"},
                limits={"ephemeral-storage": "5Gi", "memory": "2Gi"},
            ),
        )

        wait_for_regcred_container = k8s.core.v1.ContainerArgs(
            name="wait-for-regcred",
            image="alpine/k8s:1.31.3",
            command=["/bin/sh", "-c"],
            args=[WAIT_FOR_REGCRED_SCRIPT],
        )

        def make_pod_spec(
            init_containers: list[k8s.core.v1.ContainerArgs] | None = None,
        ) -> k8s.core.v1.PodSpecArgs:
            return k8s.core.v1.PodSpecArgs(
                service_account_name="pinetools",
                restart_policy="OnFailure",
                tolerations=tolerations,
                containers=[pinetools_container],
                init_containers=init_containers,
            )

        def make_cronjob_spec() -> k8s.batch.v1.JobSpecArgs:
            return k8s.batch.v1.JobSpecArgs(
                backoff_limit=0,
                ttl_seconds_after_finished=3600,
                template=k8s.core.v1.PodTemplateSpecArgs(
                    spec=make_pod_spec(),
                ),
            )

        def make_install_job_spec(
            init_containers: list[k8s.core.v1.ContainerArgs] | None = None,
        ) -> k8s.batch.v1.JobSpecArgs:
            return k8s.batch.v1.JobSpecArgs(
                backoff_limit=0,
                ttl_seconds_after_finished=60,
                template=k8s.core.v1.PodTemplateSpecArgs(
                    spec=make_pod_spec(init_containers),
                ),
            )

        cronjob = k8s.batch.v1.CronJob(
            f"{name}-cronjob",
            metadata=k8s.meta.v1.ObjectMetaArgs(name="pinetools", namespace=namespace),
            spec=k8s.batch.v1.CronJobSpecArgs(
                suspend=True,  # manual trigger only
                schedule=schedule,
                successful_jobs_history_limit=3,
                failed_jobs_history_limit=3,
                concurrency_policy="Forbid",
                job_template=k8s.batch.v1.JobTemplateSpecArgs(spec=make_cronjob_spec()),
            ),
            opts=pulumi.ResourceOptions(
                parent=self, provider=k8s_provider, depends_on=[sa]
            ),
        )

        # one-time installation job
        # Job name includes version suffix so:
        # - Same version = same job name = Pulumi sees no change = skips
        # - New version = new job name = old job deleted, new one created
        # - Failed job auto-deletes after 60s, so retry is just `pulumi up` again
        version_output = pulumi.Output.from_input(pinecone_version)
        job_name = version_output.apply(_job_name)
        install_job = k8s.batch.v1.Job(
            f"{name}-install-job",
            metadata=k8s.meta.v1.ObjectMetaArgs(name=job_name, namespace=namespace),
            spec=make_install_job_spec(init_containers=[wait_for_regcred_container]),
            opts=pulumi.ResourceOptions(
                parent=self,
                provider=k8s_provider,
                depends_on=[sa, cronjob],
            ),
        )
        self.install_job_name = install_job.metadata.name

        self.namespace = namespace
        self.cronjob_name = cronjob.metadata.name

        self.register_outputs(
            {
                "namespace": self.namespace,
                "cronjob_name": self.cronjob_name,
                "install_job_name": self.install_job_name,
            }
        )
