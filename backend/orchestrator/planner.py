"""Turns a natural-language request into a structured ServiceSpec.

Tries the local LLM first (with a strict JSON-schema prompt). If Ollama
isn't reachable, or the model returns something that doesn't parse, falls
back to a small heuristic parser so the rest of the pipeline is still
demoable without a GPU/Ollama running. This fallback is deliberately dumb --
its only job is to keep the demo alive, not to understand language.
"""
from __future__ import annotations

import json
import re

from backend.llm import ollama_client
from backend.orchestrator.state import CloudResource, ServiceSpec

_SYSTEM_PROMPT = """You are an infrastructure planning assistant.
Read the developer's request and respond with ONLY a JSON object (no prose,
no markdown fences) matching exactly this schema:

{
  "service_name": string (lowercase, dashes, dns-safe),
  "container_image": string (a real public image, default "nginx:latest" if unspecified),
  "container_port": integer,
  "replicas": integer,
  "cpu": string (Kubernetes CPU request like "250m" or "1"),
  "memory": string (Kubernetes memory request like "256Mi" or "1Gi"),
  "namespace": string,
  "public_endpoint": boolean (true if the service should be internet-facing),
  "env": object of string->string,
  "cloud_resources": array of zero or more of ["s3_bucket", "rds_postgres", "sqs_queue"],
  "aws_region": string
}

Infer sensible defaults for anything not mentioned. Never include fields
outside this schema."""


def _heuristic_parse(request_text: str) -> ServiceSpec:
    text = request_text.lower()

    name_match = re.search(r"(?:called|named)\s+([a-z0-9][a-z0-9\-]{1,40})", text)
    if not name_match:
        name_match = re.search(r"\b([a-z][a-z0-9]*(?:-[a-z0-9]+){0,3})-service\b", text)
    service_name = name_match.group(1) if name_match else "new-service"
    service_name = re.sub(r"[^a-z0-9\-]", "-", service_name).strip("-") or "new-service"

    port_match = re.search(r"port\s+(\d{2,5})", text)
    container_port = int(port_match.group(1)) if port_match else 8080

    replicas_match = re.search(r"(\d+)\s+replicas?", text)
    replicas = int(replicas_match.group(1)) if replicas_match else 2

    image_match = re.search(r"image\s+([a-zA-Z0-9_./\-]+:[a-zA-Z0-9_.\-]+)", text)
    container_image = image_match.group(1) if image_match else "nginx:latest"

    public_endpoint = any(kw in text for kw in ["public", "internet-facing", "external", "expose"])

    cloud_resources: list[CloudResource] = []
    if "s3" in text or "bucket" in text:
        cloud_resources.append(CloudResource.S3_BUCKET)
    if "postgres" in text or "rds" in text or "database" in text or "db" in text:
        cloud_resources.append(CloudResource.RDS_POSTGRES)
    if "queue" in text or "sqs" in text:
        cloud_resources.append(CloudResource.SQS_QUEUE)

    return ServiceSpec(
        service_name=service_name,
        container_image=container_image,
        container_port=container_port,
        replicas=replicas,
        public_endpoint=public_endpoint,
        cloud_resources=cloud_resources,
    )


def plan(request_text: str) -> tuple[ServiceSpec, list[str]]:
    """Returns (spec, notes). notes explains which planning path was used."""
    notes: list[str] = []

    if ollama_client.is_available():
        try:
            raw = ollama_client.generate_json(request_text, system=_SYSTEM_PROMPT)
            spec = ServiceSpec(**raw)
            notes.append(f"Planned via Ollama model '{ollama_client.OLLAMA_MODEL}'.")
            return spec, notes
        except (ollama_client.OllamaUnavailable, json.JSONDecodeError, TypeError, ValueError) as exc:
            notes.append(f"LLM planning failed ({exc}); falling back to heuristic parser.")
    else:
        notes.append(
            f"Ollama not reachable at {ollama_client.OLLAMA_HOST}; using heuristic parser. "
            f"Run `ollama serve` and `ollama pull {ollama_client.OLLAMA_MODEL}` for real NL understanding."
        )

    return _heuristic_parse(request_text), notes
