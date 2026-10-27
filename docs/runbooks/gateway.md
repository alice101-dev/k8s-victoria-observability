# Gateway runbook (kgateway, Gateway `edge`)

The alerts of the `kgateway` VMRule (`infrastructure/configs/kgateway/base/monitoring.yaml`) link to the sections below.

| What | Where |
|---|---|
| Gateway and proxy settings | `infrastructure/configs/kgateway/base/gateway.yaml` (Gateway `edge`, GatewayParameters `edge`) |
| Telemetry | `infrastructure/configs/kgateway/base/telemetry.yaml` (ListenerPolicy `edge-telemetry`) |
| Controller | `infrastructure/controllers/kgateway`, Deployment `kgateway` in `kgateway-system` |
| Proxies | Deployment `edge` in `kgateway-system`, Service `edge` (ClusterIP, port 80) |
| Dashboard | Grafana → Platform → Gateway — Edge (`/d/gateway-edge`) |
| Access logs | LogsQL `{service.name="edge-gateway"}` (OTLP, one record per request, with `trace_id`) |
| Traces | VictoriaTraces, service `edge-gateway`: one `ingress` span per request and one span per upstream call |
| Metrics | `envoy_*{job="kgateway-system/kgateway-proxy"}`; listener `envoy_http_conn_manager_prefix="http"`; upstream `envoy_cluster_name="kube_<namespace>_<service>_<port>"` |

Routes are accepted from namespaces labelled `platform/gateway: edge` only. The controller only discovers those
namespaces, `kgateway-system` and `monitoring` (`discoveryNamespaceSelectors` in `infrastructure/controllers/kgateway/values.yaml`), so a
backend in another namespace has no Envoy cluster.

```text
# LogsQL: requests per status and route in the last hour
_time:1h {service.name="edge-gateway"} | stats by (http.response.status_code, envoy.route_name) count() requests

# LogsQL: failed requests with the Envoy response flags (UH no healthy upstream, UF connection failure, UT timeout, NR no route)
_time:1h {service.name="edge-gateway"} http.response.status_code:~"^5" | fields _time, _msg, envoy.response_flags, envoy.upstream_cluster, trace_id
```

## GatewayProxyDown

**Meaning.** No `edge` proxy replica has been scraped as up for 2 minutes. Every request through the gateway fails.

**Diagnose.** `kubectl -n kgateway-system get pods -l gateway.networking.k8s.io/gateway-name=edge`, their events and
logs. The proxies get their configuration from the controller over xDS (port 9977): check `KgatewayControllerDown`.
After a node loss both replicas may be pending (`KubePodsUnschedulable`).

**Fix.** Restore scheduling or the controller; the Deployment recreates the proxies.

## GatewayProxyReplicasMissing

**Meaning.** Fewer than 2 proxy replicas have been up for 10 minutes. Traffic still flows, without redundancy.

**Diagnose and fix.** As for GatewayProxyDown. The PodDisruptionBudget allows one replica down during drains.

## KgatewayControllerDown

**Meaning.** The kgateway controller has not been scraped as up for 5 minutes. Running proxies keep their last
configuration; changes to Gateways, HTTPRoutes and policies are not applied, and new proxies cannot start.

**Diagnose.** `kubectl -n kgateway-system get pods -l app.kubernetes.io/name=kgateway`, events and logs. A missing
CRD (Gateway API or kgateway) shows in the logs at startup: check `config-gateway-api` and `kgateway-crds` in Argo CD.

**Fix.** Restore the controller; it resends the configuration to the proxies.

## KgatewayTranslationErrors

**Meaning.** Reconciliations, translations or status syncs of the controller failed during the last 10 minutes.
Invalid resources are replaced by a direct 500 response (validation level `standard`); the rest keeps working.

**Diagnose.** The status conditions name the object: `kubectl get gateway,httproute,listenerpolicy,trafficpolicy -A
-o wide`, then `kubectl describe` the one with `Accepted=False` or `ResolvedRefs=False`. Controller logs:
`kubectl -n kgateway-system logs deploy/kgateway`.

**Fix.** Correct the resource in Git. A cross-namespace reference needs a ReferenceGrant in the target namespace.

## GatewayHighErrorRate

**Meaning.** More than 5% of the requests at the gateway listener answered 5xx for 10 minutes.

**Diagnose.** Status and response flags in the access logs (recipes above): `UH` no healthy endpoint, `UF` connection
failure, `UT` upstream timeout come from the gateway; plain 5xx without flags come from the backend. Gateway — Edge
dashboard: Upstreams row. Open a failing request's `trace_id` to follow it into the service.

**Fix.** Depends on the backend: `GatewayUpstreamErrors` names it.

## GatewayUpstreamErrors

**Meaning.** One backend (`envoy_cluster_name`, `kube_<namespace>_<service>_<port>`) answered more than 5% 5xx through
the gateway for 10 minutes.

**Diagnose.** The service's own logs and traces at the same time, its pods (`kubectl -n <namespace> get pods`), and the
healthy endpoints of that cluster on the dashboard (0 healthy endpoints means `UH` responses).

**Fix.** Fix or roll back the service.
