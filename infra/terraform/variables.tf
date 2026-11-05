variable "proyecto" {
  description = "ID del proyecto de GCP."
  type        = string
}

variable "region" {
  description = "Región de Cloud Run y Artifact Registry. southamerica-east1 (São Paulo) es la más cercana a Colombia con todos los servicios."
  type        = string
  default     = "southamerica-east1"
}

variable "ubicacion_bigquery" {
  description = "Ubicación del dataset de BigQuery."
  type        = string
  default     = "southamerica-east1"
}

variable "imagen" {
  description = "Imagen del contenedor, p. ej. southamerica-east1-docker.pkg.dev/PROYECTO/safelisten/api:0.1.0"
  type        = string
}

variable "dataset" {
  description = "Nombre del dataset de BigQuery."
  type        = string
  default     = "safelisten"
}

variable "max_instancias" {
  description = "Tope de instancias de Cloud Run (controla el costo)."
  type        = number
  default     = 3
}
