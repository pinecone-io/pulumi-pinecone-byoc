from types import SimpleNamespace

from pulumi_pinecone_byoc.common.uninstaller import UNINSTALL_POD_ANNOTATIONS, job_has_failed


def _status(failed=None, conditions=None):
    return SimpleNamespace(failed=failed, conditions=conditions)


def _condition(type_, status="True"):
    return SimpleNamespace(type=type_, status=status)


def test_a_pod_lost_while_the_job_can_still_retry_is_not_a_failure():
    assert not job_has_failed(_status(failed=1))
    assert not job_has_failed(_status(failed=1, conditions=[]))


def test_the_job_has_failed_once_kubernetes_gives_up_on_it():
    assert job_has_failed(_status(failed=2, conditions=[_condition("Failed")]))


def test_a_cleared_failed_condition_is_not_a_failure():
    assert not job_has_failed(_status(failed=1, conditions=[_condition("Failed", "False")]))


def test_the_autoscaler_may_not_take_the_node_from_under_the_uninstall():
    assert UNINSTALL_POD_ANNOTATIONS == {"cluster-autoscaler.kubernetes.io/safe-to-evict": "false"}
