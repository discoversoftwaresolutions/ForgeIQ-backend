"""
trace_utils.py

Centralized helper utilities for tracing orchestration spans.
"""

import logging

try:
    from opentelemetry import trace as _trace_api
    from opentelemetry.trace import Span
    _tracer = _trace_api.get_tracer("ForgeIQ-Orchestrator", "0.1.0")
except ImportError:
    _trace_api = None
    _tracer = None

logger = logging.getLogger(__name__)


def start_trace_span_if_available(span_name: str, **attrs) -> object:
    """
    Starts a trace span if tracing is available, else returns a context manager that does nothing.
    """
    if not _trace_api or not _tracer:
        return _null_span()

    span = _tracer.start_span(span_name)
    for k, v in attrs.items():
        if isinstance(span, Span):
            span.set_attribute(k, v)
    return span


class _null_span:
    """No-op context manager when tracing is disabled."""
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def set_attribute(self, *args, **kwargs): pass
    def record_exception(self, *args, **kwargs): pass
    def set_status(self, *args, **kwargs): pass
