"""Cluster uninstaller - runs pinetools uninstall before infrastructure teardown."""

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
    def create(self, props: dict[str, Any]) -> CreateResult:
        return CreateResult(id_="uninstaller-ready", outs=props)

    def diff(self, _id: str, _olds: dict[str, Any], _news: dict[str, Any]) -> DiffResult:
        # update state if kubeconfig or image changes, never trigger replacement
        changed = _olds.get("kubeconfig") != _news.get("kubeconfig") or _olds.get(
            "pinetools_image"
        ) != _news.get("pinetools_image")
        return DiffResult(changes=changed)

    def update(
        self, _id: str, _olds: dict[str, Any], _news: dict[str, Any]
    ) -> UpdateResult:
        return UpdateResult(outs=_news)

    def delete(self, _id: str, _props: dict[str, Any]) -> None:
        from kubernetes import client, config
        from kubernetes.client.rest import ApiException
        import yaml

        kubeconfig_str = _props.get("kubeconfig")
        if not kubeconfig_str:
            raise Exception("kubeconfig not provided to uninstaller")

        pinetools_image = _props.get("pinetools_image")
        if not pinetools_image:
            raise Exception("pinetools_image not provided to uninstaller")

        try:
            kubeconfig = json.loads(kubeconfig_str)
        except (json.JSONDecodeError, ValueError):
            try:
                kubeconfig = yaml.safe_load(kubeconfig_str)
            except yaml.YAMLError as e:
                raise Exception(f"Failed to parse kubeconfig as JSON or YAML: {e}")

        # gke exec-based auth needs a fresh gcloud token in dynamic provider context
        users = kubeconfig.get("users", [])
        has_exec = users and "exec" in users[0].get("user", {})
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
                active_deadline_seconds=1800,
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
            if e.status == 409:
                pulumi.log.warn(
                    f"Uninstall job {job_name} already exists, waiting for it"
                )
            else:
                raise Exception(f"Failed to create uninstall job: {e}")

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
    """Runs cluster uninstall on destroy. Must depend on all K8s resources."""

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
