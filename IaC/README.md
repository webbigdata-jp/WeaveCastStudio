# WeaveCastStudio — Infrastructure as Code (Terraform)

Terraform configuration to deploy WeaveCastStudio's data collection backend on Google Cloud.

## Resources Created

| Resource | Description |
|----------|-------------|
| `google_compute_instance` | GCE VM (e2-small, Ubuntu 24.04) for M1/M3 data collection |
| `google_storage_bucket` | GCS bucket for GCE→PC data sync |
| `google_service_account` | Dedicated service account with minimal permissions |
| `google_compute_firewall` | SSH access rule |
| `google_project_iam_member` | Storage Object Admin binding for service account |

## Prerequisites

1. [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.5.0
2. [Google Cloud SDK](https://cloud.google.com/sdk/docs/install)
3. GCP project with billing enabled
4. Authenticated: `gcloud auth application-default login`

## Quick Start

```bash
# 1. Copy and edit the variables
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values

# 2. Initialize Terraform
terraform init

# 3. Preview changes
terraform plan

# 4. Deploy
terraform apply

# 5. SSH into the instance
gcloud compute ssh weavecast-collector --zone=asia-northeast1-b
```

## After `terraform apply`

The startup script automatically installs system packages (Chromium, fonts, uv).
You still need to manually:

1. SSH into the instance
2. Clone the private repository
3. Run `uv sync`
4. Configure `.env` files
5. Set up cron jobs

See `../gce_deploy_guide.md` for detailed steps.

## Cleanup

```bash
terraform destroy
```

## File Structure

```
terraform/
├── main.tf             # All resources (GCE, GCS, IAM, Firewall)
├── variables.tf        # Variable definitions
├── terraform.tfvars    # Your values (git-ignored)
├── startup.sh          # GCE instance startup script
├── .gitignore          # Ignores state files and secrets
└── README.md           # This file
```
