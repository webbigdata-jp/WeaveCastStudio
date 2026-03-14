# =============================================================================
# WeaveCastStudio — Infrastructure as Code (Terraform)
#
# Resources:
#   - GCE instance (data collection backend)
#   - GCS bucket (GCE→PC sync)
#   - Service account + IAM bindings
#   - Firewall rule (SSH)
#
# Usage:
#   terraform init
#   terraform plan
#   terraform apply
#   terraform destroy   # クリーンアップ
# =============================================================================

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

# -----------------------------------------------------------------------------
# Service Account — GCEインスタンス用
# -----------------------------------------------------------------------------
resource "google_service_account" "collector" {
  account_id   = "weavecast-collector"
  display_name = "WeaveCast Collector Service Account"
  description  = "Service account for GCE data collection instance"
}

# Storage Object Admin — GCSバケットへの読み書き権限
resource "google_project_iam_member" "collector_storage" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.collector.email}"
}

# -----------------------------------------------------------------------------
# GCS Bucket — GCE→Windows PC 同期用
# -----------------------------------------------------------------------------
resource "google_storage_bucket" "sync" {
  name                        = var.bucket_name
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  force_destroy               = true # デモ用。本番では false 推奨

  lifecycle_rule {
    condition {
      age = 30 # 30日経過したオブジェクトを自動削除（コスト管理）
    }
    action {
      type = "Delete"
    }
  }
}

# -----------------------------------------------------------------------------
# GCE Instance — データ収集バックエンド
# -----------------------------------------------------------------------------
resource "google_compute_instance" "collector" {
  name         = var.instance_name
  machine_type = var.machine_type
  zone         = var.zone

  tags = ["weavecast", "ssh"]

  allow_stopping_for_update = true

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
      size  = var.disk_size_gb
      type  = "pd-balanced"
    }
  }

  network_interface {
    network = "default"
    access_config {
      # Ephemeral public IP
    }
  }

  service_account {
    email  = google_service_account.collector.email
    scopes = ["cloud-platform"]
  }

  metadata_startup_script = file("${path.module}/startup.sh")

  metadata = {
    google-api-key = var.google_api_key
    bucket-name    = var.bucket_name
  }

  labels = {
    app         = "weavecaststudio"
    environment = "demo"
    challenge   = "gemini-live-agent"
  }
}

# -----------------------------------------------------------------------------
# Firewall — SSH アクセス許可
# -----------------------------------------------------------------------------
resource "google_compute_firewall" "ssh" {
  name    = "weavecast-allow-ssh"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = ["0.0.0.0/0"] # デモ用。本番ではIP制限推奨
  target_tags   = ["ssh"]
}

# -----------------------------------------------------------------------------
# Outputs
# -----------------------------------------------------------------------------
output "instance_name" {
  description = "GCE instance name"
  value       = google_compute_instance.collector.name
}

output "instance_external_ip" {
  description = "GCE instance external IP"
  value       = google_compute_instance.collector.network_interface[0].access_config[0].nat_ip
}

output "bucket_url" {
  description = "GCS bucket URL"
  value       = "gs://${google_storage_bucket.sync.name}"
}

output "service_account_email" {
  description = "Service account email"
  value       = google_service_account.collector.email
}

output "ssh_command" {
  description = "SSH command to connect"
  value       = "gcloud compute ssh ${google_compute_instance.collector.name} --zone=${var.zone}"
}
