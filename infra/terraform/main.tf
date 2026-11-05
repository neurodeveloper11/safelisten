# Infraestructura de SafeListen en GCP: Cloud Run + BigQuery + Secret Manager.
# La nube solo recibe lotes ya anonimizados; la sal y los datos originales se quedan en la empresa.

locals {
  servicios = [
    "run.googleapis.com",
    "bigquery.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
  ]
  etiquetas = { app = "safelisten" }
}

resource "google_project_service" "apis" {
  for_each           = toset(local.servicios)
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "imagenes" {
  location      = var.region
  repository_id = "safelisten"
  format        = "DOCKER"
  labels        = local.etiquetas
  depends_on    = [google_project_service.apis]
}

resource "google_bigquery_dataset" "safelisten" {
  dataset_id  = var.dataset
  location    = var.ubicacion_bigquery
  description = "Evaluaciones de calidad sobre conversaciones anonimizadas (sin datos personales)."
  labels      = local.etiquetas
  depends_on  = [google_project_service.apis]
}

resource "google_service_account" "api" {
  account_id   = "safelisten-api"
  display_name = "SafeListen API (Cloud Run)"
}

# Mínimo privilegio: escribe solo en su dataset y puede lanzar trabajos de carga.
resource "google_bigquery_dataset_iam_member" "editor" {
  dataset_id = google_bigquery_dataset.safelisten.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.api.email}"
}

resource "google_project_iam_member" "jobs" {
  project = var.proyecto
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.api.email}"
}

# El valor del token NO va en Terraform (quedaría en el estado). Se carga aparte:
#   openssl rand -hex 32 | gcloud secrets versions add safelisten-token --data-file=-
resource "google_secret_manager_secret" "token" {
  secret_id = "safelisten-token"
  labels    = local.etiquetas
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "lector" {
  secret_id = google_secret_manager_secret.token.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

resource "google_cloud_run_v2_service" "api" {
  name                = "safelisten-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false
  labels              = local.etiquetas

  template {
    service_account = google_service_account.api.email
    timeout         = "300s"

    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instancias
    }

    containers {
      image = var.imagen

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }

        cpu_idle = true
      }

      env {
        name  = "SAFELISTEN_BQ_PROYECTO"
        value = var.proyecto
      }
      env {
        name  = "SAFELISTEN_BQ_DATASET"
        value = google_bigquery_dataset.safelisten.dataset_id
      }
      env {
        name = "SAFELISTEN_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.token.secret_id
            version = "latest"
          }
        }
      }

      startup_probe {
        http_get {
          path = "/salud"
        }
      }
    }
  }

  depends_on = [google_secret_manager_secret_iam_member.lector, google_project_service.apis]
}

# El servicio exige su propio token Bearer; Cloud Run lo deja accesible por HTTPS.
# Si la empresa prefiere autenticación de Google (IAM), elimine este recurso y
# conceda roles/run.invoker solo a la cuenta que envía los lotes.
resource "google_cloud_run_v2_service_iam_member" "publico" {
  name     = google_cloud_run_v2_service.api.name
  location = google_cloud_run_v2_service.api.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}
