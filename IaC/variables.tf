variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "asia-northeast1"
}

variable "zone" {
  description = "GCP zone"
  type        = string
  default     = "asia-northeast1-b"
}

variable "instance_name" {
  description = "GCE instance name"
  type        = string
  default     = "weavecast-collector"
}

variable "machine_type" {
  description = "GCE machine type"
  type        = string
  default     = "e2-small"
}

variable "disk_size_gb" {
  description = "Boot disk size in GB"
  type        = number
  default     = 30
}

variable "bucket_name" {
  description = "GCS bucket name for sync"
  type        = string
  default     = "weavecaststudio-sync"
}

variable "google_api_key" {
  description = "Google AI Studio API key (Gemini)"
  type        = string
  sensitive   = true
}
