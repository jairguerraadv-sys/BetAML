{{/*
Expand the name of the chart.
*/}}
{{- define "betaml.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "betaml.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart label.
*/}}
{{- define "betaml.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "betaml.labels" -}}
helm.sh/chart: {{ include "betaml.chart" . }}
{{ include "betaml.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "betaml.selectorLabels" -}}
app.kubernetes.io/name: {{ include "betaml.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Resolve image tag: component tag > global tag.
*/}}
{{- define "betaml.imageTag" -}}
{{- $componentTag := .componentTag -}}
{{- $globalTag := .globalTag -}}
{{- if and $componentTag (ne $componentTag "") }}
{{- $componentTag }}
{{- else }}
{{- $globalTag }}
{{- end }}
{{- end }}
