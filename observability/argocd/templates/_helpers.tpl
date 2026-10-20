{{/*
Standard syncPolicy for all child Applications:
- automated + prune + selfHeal  : Git is the source of truth, manual drift is reverted.
- ServerSideApply               : VictoriaMetrics/ESO CRDs are too large for client-side apply.
- RespectIgnoreDifferences      : ignoreDifferences also honored on apply (webhook certs).
- retry with backoff            : CRDs from other Applications may not be ready on first sync.
*/}}
{{- define "app.syncPolicy" -}}
syncPolicy:
  automated:
    prune: true
    selfHeal: true
  syncOptions:
    - CreateNamespace=true
    - ServerSideApply=true
    - RespectIgnoreDifferences=true
    - SkipDryRunOnMissingResource=true
  retry:
    limit: 10
    backoff:
      duration: 30s
      factor: 2
      maxDuration: 5m
{{- end -}}

{{/* repoURL is required — injected by the root Application (Terraform), never hardcoded in Git. */}}
{{- define "app.repoURL" -}}
{{- required "repoURL is required (injected by the root Application from Terraform argocd-bootstrap)" .Values.repoURL -}}
{{- end -}}
