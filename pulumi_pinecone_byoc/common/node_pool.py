"""Translation from a cloud's public `NodePool` args to the internal `NodePoolConfig`."""

from typing import Any

from config.base import NodePoolConfig, NodePoolTaint


def _taint(taint: Any) -> NodePoolTaint:
    if isinstance(taint, NodePoolTaint):
        return taint
    fields = (
        dict(taint)
        if isinstance(taint, dict)
        else {"key": taint.key, "value": taint.value, "effect": taint.effect}
    )
    fields["value"] = str(fields["value"])
    return NodePoolTaint(**fields)


def node_pool_configs(pools: list[Any] | None) -> list[NodePoolConfig]:
    """Every field a cloud's `NodePool` shares a name with `NodePoolConfig` carries
    over; whatever it does not name, the machine size included, keeps its default."""
    if not pools:
        return [NodePoolConfig(name="default")]

    configs = []
    for pool in pools:
        fields = {k: v for k, v in vars(pool).items() if k in NodePoolConfig.model_fields}
        fields["taints"] = [_taint(taint) for taint in pool.taints]
        configs.append(NodePoolConfig(**fields))
    return configs
