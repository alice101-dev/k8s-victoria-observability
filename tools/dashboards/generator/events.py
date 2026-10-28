from .common import (ADHOC, COUNT, LINKS_ON, NODE_BOOT, RI, SEARCH, SEARCH_VAR, VL, WHEN, by_name, Dash,
    dashboard, logs_panel, logsql_table, restart_annotations, right_axis, stat, status_history, table,
    thresholds, ts, vl, vl_stat, vm)

FILE = "cluster-events.json"

d = Dash()
EV = '{service.name="kubernetes-events"}'
EVENTS = f'{EV} (k8s.namespace.name:in($namespace) or k8s.namespace.name:="") {SEARCH}'
EVENTS_TYPE = f"{EVENTS} severity_text:in($type)"
WARNING = "severity_text:=Warning"
NS_RE = 'namespace=~"${namespace:regex}"'
NAMESPACE = '| copy k8s.namespace.name as namespace | format if (namespace:="") "(cluster)" as namespace '
EVENT_LINE = ('| format "<k8s.object.kind>/<k8s.object.name> <k8s.event.reason>: <_msg>" as _msg '
              '| format if (severity_text:=Warning) "warning" as level | format if (severity_text:=Normal) "info" as level')
EV_SELF = "/d/cluster-events/cluster-events?${namespace:queryparam}&${type:queryparam}&${__url_time_range}"
OBJECT_LINK = [{"title": "Show this object's events in the explorer",
                "url": EV_SELF + "&var-filters=k8s.object.name%7C%3D%7C${__value.raw}"}]
NODE_LINKS = [{"title": "Open Kubernetes / Views / Nodes", "url": "/d/k8s_views_nodes/kubernetes-views-nodes?${__url_time_range}"}]

d.row("Overview — $namespace")
for p in (
    vl_stat("Warning events", f"{EVENTS} {WARNING} | stats count() events", "short",
            thresholds(("green", None), ("orange", 1)), "Events of type Warning, including repeats of the same event."),
    vl_stat("Pods that could not be scheduled", f"{EVENTS} k8s.event.reason:=FailedScheduling "
            "| stats count_uniq(k8s.object.name) pods", "short", thresholds(("green", None), ("red", 1)),
            "Pods the scheduler could not place: not enough CPU or memory, node affinity, taints or volumes. After a "
            "spot preemption the remaining nodes may be too full.", sparkline=False),
    vl_stat("Failed probes", f"{EVENTS} k8s.event.reason:=Unhealthy | stats count() events", "short",
            thresholds(("green", None), ("orange", 1)), "Readiness and liveness probe failures (the pod leaves its "
            "Service, or the container is restarted)."),
    vl_stat("OOM kills", f'{EV} k8s.event.reason:=OOMKilling {SEARCH} | stats count_uniq(_msg) kills', "short",
            thresholds(("green", None), ("red", 1)), "Processes the kernel killed at a container memory limit, reported "
            "by node-problem-detector on the node (all namespaces; the event names the process, the pod shows under "
            "Container restarts).", sparkline=False),
    stat("Container restarts", f"sum(round(increase(kube_pod_container_status_restarts_total{{{NS_RE}}}[$__range])))",
         "short", thresholds(("green", None), ("orange", 1)), "Container restarts in the time range; the reasons are "
         "in the Container restarts table.", decimals=0, no_value="0"),
    stat("Node restarts", f"count(time() - {NODE_BOOT} < $__range_s)", "short", thresholds(("green", None), ("orange", 1)),
         "Nodes that booted in the time range: spot VM preemption, node upgrade or repair, or a new node. Their pods "
         "were rescheduled; the Nodes row shows when.", decimals=0, no_value="0"),
):
    d.add(p, 4, 4)

d.row("Trends")
d.add(ts("Warning events by reason", [vl(f"{EVENTS} {WARNING} | stats by (k8s.event.reason) count() events",
                                         "statsRange", legend="{{k8s.event.reason}}")],
         "short", ds=VL, stack=True, bars=True, legend_calcs=("sum",),
         desc="FailedScheduling: no node fits. Unhealthy: probe failed. BackOff: crash or image pull loop. Failed: "
              "image pull or container start failed. OOMKilling: memory limit. Evicted / Preempting: node pressure "
              "or a higher-priority pod."), 12, 8)
d.add(status_history("Warning events per namespace",
                     vl(f"{EVENTS} {WARNING} {NAMESPACE}| stats by (namespace) count() events", "statsRange",
                        legend="{{namespace}}"),
                     "When each namespace had warnings; (cluster) is nodes and other cluster-scoped objects.",
                     thresholds(("green", None), ("orange", 1), ("red", 20))), 12, 8)

d.row("Nodes — restarts and spot preemptions")
POOL = "label_cloud_google_com_gke_nodepool"
PROVISIONING = "label_cloud_google_com_gke_provisioning"
NODE_LABELS = (f"max by (node, {POOL}, {PROVISIONING}, label_cloud_google_com_gke_preemptible, "
               "label_node_kubernetes_io_instance_type, label_topology_kubernetes_io_zone) (kube_node_labels)")
BY_POOL = f'* on (node) group_left ({POOL}) max by (node, {POOL}) (kube_node_labels{{{POOL}!=""}})'
NODE_CAUSE = (f"{EV} k8s.object.kind:=Node k8s.event.reason:in(ScaleDown, Shutdown, NodeShutdown, Rebooted, RegisteredNode, "
              "NodeNotReady) | copy k8s.object.name as node "
              '| format if (k8s.event.reason:=NodeNotReady) "1" as cause | format if (k8s.event.reason:=RegisteredNode) "2" as cause '
              '| format if (k8s.event.reason:=Rebooted) "3" as cause | format if (k8s.event.reason:in(Shutdown, NodeShutdown)) "4" as cause '
              '| format if (k8s.event.reason:=ScaleDown) "5" as cause '
              "| stats by (node) max(cause) cause, max(_time) last | math round(last/1e6) as cause_ms | delete last")
CAUSES = {"5": ("Autoscaler scale-down", "blue"), "4": ("Shutdown: spot preemption or maintenance", "orange"),
          "3": ("Rebooted: preemption, repair or reset", "orange"), "2": ("Joined: scale-up or recreated VM", "green"),
          "1": ("NotReady", "red")}
d.add(table("Nodes", [vm(f"{NODE_BOOT} * 1000", ref="A", instant=True, fmt="table"),
                      vm(f"time() - {NODE_BOOT} < bool $__range_s", ref="B", instant=True, fmt="table"),
                      vm('max by (node) (kube_node_status_condition{condition="Ready", status="true"})', ref="C",
                         instant=True, fmt="table"),
                      vm(NODE_LABELS, ref="D", instant=True, fmt="table"),
                      vl(NODE_CAUSE, "stats", ref="E")],
            [{"id": "labelsToFields", "options": {"mode": "columns", "valueLabel": "__name__"}},
             {"id": "merge", "options": {}},
             {"id": "organize", "options": {
                 "excludeByName": {"Time": True, "Value #D": True},
                 "indexByName": {POOL: 0, "node": 1, PROVISIONING: 2, "label_cloud_google_com_gke_preemptible": 3,
                                 "label_node_kubernetes_io_instance_type": 4, "label_topology_kubernetes_io_zone": 5,
                                 "Value #A": 6, "Value #B": 7, "cause": 8, "cause_ms": 9, "Value #C": 10},
                 "renameByName": {POOL: "Node pool", "node": "Node", PROVISIONING: "Provisioning",
                                  "label_cloud_google_com_gke_preemptible": "Preemptible",
                                  "label_node_kubernetes_io_instance_type": "Machine type",
                                  "label_topology_kubernetes_io_zone": "Zone", "Value #A": "Booted",
                                  "Value #B": "Booted in range", "cause": "Cause (time range)",
                                  "cause_ms": "Last node event", "Value #C": "Ready"}}}],
            overrides=[by_name("Node pool", [{"id": "custom.width", "value": 170}]),
                       by_name("Provisioning", [{"id": "custom.width", "value": 190}, {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                                {"id": "mappings", "value": [{"type": "value", "options": {
                                                    "spot": {"text": "Spot (can be preempted)", "color": "orange", "index": 0},
                                                    "preemptible": {"text": "Preemptible (can be preempted)", "color": "orange", "index": 1},
                                                    "standard": {"text": "On-demand", "color": "green", "index": 2}}}]}]),
                       by_name("Preemptible", [{"id": "custom.width", "value": 100}]),
                       by_name("Machine type", [{"id": "custom.width", "value": 130}]),
                       by_name("Zone", [{"id": "custom.width", "value": 150}]),
                       by_name("Booted", [{"id": "unit", "value": "dateTimeFromNow"}, {"id": "custom.width", "value": 130}]),
                       by_name("Booted in range", [{"id": "custom.width", "value": 130}, {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                                      {"id": "mappings", "value": [{"type": "value", "options": {
                                                          "1": {"text": "Yes", "color": "orange", "index": 0},
                                                          "0": {"text": "No", "color": "text", "index": 1}}}]}]),
                       by_name("Cause (time range)", [{"id": "custom.width", "value": 290}, {"id": "noValue", "value": "No node event"},
                                                      {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                                      {"id": "mappings", "value": [{"type": "value", "options": {
                                                          code: {"text": text, "color": color, "index": i}
                                                          for i, (code, (text, color)) in enumerate(CAUSES.items())}}]}]),
                       by_name("Last node event", [{"id": "unit", "value": "dateTimeFromNow"}, {"id": "custom.width", "value": 130}]),
                       by_name("Ready", [{"id": "custom.width", "value": 90}, {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                                         {"id": "mappings", "value": [{"type": "value", "options": {
                                             "1": {"text": "Ready", "color": "green", "index": 0},
                                             "0": {"text": "NotReady", "color": "red", "index": 1}}},
                                             {"type": "special", "options": {"match": "null", "result": {
                                                 "text": "Removed", "color": "#808080", "index": 2}}}]}])],
            desc="Every node with its GKE node pool, provisioning model and machine type, when it last booted and whether "
                 "that was in the time range (a restart, or a new node). Spot (and preemptible) nodes can be reclaimed by Compute Engine at any time: "
                 "a recent boot there usually is a preemption, after which GKE recreated the VM under the same name and its "
                 "pods were rescheduled. On-demand nodes reboot only for upgrades or repairs. Cause: the most telling "
                 "node event in the time range (autoscaler ScaleDown, Shutdown, Rebooted, RegisteredNode, NodeNotReady); "
                 "a node removed by the autoscaler stays listed for a few minutes.",
            sort=[{"displayName": "Node pool", "desc": False}]), 24, 7)
d.panels[-1]["datasource"] = {"type": "datasource", "uid": "-- Mixed --"}
d.add(ts("Ready nodes by node pool",
         [vm(f'sum by ({POOL}) (kube_node_status_condition{{condition="Ready", status="true"}} {BY_POOL})', f"{{{{{POOL}}}}} ready"),
          vm(f"count by ({POOL}) (kube_node_info {BY_POOL})", f"{{{{{POOL}}}}} nodes", "B")],
         "short", decimals=0, min_=0, legend_calcs=("min", "max"),
         desc="Ready nodes against all nodes, per node pool: a dip is a node lost (preemption, upgrade) until it is back; "
              "a rise is the autoscaler adding a node."), 10, 8)
d.add(logs_panel("Node events", f"{EV} k8s.object.kind:=Node {SEARCH} {EVENT_LINE}",
                 "Events on nodes: NodeNotReady, Rebooted, Starting (kubelet), RegisteredNode, OOMKilling and other "
                 "node-problem-detector findings."), 14, 8)
for p in d.panels[-3:]:
    p["links"] = NODE_LINKS

d.row("Pods and workloads — why they fail")
d.add(logsql_table("Warning events by object",
                   f"{EVENTS_TYPE} {NAMESPACE}"
                   r'| replace_regexp ("\\b[0-9]+\\b", "<N>") at _msg '
                   "| stats by (namespace, k8s.object.kind, k8s.object.name, k8s.event.reason, _msg) count() events, "
                   "max(_time) last | math round(last/1e6) as last_ms | delete last | sort by (last_ms desc) | limit 100",
                   [("namespace", "Namespace"), ("k8s.object.kind", "Kind"), ("k8s.object.name", "Object"),
                    ("k8s.event.reason", "Reason"), ("_msg", "Message"), ("events", "Events"), ("last_ms", "Last seen")],
                   "Events of the selected types per object and message (numbers collapsed), newest first. Click an "
                   "object to list its events in the explorer.",
                   VL, overrides=[by_name("Namespace", [{"id": "custom.width", "value": 130}]),
                                  by_name("Kind", [{"id": "custom.width", "value": 110}]),
                                  LINKS_ON("Object", OBJECT_LINK, 280), by_name("Reason", [{"id": "custom.width", "value": 140}]),
                                  COUNT("Events", 70), WHEN("Last seen", "dateTimeFromNow")],
                   sort=[{"displayName": "Last seen", "desc": True}]), 24, 10)
RESTARTS = f"sum by (namespace, pod, container) (round(increase(kube_pod_container_status_restarts_total{{{NS_RE}}}[$__range]))) > 0"
d.add(table("Container restarts",
            [vm(f"max by (namespace, pod, container, reason) (max_over_time(kube_pod_container_status_last_terminated_reason{{{NS_RE}}}[$__range])) "
                f"* on (namespace, pod, container) group_left () ({RESTARTS}) or on (namespace, pod, container) ({RESTARTS})",
                instant=True, fmt="table")],
            [{"id": "organize", "options": {"excludeByName": {"Time": True},
                                            "indexByName": {"namespace": 0, "pod": 1, "container": 2, "reason": 3, "Value": 4},
                                            "renameByName": {"namespace": "Namespace", "pod": "Pod", "container": "Container",
                                                             "reason": "Termination reason", "Value": "Restarts"}}}],
            overrides=[COUNT("Restarts", 80), by_name("Termination reason", [{"id": "custom.width", "value": 160}])],
            desc="Containers that restarted in the time range and why their runs ended: OOMKilled (memory limit), "
                 "Error (non-zero exit, failed liveness probe) or Completed. Pods deleted since keep their row.",
            sort=[{"displayName": "Restarts", "desc": True}]), 14, 8)
d.add(ts("Pending pods", [vm(f'sum by (namespace) (kube_pod_status_phase{{phase="Pending", {NS_RE}}}) > 0', "{{namespace}}")],
         "short", stack=True, decimals=0, min_=0, legend_calcs=("max",),
         desc="Pods waiting for a node or for their images. Persisting after a preemption means the remaining "
              "nodes are too full (see FailedScheduling)."), 10, 8)

d.row("Event explorer")
d.add(logs_panel("Events — $type", f"{EVENTS_TYPE} {EVENT_LINE}",
                 "Every event as <Kind>/<name> <reason>: <message>. Expand an event for the object, namespace, node and "
                 "count. Narrow with Message contains and Filters (any field)."), 24, 14)

d.row("Event pipeline")
for p in (
    d.add(ts("Events collected", [
        vm(f"sum(rate(otelcol_receiver_accepted_log_records{RI}))", "received from the API server"),
        vm(f"sum(rate(otelcol_exporter_sent_log_records{RI}))", "stored in VictoriaLogs", "B"),
        vm(f"sum(rate(otelcol_exporter_send_failed_log_records{RI}))", "failed to store", "C")],
        "short", min_=0, desc="Events per second through the k8s-events collector. Failed sends are retried from "
        "its queue; the KubeEventsExportFailing alert fires when they persist."), 12, 6),
    d.add(ts("Collector queue and memory", [
        vm("sum(otelcol_exporter_queue_size)", "queued events"),
        vm("sum(otelcol_exporter_queue_capacity)", "queue capacity", "B"),
        vm("sum(otelcol_process_memory_rss)", "memory", "C")],
        "short", min_=0, overrides=[right_axis("C", "bytes")],
        desc="Events waiting to be sent (left) and the collector's memory (right). A full queue drops events."), 12, 6),
):
    p["links"] = [{"title": "Open VictoriaLogs - single-node", "url": "/d/OqPIZTX4z/victorialogs-single-node?${__url_time_range}"}]

for p in d.panels:
    if p["type"] in ("timeseries", "status-history") and p["datasource"] is VL:
        p["interval"] = "1m"

EV_NAMESPACE = {"name": "namespace", "label": "Namespace", "type": "query", "datasource": VL,
                "query": {"type": "fieldValue", "field": "k8s.namespace.name", "query": f"{EV} k8s.namespace.name:*",
                          "limit": 200, "refId": "namespace"},
                "definition": f"k8s.namespace.name: {EV}", "refresh": 2, "sort": 1, "multi": True, "includeAll": True,
                "current": {"selected": True, "text": ["All"], "value": ["$__all"]}}
EV_TYPE = {"name": "type", "label": "Type", "type": "custom", "query": "Warning,Normal", "multi": True, "includeAll": True,
           "current": {"selected": True, "text": ["Warning"], "value": ["Warning"]},
           "options": [{"selected": v == "Warning", "text": v, "value": v} for v in ("Warning", "Normal")]}
events = dashboard("cluster-events", "Cluster — Events", ["platform", "kubernetes", "events"], d.panels,
                   [EV_NAMESPACE, EV_TYPE, SEARCH_VAR, ADHOC],
                   "Kubernetes events from VictoriaLogs (all namespaces): warnings by reason and namespace, node restarts "
                   "and spot preemptions, why pods fail (scheduling, probes, OOM, image pulls), restarts and the event "
                   "pipeline.", "now-6h")
events["annotations"] = {"list": restart_annotations(NS_RE)}
DASHBOARD = events
