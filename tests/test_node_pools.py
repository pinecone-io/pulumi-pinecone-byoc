from dataclasses import dataclass, field

from config.base import NodePoolConfig, NodePoolTaint
from pulumi_pinecone_byoc.common.node_pool import node_pool_configs


def test_a_pool_is_born_at_its_desired_size():
    assert NodePoolConfig(name="default").initial_count() == 3


def test_max_size_caps_the_birth_size():
    assert NodePoolConfig(name="default", max_size=2).initial_count() == 2


def test_min_size_raises_it():
    assert NodePoolConfig(name="default", min_size=5).initial_count() == 5


def test_gke_divides_the_seed_because_its_seed_is_per_zone():
    np_config = NodePoolConfig(name="default")

    assert np_config.initial_count_per_zone(2) == 2
    assert np_config.initial_count_per_zone(3) == 1


def test_a_divided_seed_never_exceeds_the_pool_total():
    np_config = NodePoolConfig(name="default", max_size=4)

    assert np_config.initial_count_per_zone(2) * 2 == 4


def test_a_divided_seed_is_never_zero():
    assert NodePoolConfig(name="default", max_size=1).initial_count_per_zone(2) == 1


def test_every_limit_is_a_pool_total():
    np_config = NodePoolConfig(name="default", min_size=2, max_size=8, desired_size=4)

    assert (np_config.min_size, np_config.initial_count(), np_config.max_size) == (2, 4, 8)


@dataclass
class AwsPool:
    name: str
    instance_type: str = "r6in.large"
    min_size: int = 1
    max_size: int = 10
    desired_size: int = 3
    disk_size_gb: int = 100
    labels: dict = field(default_factory=dict)
    taints: list = field(default_factory=list)


@dataclass
class AzurePool:
    name: str
    vm_size: str = "Standard_D4s_v5"
    min_size: int = 1
    max_size: int = 10
    desired_size: int = 3
    disk_size_gb: int = 100
    labels: dict = field(default_factory=dict)
    taints: list = field(default_factory=list)


@dataclass
class Taint:
    key: str
    value: str | bool
    effect: str = "NO_SCHEDULE"


def test_no_pools_means_one_default_pool_every_cloud_can_read():
    [pool] = node_pool_configs(None)

    assert (pool.name, pool.min_size, pool.max_size, pool.desired_size) == ("default", 1, 12, 3)
    assert pool.disk_size_gb == 100
    assert pool.instance_type == "r6in.large"
    assert pool.vm_size == "Standard_D4s_v5"
    assert pool.machine_type == "n2-standard-4"


def test_a_pool_carries_over_every_field_it_shares_a_name_with():
    [pool] = node_pool_configs([AzurePool(name="fdb", vm_size="Standard_E4s_v5", max_size=4)])

    assert pool.name == "fdb"
    assert pool.vm_size == "Standard_E4s_v5"
    assert pool.max_size == 4


def test_a_cloud_that_does_not_name_a_field_leaves_it_at_its_default():
    [pool] = node_pool_configs([AzurePool(name="default")])

    assert pool.instance_type == "r6in.large"


def test_a_taint_converts_from_whatever_shape_the_caller_used():
    pools = node_pool_configs(
        [
            AwsPool(name="a", taints=[Taint(key="fdb", value="true", effect="NO_SCHEDULE")]),
            AzurePool(name="b", taints=[{"key": "fdb", "value": "true"}]),
            AzurePool(name="c", taints=[NodePoolTaint(key="fdb", value="true")]),
        ]
    )

    for pool in pools:
        [taint] = pool.taints
        assert (taint.key, taint.value, taint.effect) == ("fdb", "true", "NO_SCHEDULE")


def test_a_taint_value_that_is_not_a_string_still_converts():
    [pool] = node_pool_configs([AzurePool(name="a", taints=[Taint(key="fdb", value=True)])])

    assert pool.taints[0].value == "True"
