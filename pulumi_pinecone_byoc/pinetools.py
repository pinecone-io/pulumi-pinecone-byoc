"""
Pinetools setup for cluster management.

Creates:
- CronJob for periodic cluster maintenance (suspended by default)
- One-time installation Job that runs on first provision
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


class Pinetools(pulumi.ComponentResource):
    """
    Deploys pinetools to pc-control-plane namespace.

    Creates:
    - ServiceAccount with imagePullSecrets for ECR
    - ClusterRoleBinding for cluster-admin access
    - CronJob for periodic maintenance (suspended)
    - One-time Job for initial cluster installation

    The installation Job runs automatically on first `pulumi up` and is
    ignored on subsequent runs (via ignore_changes).
    """

    def __init__(
        self,
        name: str,
        k8s_provider: pulumi.ProviderResource,
        pinetools_image: str = "843333058014.dkr.ecr.us-east-1.amazonaws.com/unstable/pinecone/v4/pinetools:latest",
        install_image_tag: str = "main-3d6741d",
        schedule: str = "0 * * * *",
        run_install_job: bool = True,
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        super().__init__("pinecone:byoc:Pinetools", name, None, opts)

        namespace = "pc-control-plane"
        self._pinetools_image = pinetools_image
        self._install_image_tag = install_image_tag

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

        # service account with imagePullSecrets
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

        # cluster-admin binding for pinetools
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

        cronjob = k8s.batch.v1.CronJob(
            f"{name}-cronjob",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="pinetools",
                namespace=namespace,
            ),
            spec=k8s.batch.v1.CronJobSpecArgs(
                suspend=True,  # manual trigger only for now
                schedule=schedule,
                successful_jobs_history_limit=3,
                failed_jobs_history_limit=3,
                concurrency_policy="Forbid",
                job_template=k8s.batch.v1.JobTemplateSpecArgs(
                    spec=k8s.batch.v1.JobSpecArgs(
                        backoff_limit=3,
                        ttl_seconds_after_finished=3600,  # cleanup after 1 hour
                        template=k8s.core.v1.PodTemplateSpecArgs(
                            spec=k8s.core.v1.PodSpecArgs(
                                service_account_name="pinetools",
                                restart_policy="OnFailure",
                                tolerations=[
                                    k8s.core.v1.TolerationArgs(
                                        key="node.kubernetes.io/disk-pressure",
                                        operator="Exists",
                                        effect="NoSchedule",
                                    ),
                                ],
                                containers=[
                                    k8s.core.v1.ContainerArgs(
                                        name="pinetools",
                                        image=pinetools_image,
                                        # TODO: get image version dynamically
                                        command=[
                                            "pinetools",
                                            "cluster",
                                            "install",
                                            "--image",
                                            install_image_tag,
                                        ],
                                        resources=k8s.core.v1.ResourceRequirementsArgs(
                                            requests={
                                                "ephemeral-storage": "1Gi",
                                                "memory": "512Mi",
                                                "cpu": "100m",
                                            },
                                            limits={
                                                "ephemeral-storage": "5Gi",
                                                "memory": "2Gi",
                                            },
                                        ),
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
                depends_on=[sa],
            ),
        )

        # one-time installation job - runs on first pulumi up only
        if run_install_job:
            install_job = k8s.batch.v1.Job(
                f"{name}-install-job",
                metadata=k8s.meta.v1.ObjectMetaArgs(
                    name="pinetools-install",
                    namespace=namespace,
                ),
                spec=k8s.batch.v1.JobSpecArgs(
                    backoff_limit=3,
                    ttl_seconds_after_finished=3600,  # cleanup after 1 hour
                    template=k8s.core.v1.PodTemplateSpecArgs(
                        spec=k8s.core.v1.PodSpecArgs(
                            service_account_name="pinetools",
                            restart_policy="OnFailure",
                            # init container waits for ECR credentials to be available
                            init_containers=[
                                k8s.core.v1.ContainerArgs(
                                    name="wait-for-regcred",
                                    image="alpine/k8s:1.31.3",
                                    command=["/bin/sh", "-c"],
                                    args=[WAIT_FOR_REGCRED_SCRIPT],
                                ),
                            ],
                            tolerations=[
                                k8s.core.v1.TolerationArgs(
                                    key="node.kubernetes.io/disk-pressure",
                                    operator="Exists",
                                    effect="NoSchedule",
                                ),
                            ],
                            containers=[
                                k8s.core.v1.ContainerArgs(
                                    name="pinetools",
                                    image=pinetools_image,
                                    command=["/bin/sh", "-c"],
                                    args=[
                                        f"pinetools cluster install --image {install_image_tag} && pinetools cluster check"
                                    ],
                                    resources=k8s.core.v1.ResourceRequirementsArgs(
                                        requests={
                                            "ephemeral-storage": "1Gi",
                                            "memory": "512Mi",
                                            "cpu": "100m",
                                        },
                                        limits={
                                            "ephemeral-storage": "5Gi",
                                            "memory": "2Gi",
                                        },
                                    ),
                                ),
                            ],
                        ),
                    ),
                ),
                opts=pulumi.ResourceOptions(
                    parent=self,
                    provider=k8s_provider,
                    depends_on=[sa, cronjob],
                    # ignore changes so this job only runs on first provision
                    # subsequent pulumi up runs will not recreate/update this job
                    ignore_changes=["*"],
                ),
            )
            self.install_job_name = install_job.metadata.name
        else:
            self.install_job_name = None

        self.namespace = namespace
        self.cronjob_name = cronjob.metadata.name

        self.register_outputs(
            {
                "namespace": self.namespace,
                "cronjob_name": self.cronjob_name,
                "install_job_name": self.install_job_name,
            }
        )
