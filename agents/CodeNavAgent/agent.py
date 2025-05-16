# agents/CodeNavAgent/agent.py
import os
import weaviate
from sentence_transformers import SentenceTransformer
import logging
# from core_python.event_bus.redis_bus import EventBus # If it needs to listen/publish
# from interfaces_python.types import CodeNavSearchQuery, CodeNavSearchResults, CodeNavSearchResultItem

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger(__name__)

WEAVIATE_URL = os.getenv("WEAVIATE_URL")
WEAVIATE_API_KEY = os.getenv("WEAVIATE_API_KEY") # If using Weaviate Cloud with API key auth
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL", 'all-MiniLM-L6-v2')

class CodeNavAgent:
    def __init__(self):
        logger.info(f"Initializing CodeNavAgent with model: {EMBEDDING_MODEL_NAME}")
        # Initialize Sentence Transformer model
        try:
            self.model = SentenceTransformer(EMBEDDING_MODEL_NAME)
            logger.info("SentenceTransformer model loaded.")
        except Exception as e:
            logger.error(f"Failed to load SentenceTransformer model: {e}")
            self.model = None

        # Initialize Weaviate client
        if not WEAVIATE_URL:
            logger.error("WEAVIATE_URL not configured!")
            self.weaviate_client = None
        else:
            client_params = {"url": WEAVIATE_URL}
            if WEAVIATE_API_KEY:
                 client_params["auth_client_secret"] = weaviate.AuthApiKey(api_key=WEAVIATE_API_KEY)

            try:
                self.weaviate_client = weaviate.Client(**client_params)
                if not self.weaviate_client.is_ready():
                    logger.error("Weaviate not ready!")
                    self.weaviate_client = None
                else:
                    logger.info("Connected to Weaviate.")
                    self._ensure_schema()
            except Exception as e:
                logger.error(f"Failed to connect or setup Weaviate: {e}")
                self.weaviate_client = None

        # self.event_bus = EventBus() # If subscribing to indexing requests or publishing results

    def _ensure_schema(self):
        if not self.weaviate_client: return
        class_name = "CodeSnippet"
        # Example schema - customize as needed
        schema = {
            "class": class_name,
            "description": "A snippet of source code",
            "vectorizer": "none", # We provide our own vectors
            "properties": [
                {"name": "projectId", "dataType": ["text"]},
                {"name": "filePath", "dataType": ["text"]},
                {"name": "content", "dataType": ["text"]},
                {"name": "language", "dataType": ["text"], "tokenization": "word"},
                {"name": "startLine", "dataType": ["int"]},
                {"name": "endLine", "dataType": ["int"]},
                # Add other metadata properties
            ]
        }
        if not self.weaviate_client.schema.exists(class_name):
            self.weaviate_client.schema.create_class(schema)
            logger.info(f"Created Weaviate class: {class_name}")


    def index_code_snippet(self, project_id: str, file_path: str, content: str, lang: str, start_line: int, end_line: int):
        if not self.model or not self.weaviate_client:
            logger.error("Cannot index: Model or Weaviate client not initialized.")
            return None

        try:
            vector = self.model.encode(content).tolist()
            data_object = {
                "projectId": project_id,
                "filePath": file_path,
                "content": content,
                "language": lang,
                "startLine": start_line,
                "endLine": end_line
            }
            uuid = self.weaviate_client.data_object.create(
                data_object=data_object,
                class_name="CodeSnippet",
                vector=vector
            )
            logger.info(f"Indexed snippet from {file_path} with UUID: {uuid}")
            return uuid
        except Exception as e:
            logger.error(f"Error indexing code snippet {file_path}: {e}")
            return None

    def search(self, project_id: str, query_text: str, limit: int = 5) -> list: # -> List[CodeNavSearchResultItem]
        if not self.model or not self.weaviate_client:
            logger.error("Cannot search: Model or Weaviate client not initialized.")
            return []

        try:
            query_vector = self.model.encode(query_text).tolist()

            near_vector = {"vector": query_vector}

            # Example filter - ensure projectId matches
            where_filter = {
                "path": ["projectId"],
                "operator": "Equal",
                "valueText": project_id,
            }

            results = (
                self.weaviate_client.query
                .get("CodeSnippet", ["projectId", "filePath", "content", "language", "startLine", "endLine", "_additional {certainty distance}"])
                .with_near_vector(near_vector)
                .with_where(where_filter)
                .with_limit(limit)
                .do()
            )

            # Format results (this is a placeholder for actual formatting into CodeNavSearchResultItem)
            formatted_results = []
            if "data" in results and "Get" in results["data"] and "CodeSnippet" in results["data"]["Get"]:
                for item in results["data"]["Get"]["CodeSnippet"]:
                    # This mapping depends on your actual CodeNavSearchResultItem type
                    formatted_results.append({
                        "file_path": item.get("filePath"),
                        "snippet": item.get("content"), # Or a summary
                        "score": item.get("_additional", {}).get("certainty"), # or 1-distance
                        "metadata": {"language": item.get("language"), "startLine": item.get("startLine")}
                    })
            logger.info(f"Search for '{query_text}' found {len(formatted_results)} results.")
            return formatted_results # Should be List[CodeNavSearchResultItem]
        except Exception as e:
            logger.error(f"Error during search for '{query_text}': {e}")
            return []

    def run_server(self): # Example: if it needs to expose an API for PatchAgent
        # This would be a simple FastAPI or Flask app, or an RPC server
        # For now, just a placeholder
        logger.info("CodeNavAgent server would start here (e.g., FastAPI for search queries).")
        # Example:
        # from fastapi import FastAPI
        # uvicorn_app = FastAPI()
        # @uvicorn_app.post("/search")
        # async def handle_search(query: CodeNavSearchQuery):
        #     return self.search(query.project_id, query.query_text, query.limit or 5)
        # import uvicorn
        # uvicorn.run(uvicorn_app, host="0.0.0.0", port=int(os.getenv("PORT", "8001")))
        # For now, just keep alive as a worker if it only processes events
        import time
        while True:
            logger.debug("CodeNavAgent alive...")
            time.sleep(300)


if __name__ == "__main__":
    # This part is for standalone execution or simple worker mode
    # In a real deployment, this might be started by Uvicorn if it's an API server,
    # or just run if it's an event-driven worker.
    agent = CodeNavAgent()
    # agent.index_code_snippet("proj1", "src/main.py", "def hello():\n  print('world')", "python", 1, 2) # Example indexing
    # results = agent.search("proj1", "greeting function")
    # print(results)
    agent.run_server() # Or a method to start listening to event bus for indexing tasks
