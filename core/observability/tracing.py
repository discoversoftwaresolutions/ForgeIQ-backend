# core/observability/tracing.py

import logging
import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
# You would typically choose one or more exporters based on your tracing backend
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
# from opentelemetry.exporter.jaeger.thrift import JaegerExporter
# from opentelemetry.sdk.trace.export import ConsoleSpanExporter # Good for local testing

# Configure logging for this module
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def setup_tracing(service_name: str = "my-python-application", endpoint: str = "localhost:4317"):
  """
  Sets up OpenTelemetry tracing for the application.

  Args:
    service_name: The name of the service to be used in traces.
    endpoint: The endpoint for the OTLP collector (e.g., "localhost:4317").
              Adjust if using a different exporter.
  """
  logger.info(f"Setting up tracing for service: {service_name}")

  # Set resource attributes (identifying your service)
  resource = Resource.create({
      "service.name": service_name,
      "environment": os.getenv("ENV", "development"),
      # Add any other relevant attributes
  })

  # Set up the TracerProvider
  tracer_provider = TracerProvider(resource=resource)

  # Configure an exporter
  # This example uses the OTLP exporter via gRPC.
  # You might need to install the necessary package:
  # pip install opentelemetry-exporter-otlp-proto-grpc
  span_exporter = OTLPSpanExporter(endpoint=endpoint)

  # If using Jaeger:
  # pip install opentelemetry-exporter-jaeger
  # span_exporter = JaegerExporter(agent_host_name="localhost", agent_port=6831)

  # If you want to print traces to the console for debugging:
  # span_exporter = ConsoleSpanExporter()


  # Configure a SpanProcessor that sends spans to the exporter
  # BatchSpanProcessor is recommended for production to reduce overhead
  tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))

  # Set the global TracerProvider
  trace.set_tracer_provider(tracer_provider)

  logger.info("OpenTelemetry tracing setup complete.")

# Example of how you might call setup_tracing in your main application file:
# from core.observability.tracing import setup_tracing
#
# if __name__ == "__main__":
#     setup_tracing(service_name="my-web-service", endpoint="http://otel-collector:4317")
#     # Your main application logic follows
