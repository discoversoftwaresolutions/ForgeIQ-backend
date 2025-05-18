import os
import logging
from typing import Dict, Any, Optional, List, Union
import weaviate

# Define constant for code snippets
CODE_SNIPPET_CLASS_NAME = "CodeSnippet"

# Declare exported
# Initialize logger
logger = logging.getLogger(__name__)

# Environment variables
WEAVIATE_URL = os.getenv("WEAVIATE_URL")
WEAVIATE_API_KEY = os.getenv("WEAVIATE_API_KEY")

class VectorStoreClient:
    def __init__(self, weaviate_url: Optional[str] = None, api_key: Optional[str] = None):
        """
        Initializes the Weaviate client for vector storage.
        """
        self.url: Optional[str] = weaviate_url or WEAVIATE_URL
        self.api_key: Optional[str] = api_key or WEAVIATE_API_KEY
        self.client: Optional[weaviate.Client] = None

        if not self.url:
            logger.error("WEAVIATE_URL not configured. VectorStoreClient cannot operate.")
            return  # Client remains None

        client_params: Dict[str, Any] = {"url": self.url}
        if self.api_key:
            auth_config = weaviate.AuthApiKey(self.api_key)
            client_params["auth_client_secret"] = auth_config

        try:
            self.client = weaviate.Client(**client_params)
            logger.info("Weaviate client initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Weaviate client: {e}", exc_info=True)
            self.client = None  # Ensure client remains None on failure

    def is_ready(self) -> bool:
        if not self.client:
            return False
        try:
            return self.client.is_ready()
        except Exception as e:
            logger.error(f"Error checking Weaviate readiness: {e}")
            return False

    def ensure_schema_class_exists(self, class_name: str, class_schema: Dict[str, Any]):
        if not self.is_ready() or not self.client:
            logger.error(f"Cannot ensure schema for '{class_name}': Weaviate client not ready.")
            return
        try:
            if not self.client.schema.exists(class_name):
                self.client.schema.create_class(class_schema)
                logger.info(f"Created Weaviate class schema: '{class_name}'")
            else:
                logger.info(f"Weaviate class schema '{class_name}' already exists.")
        except Exception as e:
            logger.error(f"Error ensuring Weaviate schema for '{class_name}': {e}", exc_info=True)

    def upsert_object(self, class_name: str, data_object: Dict[str, Any], vector: List[float], uuid_key: Optional[str] = None) -> Optional[str]:
        if not self.is_ready() or not self.client:
            logger.error("Cannot upsert object: Weaviate client not ready.")
            return None
        try:
            # Weaviate's create with a specified UUID acts as an upsert.
            # If uuid_key is provided and present in data_object, use it for UUID.
            object_uuid = data_object.get(uuid_key) if uuid_key else None
                
            created_uuid = self.client.data_object.create(
                data_object=data_object,
                class_name=class_name,
                vector=vector,
                uuid=object_uuid  # Pass UUID if available for upsert behavior
            )
            logger.debug(f"Upserted object to class '{class_name}' with Weaviate UUID: {created_uuid}")
            return created_uuid
        except Exception as e:
            logger.error(f"Error upserting object to Weaviate class '{class_name}': {e}", exc_info=True)
            return None

    def batch_upsert_objects(self, class_name: str, objects_with_vectors: List[Dict[str, Union[Dict[str, Any], List[float], Optional[str]]]]):
        """
        Expects a list of dicts, each dict having 'data_object', 'vector', and optional 'uuid'.
        e.g., [{"data_object": {...}, "vector": [...], "uuid": "..."}]
        """
        if not self.is_ready() or not self.client:
            logger.error("Cannot batch upsert: Weaviate client not ready.")
            return None
        
        results = None
        try:
            with self.client.batch as batch:
                batch.batch_size = 100  # Configurable
                for item in objects_with_vectors:
                    batch.add_data_object(
                        data_object=item["data_object"],
                        class_name=class_name,
                        vector=item["vector"],  # type: ignore
                        uuid=item.get("uuid")
                    )
            # batch.create_objects() returns a list of dicts with results for each object
            results = batch.create_objects()
            
            # Check for errors in batch results
            errors_found = 0
            if results:
                for result_item in results:
                    if 'result' in result_item and 'errors' in result_item['result'] and result_item['result']['errors']:
                        errors_found += 1
                        logger.error(f"Error in batch upsert for an object: {result_item['result']['errors']}")
            if errors_found > 0:
                logger.warning(f"Batch upsert completed with {errors_found} errors out of {len(objects_with_vectors)} items.")
            else:
                logger.info(f"Batch upsert of {len(objects_with_vectors)} objects to '{class_name}' completed successfully.")
        except Exception as e:
            logger.error(f"Error during batch upsert to Weaviate class '{class_name}': {e}", exc_info=True)
        return results

    def semantic_search(self, class_name: str, query_vector: List[float],
                        properties_to_return: List[str],
                        limit: int = 10,  # Default to 10 if not provided
                        project_id_filter: Optional[str] = None,  # Example specific filter
                        additional_filters_weaviate: Optional[Dict[str, Any]] = None
                       ) -> List[Dict[str, Any]]:
        if not self.is_ready() or not self.client:
            logger.error("Cannot search: Weaviate client not ready.")
            return []
        try:
            near_vector_filter = {"vector": query_vector}
                
            query_builder = (
                self.client.query
                .get(class_name, properties_to_return + ["_additional {id, distance, certainty, vector}"])
                .with_near_vector(near_vector_filter)
                .with_limit(limit)
            )

            # Construct 'where' filter if project_id_filter or additional_filters are provided
            where_clauses: List[Dict[str, Any]] = []
            if project_id_filter:
                where_clauses.append({
                    "path": ["projectId"],  # Assuming 'projectId' is a property in your schema
                    "operator": "Equal",
                    "valueString": project_id_filter
                })
                
            if additional_filters_weaviate and additional_filters_weaviate.get("operands"):
                # If additional_filters_weaviate is already a Weaviate 'where' dict
                if where_clauses:  # Need to combine with project_id filter
                    where_clauses.append(additional_filters_weaviate)
                    final_where_filter = {"operator": "And", "operands": where_clauses}
                else:
                    final_where_filter = additional_filters_weaviate
                query_builder = query_builder.with_where(final_where_filter)
            elif where_clauses:  # Only project_id filter
                query_builder = query_builder.with_where(where_clauses[0])
                    
            raw_results = query_builder.do()
            found_objects = raw_results.get("data", {}).get("Get", {}).get(class_name, [])
            logger.debug(f"Weaviate search returned {len(found_objects)} raw results for class '{class_name}'.")
            return found_objects
        except Exception as e:
            logger.error(f"Error performing semantic search in Weaviate class '{class_name}': {e}", exc_info=True)
            return []

    def get_object_by_id(self, class_name: str, uuid: str, with_vector: bool = False) -> Optional[Dict[str, Any]]:
        if not self.is_ready() or not self.client: return None
        try:
            return self.client.data_object.get_by_id(uuid, class_name=class_name, with_vector=with_vector)
        except Exception as e:
            logger.error(f"Error fetching object by UUID {uuid} from class '{class_name}': {e}", exc_info=True)
            return None

    def delete_object(self, class_name: str, uuid: str) -> bool:
        if not self.is_ready() or not self.client: return False
        try:
            self.client.data_object.delete(uuid, class_name=class_name)
            logger.info(f"Deleted object with UUID: {uuid} from class '{class_name}'")
            return True
        except Exception as e:
            logger.error(f"Error deleting object UUID {uuid} from class '{class_name}': {e}", exc_info=True)
            return False


# Standard schema for code snippets used by CodeNavAgent
# Can be moved to a shared constants file or CodeNavAgent specific config if needed
CODE_SNIPPET_SCHEMA = {
    "class": "CodeSnippet",  # Replace with the actual class name
    "description": "A chunk of source code with its metadata and embedding.",
    "vectorizer": "none",
    "properties": [
        {"name": "content", "dataType": ["text"]},
        {"name": "filePath", "dataType": ["string"]},
        {"name": "projectId", "dataType": ["string"]},
        {"name": "language", "dataType": ["string"]},
        {"name": "startLine", "dataType": ["int"]},
        {"name": "endLine", "dataType": ["int"]},
        {"name": "contentHash", "dataType": ["string"]},  # To detect changes
        {"name": "metadataJson", "dataType": ["text"]}  # For other arbitrary metadata
    ],
}
