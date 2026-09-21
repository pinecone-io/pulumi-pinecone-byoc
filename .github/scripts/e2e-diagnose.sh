#!/usr/bin/env bash
# Run with KUBECONFIG pointing at the cell after a failed `pulumi up`. Prints what
# the Pulumi diagnostics cannot: nodes and their architecture, every pod that is
# not Running/Succeeded with its events, and the pinetools install job log.
set -uo pipefail
k() { kubectl "$@" 2>&1; }

echo "::group::nodes"
k get nodes -L kubernetes.io/arch,node.kubernetes.io/instance-type,topology.kubernetes.io/zone
echo "--- taints"
k get nodes -o json | jq -r '.items[] | "\(.metadata.name): \((.spec.taints // []) | map("\(.key)=\(.value // "")\(.effect)") | join(" "))"'
echo "::endgroup::"

echo "::group::cluster-autoscaler status"
k -n kube-system get configmap cluster-autoscaler-status -o jsonpath='{.data.status}' | head -60
echo "::endgroup::"

echo "::group::warning events (last 40)"
k get events -A --field-selector type=Warning --sort-by=.lastTimestamp | tail -40
echo "::endgroup::"

echo "::group::pods not Running/Succeeded"
k get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded -o wide
echo "::endgroup::"

echo "::group::pods with unready or crash-looping containers"
k get pods -A -o json | jq -r '
  .items[] | select(.status.phase == "Running") |
  select(any(.status.containerStatuses[]?; (.ready | not) or .restartCount >= 3)) |
  "\(.metadata.namespace)/\(.metadata.name) restarts=\([.status.containerStatuses[].restartCount] | add) waiting=\([.status.containerStatuses[] | .state.waiting.reason // empty] | join(","))"'
echo "::endgroup::"

k get pods -A -o json | jq -r '
  .items[] | select(.status.phase != "Running" and .status.phase != "Succeeded"
    or any(.status.containerStatuses[]?; (.ready | not) or .restartCount >= 3)) |
  "\(.metadata.namespace) \(.metadata.name)"' | head -40 | while read -r ns pod; do
  echo "::group::describe $ns/$pod"
  k describe pod -n "$ns" "$pod" | sed -n '/^Events:/,$p'
  echo "::endgroup::"
  echo "::group::logs $ns/$pod (last 60 lines, all containers, previous if restarted)"
  k logs -n "$ns" "$pod" --all-containers --tail=60 --prefix
  k logs -n "$ns" "$pod" --all-containers --tail=60 --prefix --previous 2>/dev/null
  echo "::endgroup::"
done

echo "::group::pinetools install job"
k get jobs -n pc-control-plane
for job in $(k get jobs -n pc-control-plane -o name | grep -E 'pinetools-install'); do
  k logs -n pc-control-plane "$job" --all-containers --tail=300
done
echo "::endgroup::"
