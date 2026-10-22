# Kubernetes runbook (events, nodes, scheduling)

The alerts of the `k8s-events` VMRule (`observability/k8s-events/monitoring.yaml`) link to the sections below.
Kubernetes events reach VictoriaLogs through the `k8s-events` collector (OpenTelemetry Collector, `k8s_events`
receiver, namespace `monitoring`); the API server itself keeps them for one hour only. The **Cluster — Events**
dashboard (Grafana folder **Platform**) shows them by reason, namespace and object, with node restarts, container
restarts and pending pods. All nodes are spot VMs: Compute Engine can reclaim one at any time, GKE recreates it
under the same name and its pods are rescheduled.

```text
# LogsQL: warning events of the last hour by namespace, reason and object
_time:1h {service.name="kubernetes-events"} severity_text:=Warning
  | stats by (k8s.namespace.name, k8s.event.reason, k8s.object.kind, k8s.object.name) count() events | sort by (events desc)

# LogsQL: everything that happened to one object (pod, node, deployment ...)
_time:1h {service.name="kubernetes-events"} k8s.object.name:=<name> | fields _time, k8s.event.reason, _msg
```

Event fields: `_msg` (the event message), `k8s.event.reason`, `severity_text` (`Normal`, `Warning`),
`k8s.object.kind`, `k8s.object.name`, `k8s.namespace.name` (empty for nodes), `k8s.node.name` (for kubelet and
node-problem-detector events), `k8s.event.count`. A repeated event is stored once per repetition.

## KubeNodeRestarted

**Meaning.** A node booted in the last 15 minutes (severity `info`, not paged): spot preemption, node upgrade or
auto-repair, or a new node.

**Diagnose.** Nodes row of the Cluster — Events dashboard (boot time, Ready nodes, node events such as
`NodeNotReady`, `Rebooted`, `Starting`). `kubectl get nodes`, `kubectl get pods -A -o wide --field-selector
spec.nodeName=<node>`. Check that the stateful workloads recovered: CloudNativePG (`kubectl -n shop get cluster
shop-db`), Redis (`RedisNoSingleMaster`), the VictoriaMetrics, VictoriaLogs and VictoriaTraces storage pods.

**Fix.** Nothing when everything is back. Pods left `Pending` are covered by `KubePodsUnschedulable`.

## KubePodsUnschedulable

**Meaning.** For 10 minutes the scheduler found no node for one or more pods of the namespace.

**Diagnose.** The `FailedScheduling` message says why, for example `0/3 nodes are available: 3 Insufficient
cpu` (Warning events by object table, or `kubectl describe pod <pod>`). After a preemption the remaining nodes may
not fit every request until the node is back; required anti-affinity (Redis, one pod per node) and volume zones
also limit where a pod can go.

**Fix.** Wait for the recreated node when a node is missing. Otherwise lower the pod's requests, or add capacity
(node pool size in `terraform/environments/staging/kubernetes-engine`).

## KubeContainerOOMKilled

**Meaning.** The kernel killed a process because its container reached its memory limit (reported by GKE's
node-problem-detector as an `OOMKilling` event on the node).

**Diagnose.** The event names the process and the node; the Container restarts table shows the pod with last
termination `OOMKilled`. Compare the container's memory with its limit (Kubernetes / Compute Resources / Pod).
For the Go services, `GOMEMLIMIT` follows the limit, so a kill points at memory outside the Go heap or a limit
that is too low for the load.

**Fix.** Raise the memory limit (and request) of the workload in Git, or find what grows (heap profile, cache
sizes, query results).

## KubeImagePullFailing

**Meaning.** The kubelet could not pull a pod's image for at least 5 minutes.

**Diagnose.** The event message has the registry's answer: `not found` (wrong tag or image), `unauthorized` or
`denied` (registry access of the node service account), `i/o timeout` (network, Cloud NAT). `kubectl describe pod
<pod>`.

**Fix.** Correct the image tag in Git (the Shop images are released with `make -C apps release`), or the
registry permissions. A rollout waits on the new pods and keeps the old ones running meanwhile.

## KubeEventsCollectorDown

**Meaning.** vmagent has not scraped the `k8s-events` collector for 10 minutes: Kubernetes events are not stored.

**Diagnose.** `kubectl -n monitoring get pods -l app.kubernetes.io/instance=k8s-events`, its logs and events;
Argo CD application `k8s-events`.

**Fix.** Restart or resync it. Events from the outage are lost once the API server drops them (after one hour).

## KubeEventsExportFailing

**Meaning.** For 10 minutes the collector failed to send events to VictoriaLogs (`vlinsert`).

**Diagnose.** Collector logs (`kubectl -n monitoring logs deploy/k8s-events`), the Event pipeline row (queue
size), VictoriaLogs health (`vlinsert` and `vlstorage` pods, the VictoriaLogs - cluster dashboard).

**Fix.** Fix VictoriaLogs; the collector retries from its in-memory queue and drops events when it is full.
