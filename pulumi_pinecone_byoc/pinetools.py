"""
Pinetools cronjob - runs pinetools for cluster management.

TODO: this is a placeholder. actual command will be "pinetools cluster install".
currently just runs "pinetools doctor" to verify ECR pull works.
"""

from typing import Optional

import pulumi
import pulumi_kubernetes as k8s


class Pinetools(pulumi.ComponentResource):
    """
    Deploys pinetools cronjob to pc-control-plane namespace.

    Requires regcred secret to be created by EcrCredentialRefresher.
    """

    def __init__(
        self,
        name: str,
        k8s_provider: pulumi.ProviderResource,
        pinetools_image: str = "843333058014.dkr.ecr.us-east-1.amazonaws.com/unstable/pinecone/v4/pinetools:smbyoc-20",
        schedule: str = "0 * * * *",
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        super().__init__("pinecone:byoc:Pinetools", name, None, opts)

        namespace = "pc-control-plane"

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
                                            "main-3d6741d",
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

        self.namespace = namespace
        self.cronjob_name = cronjob.metadata.name

        self.register_outputs(
            {
                "namespace": self.namespace,
                "cronjob_name": self.cronjob_name,
            }
        )
