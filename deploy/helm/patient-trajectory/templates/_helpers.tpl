{{- define "trajectory.name" -}}
{{- printf "%s-trajectory" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- define "trajectory.labels" -}}
app.kubernetes.io/name: patient-trajectory
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}
