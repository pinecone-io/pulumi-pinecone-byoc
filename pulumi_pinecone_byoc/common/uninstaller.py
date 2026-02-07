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
from pulumi.dynamic import (
    Resource,
    ResourceProvider,
    CreateResult,
    DiffResult,
    UpdateResult,
)


class ClusterUninstallerProvider(ResourceProvider):
    """
    Dynamic provider that runs uninstall job on delete.

    The delete method creates a K8s Job and waits for it to complete.
    If the job fails, an exception is raised which fails the destroy.
    """

    def create(self, props: dict[str, Any]) -> CreateResult:
        # no-op on create - just mark as "ready for uninstall"
        return CreateResult(id_="uninstaller-ready", outs=props)

    def diff(self, _id: str, old: dict[str, Any], new: dict[str, Any]) -> DiffResult:
        # update state if kubeconfig or image changes, but never trigger replacement
        # this keeps state fresh without causing accidental uninstalls
        changed = old.get("kubeconfig") != new.get("kubeconfig") or old.get(
            "pinetools_image"
        ) != new.get("pinetools_image")
        return DiffResult(changes=changed)

    def update(
        self, _id: str, _old: dict[str, Any], new: dict[str, Any]
    ) -> UpdateResult:
        # no-op - just pass through new props, no actual update needed
        return UpdateResult(outs=new)

    def delete(self, _id: str, props: dict[str, Any]) -> None:
        from kubernetes import client, config
        from kubernetes.client.rest import ApiException
        import yaml

        kubeconfig_str = props.get("kubeconfig")
        if not kubeconfig_str:
            raise Exception("kubeconfig not provided to uninstaller")

        pinetools_image = props.get("pinetools_image")
        if not pinetools_image:
            raise Exception("pinetools_image not provided to uninstaller")

        try:
            # try JSON first (AWS EKS format)
            kubeconfig = json.loads(kubeconfig_str)
        except (json.JSONDecodeError, ValueError):
            # fall back to YAML (GCP GKE format)
            try:
                kubeconfig = yaml.safe_load(kubeconfig_str)
            except yaml.YAMLError as e:
                raise Exception(f"Failed to parse kubeconfig as JSON or YAML: {e}")

        # GKE exec-based auth may not work in dynamic provider context;
        # inject a fresh token via gcloud CLI if the kubeconfig uses exec auth
        users = kubeconfig.get("users", [])
        has_exec = users and "exec" in users[0].get("user", {})
        pulumi.log.info(f"Uninstaller kubeconfig auth: exec={has_exec}")
        if has_exec:
            try:
                import subprocess

                token = subprocess.check_output(
                    ["gcloud", "auth", "print-access-token"],
                    text=True,
                    timeout=10,
                ).strip()
                pulumi.log.info(f"Injected gcloud token: {token[:10]}...")
                for user in users:
                    user["user"] = {"token": token}
            except Exception as e:
                pulumi.log.warn(f"Failed to get gcloud token: {e}")

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
                backoff_limit=1,
                active_deadline_seconds=600,
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
                                image=pinetools_image,
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
        pinetools_image: pulumi.Input[str],
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        props = {
            "kubeconfig": kubeconfig,
            "pinetools_image": pinetools_image,
        }
        super().__init__(
            ClusterUninstallerProvider(),
            name,
            props,
            opts,
        )
