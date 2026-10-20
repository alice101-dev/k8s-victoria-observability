{{/* Selector labels — immutable on Deployments, never add labels that can change (e.g. version). */}}
{{- define "shop.selectorLabels" -}}
app.kubernetes.io/name: {{ . }}
app.kubernetes.io/part-of: shop
{{- end -}}

{{/* Standard labels. "component: backend" is used by VMServiceScrape to select app services. */}}
{{- define "shop.labels" -}}
{{ include "shop.selectorLabels" .name }}
app.kubernetes.io/component: backend
app.kubernetes.io/version: {{ .root.Values.image.tag | quote }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
{{- end -}}
