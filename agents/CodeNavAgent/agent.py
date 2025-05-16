# agents/CodeNavAgent/agent.py
import os
import time
import json
from sentence_transformers import SentenceTransformer
import logging
from typing import List, Dict, Any, Optional

from .weaviate_client import WeaviateManager # Assuming WeaviateManager is in weaviate_client.py
from .code_parser import chunk_code_content, scan_code_directory, get_language_from_extension
# from core.event_bus.redis_bus import EventBus # If it needs to listen/publish for indexing tasks
# from interfaces.types import CodeNavSearchQuery, CodeNavSearchResults, CodeNavSearchResultItem # If using typed dicts for API

# Configure logging (basic setup, can be enhanced)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", 'all-MiniLM-L6-v2')
# Directory where code to be indexed might be mounted or checked out in the container
CODE_BASE_PATH = os.getenv("CODE_BASE_PATH", "/codesrc") # Example path inside Docker

class CodeNavAgent:
    def __init__(self):
        logger.info(f"Initializing CodeNavAgent with embedding model: {EMBEDDING_MODEL_NAME}")
        try:
            self.embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
            logger.info("SentenceTransformer model loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load SentenceTransformer model '{EMBEDDING_MODEL_NAME}': {e}", exc_info=True)
            self.embedding_model = None # Critical failure

        self.weaviate_manager = WeaviateManager()
        # self.event_bus = EventBus() # Initialize if agent uses event bus

        # TODO: Add API server setup (e.g., FastAPI) if this agent serves search requests over HTTP
        # For now, search method can be called directly or via event bus triggers

    def index_project_directory(self, project_id: str, directory_path: str):
        if not self.embedding_model or not self.weaviate_manager.client:
            logger.error("Cannot index directory: Agent not fully initialized (model or Weaviate missing).")
            return

        logger.info(f"Starting indexing for project '{project_id}' in directory: {directory_path}")

        # In a real scenario, you'd manage a list of already indexed files/hashes
        # to avoid re-indexing unchanged content or to handle deletions.
        # This is a simplified "index all found files" approach.

        files_to_index = scan_code_directory(directory_path)
        if not files_to_index:
            logger.info(f"No files found to index in {directory_path} for project {project_id}.")
            return

        all_chunks_data = []
        all_vectors = []

        for file_path in files_to_index:
            relative_file_path = os.path.relpath(file_path, directory_path) # Store path relative to project root
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()

                if not content.strip(): # Skip empty files
                    logger.debug(f"Skipping empty file: {file_path}")
                    continue

                language = get_language_from_extension(file_path)
                if not language: # Skip unknown file types for now
                    logger.debug(f"Skipping file with unknown language/extension: {file_path}")
                    continue

                logger.debug(f"Processing file for indexing: {file_path} (lang: {language})")
                code_chunks_info = chunk_code_content(content, language=language, file_path=relative_file_path)

                if not code_chunks_info:
                    continue

                chunk_contents = [chunk["content"] for chunk in code_chunks_info]
                # Generate embeddings in batch for efficiency
                vectors = self.embedding_model.encode(chunk_contents, show_progress_bar=False).tolist()

                for i, chunk_info in enumerate(code_chunks_info):
                    snippet_data = {
                        "projectId": project_id,
                        "filePath": relative_file_path,
                        "content": chunk_info["content"],
                        "language": language,
                        "startLine": chunk_info["startLine"],
                        "endLine": chunk_info["endLine"],
                        "contentHash": chunk_info["contentHash"],
                        "metadataJson": chunk_info["metadataJson"]
                    }
                    all_chunks_data.append(snippet_data)
                    all_vectors.append(vectors[i])

            except Exception as e:
                logger.error(f"Error processing file {file_path} for indexing: {e}", exc_info=True)

        if all_chunks_data and all_vectors:
            logger.info(f"Attempting to batch add {len(all_chunks_data)} snippets for project {project_id}.")
            self.weaviate_manager.batch_add_snippets(all_chunks_data, all_vectors)
        else:
            logger.info(f"No valid chunks generated for project {project_id} in {directory_path}.")

        logger.info(f"Finished indexing attempt for project '{project_id}' in directory: {directory_path}")


    def perform_search(self, project_id: str, query_text: str, limit: int = 10, search_filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]: # Return type should match CodeNavSearchResultItem structure
        if not self.embedding_model or not self.weaviate_manager.client:
            logger.error("Cannot search: Agent not fully initialized (model or Weaviate missing).")
            return []

        logger.info(f"Performing search in project '{project_id}' for query: '{query_text}' with limit {limit}")
        query_vector = self.embedding_model.encode(query_text).tolist()

        # Example: search_filters = {"language": "python"}
        search_results_raw = self.weaviate_manager.semantic_search(query_vector, project_id=project_id, limit=limit, filters=search_filters)

        # Transform raw Weaviate results into a more structured format if needed
        # This structure should align with interfaces_python.types.CodeNavSearchResultItem
        results = []
        for raw_item in search_results_raw:
            metadata_json = raw_item.get("metadataJson", "{}")
            try:
                metadata = json.loads(metadata_json)
            except json.JSONDecodeError:
                metadata = {}

            results.append({
                "file_path": raw_item.get("filePath"),
                "snippet": raw_item.get("content"), # You might want to truncate or summarize this
                "score": raw_item.get("_additional", {}).get("certainty", 0.0), # Or use 'distance'
                "language": raw_item.get("language"),
                "start_line": raw_item.get("startLine"),
                "end_line": raw_item.get("endLine"),
                "metadata": metadata, # Parsed metadata
                "raw_weaviate_id": raw_item.get("_additional", {}).get("id") # Weaviate's internal ID
            })
        return results

    def run_api_server(self, host="0.0.0.0", port=8001):
        """ Exposes search functionality via a simple FastAPI server """
        from fastapi import FastAPI, HTTPException
        # from interfaces_python.types import CodeNavSearchQuery # If using TypedDict for request body

        # Ensure this import works with your PYTHONPATH settings in Docker
        # It might need to be relative if this agent.py is the entrypoint for uvicorn

        # Define a Pydantic model for the search request if not using TypedDict directly
        from pydantic import BaseModel, Field
        class SearchRequest(BaseModel):
            project_id: str
            query_text: str
            limit: Optional[int] = 10
            filters: Optional[Dict[str, Any]] = None


        api_app = FastAPI(title="CodeNavAgent API")

        @api_app.post("/search", summary="Perform semantic code search")
        # async def search_endpoint(request_data: CodeNavSearchQuery): # Using TypedDict
        async def search_endpoint(request_data: SearchRequest): # Using Pydantic model
            logger.info(f"API /search called with project: {request_data.project_id}, query: '{request_data.query_text}'")
            if not self.embedding_model or not self.weaviate_manager.client:
                raise HTTPException(status_code=503, detail="CodeNavAgent not fully initialized.")
            try:
                results = self.perform_search(
                    project_id=request_data.project_id,
                    query_text=request_data.query_text,
                    limit=request_data.limit or 10,
                    search_filters=request_data.filters
                )
                return {"results": results}
            except Exception as e:
                logger.error(f"Error in /search endpoint: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail="Internal server error during search.")

        @api_app.post("/index_directory", summary="Trigger indexing of a project directory (conceptual)")
        async def index_endpoint(project_id: str, directory_path_in_container: str):
            # In a real system, this might be an async task, or triggered via event bus
            # For now, direct call (might be long-running)
            logger.info(f"API /index_directory called for project: {project_id}, path: {directory_path_in_container}")
            # This path needs to be accessible within the CodeNavAgent's container
            # e.g., a path under CODE_BASE_PATH if code is mounted/copied there
            full_path_to_index = os.path.join(CODE_BASE_PATH, project_id, directory_path_in_container) # Example construction
            if not os.path.isdir(full_path_to_index):
                 raise HTTPException(status_code=404, detail=f"Directory not found in container: {full_path_to_index}")

            # Offload actual indexing to a background task in a real app
            self.index_project_directory(project_id, full_path_to_index)
            return {"message": f"Indexing started for project {project_id}, directory {directory_path_in_container}"}

        import uvicorn
        logger.info(f"Starting CodeNavAgent API server on {host}:{port}")
        uvicorn.run(api_app, host=host, port=port, log_level=LOG_LEVEL.lower())

    def run_worker(self):
        """
        Runs the agent in a worker mode, perhaps listening to an event bus for indexing tasks.
        """
        logger.info("CodeNavAgent started in worker mode.")
        logger.info(f"Code base path configured for scanning (if any): {CODE_BASE_PATH}")

        # Example: Perform an initial scan of a pre-configured directory if CODE_BASE_PATH is set
        # This would be more complex, iterating through projects defined under CODE_BASE_PATH
        if CODE_BASE_PATH and os.path.isdir(CODE_BASE_PATH):
            # Assuming CODE_BASE_PATH contains subdirectories, each being a project_id
            for project_id_dir in os.listdir(CODE_BASE_PATH):
                project_full_path = os.path.join(CODE_BASE_PATH, project_id_dir)
                if os.path.isdir(project_full_path):
                    logger.info(f"Found project directory to index: {project_full_path} (ID: {project_id_dir})")
                    # In a real scenario, you might trigger this via an event or initial setup
                    self.index_project_directory(project_id=project_id_dir, directory_path=project_full_path)
        else:
            logger.warning(f"CODE_BASE_PATH ('{CODE_BASE_PATH}') is not set or not a directory. No initial indexing scan performed.")


        # If this agent also needs to listen to events (e.g., "NewCommitEvent" to trigger re-indexing)
        # it would set up its event bus subscriber here.
        # For now, we can make it expose the API server.
        # If it needs to do both, it would run the API server in a separate thread/process
        # or integrate event listening into the API app's lifecycle (e.g. FastAPI background tasks).

        # For now, let's assume it primarily serves API requests after an initial scan
        # If it's meant to be an API server, call run_api_server.
        # If it's meant to be a continuously running worker that polls or listens to events,
        # that logic would go here.
        # For this example, we'll start the API server.
        # If you need both API and background tasks (like periodic re-indexing),
        # you'd typically use something like Celery with FastAPI, or FastAPI's BackgroundTasks.

        api_port = int(os.getenv("PORT", "8001")) # PORT for Railway
        self.run_api_server(port=api_port)


if __name__ == "__main__":
    # This determines how the agent runs when `python agents/CodeNavAgent/app/agent.py` is called
    agent = CodeNavAgent()
    # Default to worker mode which might include starting an API server
    agent.run_worker()
