from typing import Dict, Any, Optional

class DeployContext:
    """Manages deployment context information."""

    def __init__(self, project_id: str, environment: str, commit_sha: str, metadata: Optional[Dict[str, Any]] = None):
        """
        Initializes a deployment context.
        
        Args:
            project_id (str): Identifier for the project being deployed.
            environment (str): Target deployment environment (e.g., 'staging', 'production').
            commit_sha (str): Commit hash associated with the deployment.
            metadata (Optional[Dict[str, Any]]): Additional deployment metadata.
        """
        self.project_id = project_id
        self.environment = environment
        self.commit_sha = commit_sha
        self.metadata = metadata or {}

    def update_metadata(self, key: str, value: Any) -> None:
        """Updates deployment metadata."""
        self.metadata[key] = value

    def get_metadata(self, key: str) -> Optional[Any]:
        """Retrieves metadata value for a given key."""
        return self.metadata.get(key)

    def as_dict(self) -> Dict[str, Any]:
        """Returns the deployment context as a dictionary."""
        return {
            "project_id": self.project_id,
            "environment": self.environment,
            "commit_sha": self.commit_sha,
            "metadata": self.metadata,
        }
