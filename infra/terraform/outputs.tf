output "url_api" {
  description = "URL del servicio (use con `safelisten enviar lote/ --url ...`)."
  value       = google_cloud_run_v2_service.api.uri
}

output "dataset" {
  value = "${var.proyecto}.${google_bigquery_dataset.safelisten.dataset_id}"
}

output "repositorio_imagenes" {
  value = "${var.region}-docker.pkg.dev/${var.proyecto}/${google_artifact_registry_repository.imagenes.repository_id}"
}
