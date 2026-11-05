FROM python:3.12-slim

# git: las dependencias propias (Alómetro, DataShield) se instalan desde GitHub fijadas a un commit.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[api,gcp]"

RUN useradd --create-home --uid 10001 app
USER app

ENV PORT=8080
EXPOSE 8080
CMD ["safelisten", "servir"]
