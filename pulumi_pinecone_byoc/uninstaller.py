"""
Cluster uninstaller - runs pinetools uninstall before infrastructure teardown.

Creates a dynamic resource that:
- Does nothing on create
- On delete: runs a K8s Job with `pinetools cluster uninstall --force`
- Waits for job completion; fails destroy if job fails
- Must depend on all K8s resources so it's destroyed FIRST
"""

import json
import time
import uuid
from typing import Any, Optional

import pulumi
from pulumi.dynamic import Resource, ResourceProvider, CreateResult, DiffResult, UpdateResult


class ClusterUninstallerProvider(ResourceProvider):
    """
    Dynamic provider that runs uninstall job on delete.

    The delete method creates a K8s Job and waits for it to complete.
    If the job fails, an exception is raised which fails the destroy.
    """

    def create(self, props: dict[str, Any]) -> CreateResult:
        # no-op on create - just mark as "ready for uninstall"
        return CreateResult(id_="uninstaller-ready", outs=props)

    def diff(self, id: str, old: dict[str, Any], new: dict[str, Any]) -> DiffResult:
        # no-op - never trigger replacement, uninstall only runs on explicit delete
        return DiffResult(changes=False)

    def update(self, id: str, old: dict[str, Any], new: dict[str, Any]) -> UpdateResult:
        # no-op - just pass through new props, no actual update needed
        return UpdateResult(outs=new)

    def delete(self, id: str, props: dict[str, Any]) -> None:
        from kubernetes import client, config
        from kubernetes.client.rest import ApiException

        kubeconfig_json = props.get("kubeconfig")
        if not kubeconfig_json:
            raise Exception("kubeconfig not provided to uninstaller")

        kubeconfig = json.loads(kubeconfig_json)

        config.load_kube_config_from_dict(kubeconfig)

        batch_v1 = client.BatchV1Api()
        core_v1 = client.CoreV1Api()

        namespace = "pc-control-plane"
        job_name = f"pinetools-uninstall-{uuid.uuid4().hex[:8]}"

        job = client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=client.V1ObjectMeta(
                name=job_name,
                namespace=namespace,
            ),
            spec=client.V1JobSpec(
                backoff_limit=3,
                ttl_seconds_after_finished=300,
                template=client.V1PodTemplateSpec(
                    spec=client.V1PodSpec(
                        service_account_name="pinetools",
                        restart_policy="OnFailure",
                        tolerations=[
                            client.V1Toleration(
                                key="node.kubernetes.io/disk-pressure",
                                operator="Exists",
                                effect="NoSchedule",
                            ),
                        ],
                        containers=[
                            client.V1Container(
                                name="pinetools",
                                image="843333058014.dkr.ecr.us-east-1.amazonaws.com/unstable/pinecone/v4/pinetools:latest",
                                command=["/bin/sh", "-c"],
                                args=["pinetools cluster uninstall --force"],
                                resources=client.V1ResourceRequirements(
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
        )

        pulumi.log.info(f"Creating uninstall job: {job_name}")

        try:
            batch_v1.create_namespaced_job(namespace=namespace, body=job)
        except ApiException as e:
            if e.status == 409:  # already exists
                pulumi.log.warn(
                    f"Uninstall job {job_name} already exists, waiting for it"
                )
            else:
                raise Exception(f"Failed to create uninstall job: {e}")

        # wait for job to complete (timeout after 30 minutes)
        timeout_seconds = 1800
        poll_interval = 10
        elapsed = 0

        while elapsed < timeout_seconds:
            try:
                job_status = batch_v1.read_namespaced_job_status(
                    name=job_name,
                    namespace=namespace,
                )

                if job_status.status.succeeded and job_status.status.succeeded > 0:
                    pulumi.log.info(f"Uninstall job {job_name} completed successfully")
                    return

                if job_status.status.failed and job_status.status.failed > 0:
                    # get pod logs for debugging
                    pods = core_v1.list_namespaced_pod(
                        namespace=namespace,
                        label_selector=f"job-name={job_name}",
                    )
                    logs = ""
                    for pod in pods.items:
                        try:
                            logs += core_v1.read_namespaced_pod_log(
                                name=pod.metadata.name,
                                namespace=namespace,
                            )
                        except Exception:
                            pass

                    raise Exception(
                        f"Uninstall job {job_name} failed. "
                        f"Run 'pulumi destroy' again to retry.\n"
                        f"Logs:\n{logs}"
                    )

                pulumi.log.info(
                    f"Waiting for uninstall job {job_name}... "
                    f"(active: {job_status.status.active or 0}, "
                    f"elapsed: {elapsed}s)"
                )

            except ApiException as e:
                if e.status == 404:
                    pulumi.log.warn(f"Job {job_name} not found, may have been deleted")
                    return
                raise

            time.sleep(poll_interval)
            elapsed += poll_interval

        raise Exception(
            f"Uninstall job {job_name} timed out after {timeout_seconds}s. "
            f"Run 'pulumi destroy' again to retry."
        )


class ClusterUninstaller(Resource):
    """
    Resource that runs cluster uninstall on destroy.

    IMPORTANT: This resource must depend on ALL K8s resources
    so that it is destroyed FIRST during `pulumi destroy`.

    Example:
        uninstaller = ClusterUninstaller(
            "uninstaller",
            kubeconfig=eks.kubeconfig,
            opts=pulumi.ResourceOptions(
                depends_on=[pinetools, k8s_addons, k8s_secrets, ...],
            ),
        )
    """

    kubeconfig: pulumi.Output[str]

    def __init__(
        self,
        name: str,
        kubeconfig: pulumi.Input[str],
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        props = {
            "kubeconfig": kubeconfig,
        }
        super().__init__(
            ClusterUninstallerProvider(),
            name,
            props,
            opts,
        )
