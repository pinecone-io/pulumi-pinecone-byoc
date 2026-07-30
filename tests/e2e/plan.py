"""Picking the URNs a targeted apply needs, out of a `pulumi preview --json` plan."""

VPC_COMPONENT_TYPE = "pinecone:byoc:VPC"


def urn_type_chain(urn):
    """The parent chain of types a URN carries, e.g. Cluster$VPC$Subnet."""
    parts = urn.split("::")
    return parts[2].split("$") if len(parts) == 4 else []


def network_targets(plan):
    """The URNs to hand `--target`, taken from a `pulumi preview --json` plan.

    Everything under the VPC component, plus the components it hangs off: a
    parent counts as a dependency, and the engine bails with "untargeted create"
    on a resource whose parent it skipped. Component shells create nothing in the
    cloud, so that costs nothing. Providers and the root stack are targeted
    implicitly by the engine and are left out. A type chain that merely starts
    like the VPC's, as the Datadog key's does, is not an ancestor of it.
    """
    urns = [step.get("urn", "") for step in plan.get("steps", [])]
    chains = {urn: urn_type_chain(urn) for urn in urns}
    vpc_chains = [chain for chain in chains.values() if VPC_COMPONENT_TYPE in chain]

    def wanted(chain):
        if not chain:
            return False
        if VPC_COMPONENT_TYPE in chain:
            return True
        return any(vpc[: len(chain)] == chain for vpc in vpc_chains)

    return [urn for urn in urns if wanted(chains[urn])]
