from .common import CADVISOR, COUNT, RI, by_name, Dash, dashboard, stat, table, thresholds, ts, vm

FILE = "platform-mcp.json"
UID = "platform-mcp"

d = Dash()
SEMCONV = "mcp_server_operation_duration_seconds"
VICTORIA_CALLS = '__name__=~"mcp_.+_call_tool_total"'
SCRAPED = 'count by (job) (last_over_time({__name__=~"mcp_.+", job!=""}[1d]))'
WINDOW = "$__interval"


def relabel(expr, src, dst, rx="(.+)"):
    return f'label_replace({expr}, "{dst}", "$1", "{src}", "{rx}")'


def server(expr):
    return relabel(relabel(expr, "job", "server", "(?:.*/)?(.+)"), "k8s_deployment_name", "server")


def selector(*parts):
    return "{" + ", ".join(p for p in parts if p) + "}"


def tool_calls(window, failed=False, by="server, tool"):
    victoria = f"increase({selector(VICTORIA_CALLS, 'is_error=\"true\"' if failed else '')}[{window}])"
    semconv = (f"increase({SEMCONV}_count"
               f"{selector('mcp_method_name=\"tools/call\"', 'error_type!=\"\"' if failed else '')}[{window}])")
    parts = [relabel(server(victoria), "name", "tool"), relabel(server(semconv), "gen_ai_tool_name", "tool")]
    if failed:
        parts.append(relabel(server(f'increase(toolbox_tool_execution_duration_seconds_count{{error_type!=""}}[{window}])'),
                             "gen_ai_tool_name", "tool"))
    return " or ".join(f"sum by ({by}) ({part})" for part in parts)


def tool_quantile(q, window, by=("server",)):
    buckets = relabel(server(f'increase({SEMCONV}_bucket{{mcp_method_name="tools/call"}}[{window}])'),
                      "gen_ai_tool_name", "tool")
    return f"histogram_quantile({q}, sum by ({', '.join((*by, 'le'))}) ({buckets}))"


def scraped(expr):
    return server(f"({expr}) and on (job) {SCRAPED}")


def sessions(window):
    return (server(f'increase({SEMCONV}_count{{mcp_method_name="initialize"}}[{window}])'),
            server(f'increase({{__name__=~"mcp_.+_initialize_total"}}[{window}])'))


PODS = ("max by (namespace, pod, server) (" + scraped("up") + " or "
        + server(relabel(relabel(f"max by (k8s_namespace_name, k8s_pod_name, k8s_deployment_name) "
                                 f"(last_over_time({SEMCONV}_count[1d]))", "k8s_namespace_name", "namespace"),
                         "k8s_pod_name", "pod"))
        + ")")
REPORTING = f'count(count by (server) ({server("last_over_time({__name__=~\"mcp_.+\"}[5m])")})) or vector(0)'
TOTAL, FAILED = f"sum({tool_calls('$__range')})", f"(sum({tool_calls('$__range', failed=True)}) or vector(0))"

d.row("Overview")
for p in (
    stat("Servers reporting", REPORTING, "short", thresholds(("red", None), ("green", 1)),
         "MCP servers that reported MCP metrics in the last 5 minutes, scraped or sent over OTLP.", decimals=0),
    stat("Tool calls", f"{TOTAL} or vector(0)", "short", thresholds(("blue", None)),
         "Tools the AI clients called on every MCP server in the time range.", decimals=0),
    stat("Failed tool calls", FAILED, "short", thresholds(("green", None), ("orange", 1)),
         "Tool calls that returned an error: a bad query, a timeout or an unreachable backend.", decimals=0),
    stat("Failure ratio", f"{FAILED} / {TOTAL}", "percentunit",
         thresholds(("green", None), ("orange", 0.05), ("red", 0.2)), "Failed tool calls against all tool calls."),
    stat("p95 tool duration, slowest server", f'max({tool_quantile(0.95, "$__range")})', "s",
         thresholds(("green", None), ("orange", 5), ("red", 20)),
         "The highest 95th percentile tool duration among the servers that report the OpenTelemetry MCP metric "
         "mcp.server.operation.duration, over the time range."),
    stat("Sessions started", " + ".join(f"(sum({s}) or vector(0))" for s in sessions("$__range")), "short",
         thresholds(("blue", None)), "MCP sessions the clients opened (initialize) in the time range.", decimals=0),
):
    d.add(p, 4, 4)

d.row("Tool calls")
d.add(ts("Tool calls per server", [vm(tool_calls(WINDOW, by="server"), "{{server}}")], "short",
         bars=True, stack=True, min_=0, decimals=0, desc="Tool calls per interval on each MCP server."), 12, 8)
d.add(ts("Failed tool calls per server", [vm(tool_calls(WINDOW, failed=True, by="server"), "{{server}}")],
         "short", bars=True, stack=True, min_=0, decimals=0, desc="Tool calls that returned an error, per interval."),
      12, 8)
d.add(table("Tools", [vm(tool_calls("$__range"), ref="A", instant=True, fmt="table"),
                      vm(tool_calls("$__range", failed=True), ref="B", instant=True, fmt="table"),
                      vm(tool_quantile(0.95, "$__range", by=("server", "tool")), ref="C", instant=True, fmt="table")],
            [{"id": "merge", "options": {}},
             {"id": "organize", "options": {"excludeByName": {"Time": True},
                                            "indexByName": {"server": 0, "tool": 1, "Value #A": 2, "Value #B": 3,
                                                            "Value #C": 4},
                                            "renameByName": {"server": "Server", "tool": "Tool", "Value #A": "Calls",
                                                             "Value #B": "Failed", "Value #C": "p95"}}}],
            overrides=[COUNT("Calls"), COUNT("Failed"),
                       by_name("Failed", [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                          {"id": "thresholds", "value": thresholds(("green", None), ("red", 1))}]),
                       by_name("p95", [{"id": "unit", "value": "s"}, {"id": "decimals", "value": 2}])],
            sort=[{"displayName": "Calls", "desc": True}],
            desc="Every tool called in the time range: calls, failures and the 95th percentile duration where the "
                 "server reports it. Shows which questions the AI clients ask the platform."), 24, 10)

d.row("Latency and backends")
d.add(ts("Tool duration per server", [vm(tool_quantile(0.5, WINDOW), "p50 {{server}}"),
                                      vm(tool_quantile(0.95, WINDOW), "p95 {{server}}", "B")],
         "s", min_=0, desc="Median and 95th percentile tool duration on the servers that report the OpenTelemetry MCP "
                           "metric. The VictoriaMetrics, VictoriaLogs and VictoriaTraces servers only count calls."),
      12, 8)
d.add(ts("Backend calls from MCP servers",
         [vm(f"sum by (server, server_address, http_response_status_code) "
             f"({scraped(f'rate(http_client_request_duration_seconds_count{RI})')})",
             "{{server}} → {{server_address}} {{http_response_status_code}}")],
         "reqps", stack=True, min_=0, desc="HTTP calls an MCP server makes to the system it wraps (for example "
                                          "Grafana), by status. Only servers with an instrumented HTTP client report "
                                          "this."), 12, 8)

d.row("Sessions and protocol")
semconv_sessions, victoria_sessions = sessions(WINDOW)
d.add(ts("Sessions by client",
         [vm(f"sum by (server, client_name) ({victoria_sessions})", "{{server}} {{client_name}}"),
          vm(f"sum by (server) ({semconv_sessions})", "{{server}}", "B")],
         "short", bars=True, min_=0, decimals=0,
         desc="MCP sessions opened per server, and per client where the server records the client name."), 12, 8)
d.add(ts("Requests by MCP method",
         [vm("sum by (server, method) ("
             + server(relabel(f'increase({{__name__=~"mcp_.+_total", __name__!~"mcp_client_.+"}}[{WINDOW}]) '
                              "keep_metric_names", "__name__", "method", "mcp_[a-z]+_(.+)_total")) + ")",
             "{{server}} {{method}}"),
          vm(f"sum by (server, method) ({relabel(server(f'increase({SEMCONV}_count[{WINDOW}])'), 'mcp_method_name', 'method')})",
             "{{server}} {{method}}", "B")],
         "short", bars=True, stack=True, min_=0, decimals=0,
         desc="Protocol requests per method: initialize, tools/list, resources/list, prompts/list and tool calls."),
      12, 8)

d.row("Resources")
d.add(ts("Memory per server",
         [vm(f'sum by (server) (container_memory_working_set_bytes{{{CADVISOR}, container!="", container!="POD"}} '
             f"* on (namespace, pod) group_left (server) {PODS})", "{{server}}")],
         "bytes", min_=0, desc="Working set of the MCP server pods."), 12, 7)
d.add(ts("CPU per server",
         [vm(f'sum by (server) (rate(container_cpu_usage_seconds_total{{{CADVISOR}, container!="", container!="POD"}}{RI}) '
             f"* on (namespace, pod) group_left (server) {PODS})", "{{server}}")],
         "short", min_=0, desc="CPU cores used by the MCP server pods."), 12, 7)

DASHBOARD = dashboard(UID, "Platform — MCP servers", ["platform", "mcp"], d.panels, [],
                      "The MCP servers that give AI clients access to metrics, logs, traces, Grafana and PostgreSQL: "
                      "which tools are called, failures, durations, sessions, backend calls and resources. Servers are "
                      "discovered from the MCP metrics they report.")
