# agents/CodeNavAgent/weaviate_client.py
import weaviate
import os
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

WEAVIATE_URL = os.getenv("WEAVIATE_URL")
WEAVIATE_API_KEY = os.getenv("WEAVIATE_API_KEY")
CODE_SNIPPET_CLASS_NAME = "CodeSnippet"

class WeaviateManager:
    def __init__(self):
        self.client = None
        if not WEAVIATE_URL:
            logger.error("WEAVIATE_URL environment variable is not set. Weaviate client cannot be initialized.")
            return

        client_args = {"url": WEAVIATE_URL}
        if WEAVIATE_API_KEY:
            client_args["auth_client_secret"] = weaviate.AuthApiKey(api_key=WEAVIATE_API_KEY)

        try:
            self.client = weaviate.Client(**client_args)
            if self.client.is_ready():
                logger.info(f"Successfully connected to Weaviate at {WEAVIATE_URL}")
                self._ensure_schema_exists()
            else:
                logger.error(f"Weaviate instance at {WEAVIATE_URL} is not ready.")
                self.client = None # Ensure client is None if not ready
        except Exception as e:
            logger.error(f"Failed to connect to Weaviate or check readiness: {e}")
            self.client = None

    def _ensure_schema_exists(self):
        if not self.client:
            return
        try:
            if not self.client.schema.exists(CODE_SNIPPET_CLASS_NAME):
                code_snippet_class = {
                    "class": CODE_SNIPPET_CLASS_NAME,
                    "description": "A chunk of source code with its metadata and embedding.",
                    "vectorizer": "none",  # We will provide our own vectors
                    "properties": [
                        {"name": "content", "dataType": ["text"], "description": "The actual code content"},
                        {"name": "filePath", "dataType": ["string"], "description": "Original file path"},
                        {"name": "projectId", "dataType": ["string"], "description": "Project identifier"},
                        {"name": "language", "dataType": ["string"], "description": "Programming language"},
                        {"name": "startLine", "dataType": ["int"], "description": "Start line of the snippet in the original file"},
                        {"name": "endLine", "dataType": ["int"], "description": "End line of the snippet"},
                        {"name": "contentHash", "dataType": ["string"], "description": "SHA256 hash of the content to detect changes"},
                        {"name": "metadataJson", "dataType": ["text"], "description": "Other metadata as a JSON string (e.g., function/class name)"}
                    ],
                }
                self.client.schema.create_class(code_snippet_class)
                logger.info(f"Created Weaviate class schema: '{CODE_SNIPPET_CLASS_NAME}'")
        except Exception as e:
            logger.error(f"Error ensuring Weaviate schema for '{CODE_SNIPPET_CLASS_NAME}': {e}")

    def add_or_update_snippet(self, snippet_data: Dict[str, Any], vector: List[float]) -> Optional[str]:
        if not self.client:
            logger.error("Cannot add snippet: Weaviate client not initialized.")
            return None
        try:
            # Check if a snippet with the same contentHash and filePath already exists
            # For simplicity, this example doesn't implement perfect upsert logic based on contentHash.
            # A robust upsert might involve querying by hash, then updating or creating.
            # Weaviate's create with a specified UUID can act as an upsert if the UUID is deterministic.

            uuid = self.client.data_object.create(
                data_object=snippet_data,
                class_name=CODE_SNIPPET_CLASS_NAME,
                vector=vector
                # To make it upsert-like, you could generate a deterministic UUID based on contentHash and filePath
                # uuid = generate_deterministic_uuid(snippet_data['contentHash'], snippet_data['filePath'])
                # self.client.data_object.create(..., uuid=uuid)
            )
            logger.debug(f"Added/Updated snippet for {snippet_data.get('filePath')} with UUID: {uuid}")
            return uuid
        except Exception as e:
            logger.error(f"Error adding/updating snippet in Weaviate for {snippet_data.get('filePath')}: {e}")
            return None

    def batch_add_snippets(self, snippets_data: List[Dict[str, Any]], vectors: List[List[float]]):
        if not self.client:
            logger.error("Cannot batch add snippets: Weaviate client not initialized.")
            return
        if len(snippets_data) != len(vectors):
            logger.error("Mismatch between snippets data and vectors count for batch add.")
            return

        with self.client.batch as batch:
            batch.batch_size = 100 # Configure as needed
            for i in range(len(snippets_data)):
                batch.add_data_object(
                    data_object=snippets_data[i],
                    class_name=CODE_SNIPPET_CLASS_NAME,
                    vector=vectors[i]
                )
        logger.info(f"Batch added {len(snippets_data)} snippets to Weaviate.")
        # Note: Batch results (errors, successes) can be inspected from the return of batch.create_objects()

    def semantic_search(self, query_vector: List[float], project_id: Optional[str] = None, limit: int = 5, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        if not self.client:
            logger.error("Cannot search: Weaviate client not initialized.")
            return []
        try:
            query_builder = (
                self.client.query
                .get(CODE_SNIPPET_CLASS_NAME, [
                    "content", "filePath", "projectId", "language", "startLine", "endLine", "metadataJson",
                    "_additional {id, distance, certainty, vector}" # Include vector if needed for re-ranking
                ])
                .with_near_vector({"vector": query_vector})
                .with_limit(limit)
            )

            # Build 'where' filter
            where_clauses = []
            if project_id:
                where_clauses.append({
                    "path": ["projectId"],
                    "operator": "Equal",
                    "valueString": project_id # Use valueString for string comparison
                })
            if filters:
                for key, value in filters.items():
                    # This is a simplified filter builder; Weaviate 'where' can be more complex
                    value_type = "valueString" # default
                    if isinstance(value, int): value_type = "valueInt"
                    elif isinstance(value, bool): value_type = "valueBoolean"
                    # ... add more type checks if needed
                    where_clauses.append({
                        "path": [key], # Assumes metadata keys are direct properties
                        "operator": "Equal",
                        value_type: value
                    })

            if len(where_clauses) == 1:
                query_builder = query_builder.with_where(where_clauses[0])
            elif len(where_clauses) > 1:
                query_builder = query_builder.with_where({
                    "operator": "And",
                    "operands": where_clauses
                })

            result = query_builder.do()

            found_objects = result.get("data", {}).get("Get", {}).get(CODE_SNIPPET_CLASS_NAME, [])
            logger.debug(f"Weaviate search returned {len(found_objects)} raw results.")
            return found_objects
        except Exception as e:
            logger.error(f"Error performing semantic search in Weaviate: {e}")
            return []

    def get_snippet_by_id(self, uuid: str) -> Optional[Dict[str, Any]]:
        if not self.client: return None
        try:
            return self.client.data_object.get_by_id(uuid, class_name=CODE_SNIPPET_CLASS_NAME, with_vector=True)
        except Exception as e:
            logger.error(f"Error fetching snippet by UUID {uuid}: {e}")
            return None

    def delete_snippet(self, uuid: str) -> bool:
        if not self.client: return False
        try:
            self.client.data_object.delete(uuid, class_name=CODE_SNIPPET_CLASS_NAME)
            logger.info(f"Deleted snippet with UUID: {uuid}")
            return True
        except Exception as e:
            logger.error(f"Error deleting snippet UUID {uuid}: {e}")
            return False
