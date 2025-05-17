# ============================
# 📁 shared/app_config.py
# ============================
import os
import logging
from typing import Optional, Any # For type hints

logger = logging.getLogger(__name__)

class AppConfig:
    def __init__(self):
        # Core Infrastructure URLs
        self.redis_url: Optional[str] = os.getenv("REDIS_URL", "redis://localhost:6379")
        self.weaviate_url: Optional[str] = os.getenv("WEAVIATE_URL")
        self.weaviate_api_key: Optional[str] = os.getenv("WEAVIATE_API_KEY")

        self.otel_exporter_otlp_traces_endpoint: Optional[str] = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")

        # Service-specific configurations
        self.codenav_api_url: Optional[str] = os.getenv("CODE_NAV_AGENT_API_URL")
        self.forgeiq_backend_api_url: Optional[str] = os.getenv("FORGEIQ_API_BASE_URL") # For Python SDK

        # General Application Settings
        self.environment: str = os.getenv("APP_ENV", "development").lower()
        self.log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()

        self.enable_feature_x: bool = self._get_bool_env("ENABLE_FEATURE_X", False)

        self._validate_critical_configs()

    def _get_bool_env(self, var_name: str, default: bool) -> bool:
        val = os.getenv(var_name)
        if val is None:
            return default
        # Manual boolean conversion instead of distutils.strtobool
        return val.lower() in ['true', '1', 't', 'y', 'yes']

    def _validate_critical_configs(self):
        if not self.redis_url:
            logger.warning("REDIS_URL is not set in environment. Core services relying on Redis may not function.")
        # Add more critical config validations here as your system grows

    def get(self, key: str, default: Optional[Any] = None) -> Optional[Any]:
        """Generic getter for other config values if you add them dynamically to the instance."""
        return getattr(self, key, default)

# Singleton instance for easy access across the application
app_config = AppConfig()
