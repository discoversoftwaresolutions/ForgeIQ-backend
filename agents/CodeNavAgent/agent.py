# agents/CodeNavAgent/agent.py
# ... (imports from previous CodeNavAgent definition, including new ones below)
from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel, Field # For request/response validation if not using TypedDict directly for API
from typing import List, Dict, Any, Optional # Ensure this is imported
import uvicorn

# --- Observability Setup ---
SERVICE_NAME_CODENAV = "CodeNavAgent" # Consistent service name
LOG_LEVEL_CODENAV = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL_CODENAV,
    format=f'%(asctime)s - {SERVICE_NAME_CODENAV} - %(name)s - %(levelname)s - %(message)s'
)
tracer_codenav = None
try:
    from core.observability.tracing import setup_tracing
    tracer_codenav = setup_tracing(SERVICE_NAME_CODENAV)
except ImportError:
    logging.getLogger(SERVICE_NAME_CODENAV).warning(
        "CodeNavAgent: Tracing setup failed."
    )
logger_codenav = logging.getLogger(__name__) # Use this for logging within the class
# --- End Observability Setup ---

# ... (WeaviateManager and CodeParser class/functions as defined before) ...
# ... (EMBEDDING_MODEL_NAME, CODE_BASE_PATH defined as before) ...

# Pydantic models for API validation (alternative to using TypedDicts directly for FastAPI body)
class ApiSearchRequest(BaseModel):
    project_id: str
    query_text: str
    limit: Optional[int] = Field(default=10, ge=1, le=100)
    filters: Optional[Dict[str, Any]] = None

class ApiCodeSnippet(BaseModel): # Corresponds to CodeNavSearchResultItem
    file_path: str
    snippet: str
    score: float
    language: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None
    raw_weaviate_id: Optional[str] = None

class ApiSearchResponse(BaseModel):
    results: List[ApiCodeSnippet]

class ApiIndexRequest(BaseModel):
    project_id: str
    directory_path_in_container: str # Relative to CODE_BASE_PATH

class CodeNavAgent:
    def __init__(self):
        logger_codenav.info(f"Initializing CodeNavAgent with embedding model: {EMBEDDING_MODEL_NAME}")
        # ... (embedding_model and weaviate_manager initialization as before) ...
        self.embedding_model = None # Placeholder init
        try:
            self.embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
            logger_codenav.info("SentenceTransformer model loaded successfully.")
        except Exception as e:
            logger_codenav.error(f"Failed to load SentenceTransformer model '{EMBEDDING_MODEL_NAME}': {e}", exc_info=True)

        self.weaviate_manager = WeaviateManager()


    @property # Make tracer accessible if needed, or pass explicitly
    def tracer(self):
        return tracer_codenav

    # ... index_project_directory and perform_search methods as defined before ...
    # Ensure they use logger_codenav and integrate tracing spans:

    def index_project_directory(self, project_id: str, directory_path: str):
        if not self.tracer: # Fallback if tracer couldn't initialize
            self._index_project_directory_logic(project_id, directory_path)
            return

        with self.tracer.start_as_current_span("index_project_directory") as span:
            span.set_attributes({
                "code_nav.project_id": project_id,
                "code_nav.directory_path": directory_path
            })
            try:
                self._index_project_directory_logic(project_id, directory_path)
                span.set_attribute("code_nav.indexing_status", "success")
            except Exception as e:
                logger_codenav.error(f"Exception during index_project_directory: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, "Indexing failed"))
                span.set_attribute("code_nav.indexing_status", "failed")


    def _index_project_directory_logic(self, project_id: str, directory_path: str):
        # ... (actual indexing logic from previous CodeNavAgent detailed version) ...
        # ... (scan_code_directory, chunk_code_content, encode, batch_add_snippets) ...
        if not self.embedding_model or not self.weaviate_manager.client:
            logger_codenav.error("Cannot index directory: Agent not fully initialized (model or Weaviate missing).")
            return

        logger_codenav.info(f"Starting indexing for project '{project_id}' in directory: {directory_path}")
        files_to_index = scan_code_directory(directory_path) # from .code_parser
        # ... rest of the logic ...
        all_chunks_data = []
        all_vectors = []
        for file_path in files_to_index:
            # ... (file reading, chunking, embedding as before) ...
            # Make sure to use logger_codenav
            relative_file_path = os.path.relpath(file_path, directory_path)
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f: content = f.read()
                if not content.strip(): continue
                language = get_language_from_extension(file_path) # from .code_parser
                if not language: continue

                code_chunks_info = chunk_code_content(content, language=language, file_path=relative_file_path) # from .code_parser
                if not code_chunks_info: continue

                chunk_contents = [chunk["content"] for chunk in code_chunks_info]
                vectors = self.embedding_model.encode(chunk_contents, show_progress_bar=False).tolist()

                for i, chunk_info in enumerate(code_chunks_info):
                    snippet_data = {
                        "projectId": project_id, "filePath": relative_file_path, "content": chunk_info["content"],
                        "language": language, "startLine": chunk_info["startLine"], "endLine": chunk_info["endLine"],
                        "contentHash": chunk_info["contentHash"], "metadataJson": chunk_info["metadataJson"]
                    }
                    all_chunks_data.append(snippet_data)
                    all_vectors.append(vectors[i])
            except Exception as e:
                logger_codenav.error(f"Error processing file {file_path} for indexing: {e}", exc_info=True)

        if all_chunks_data and all_vectors:
            self.weaviate_manager.batch_add_snippets(all_chunks_data, all_vectors)
        logger_codenav.info(f"Finished indexing attempt for project '{project_id}' in {directory_path}")


    def perform_search(self, project_id: str, query_text: str, limit: int = 10, search_filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        if not self.tracer: # Fallback
            return self._perform_search_logic(project_id, query_text, limit, search_filters)

        with self.tracer.start_as_current_span("perform_search") as span:
            span.set_attributes({
                "code_nav.project_id": project_id,
                "code_nav.query_text": query_text, # Be careful with PII in query_text if sensitive
                "code_nav.limit": limit,
                "code_nav.has_filters": bool(search_filters)
            })
            try:
                results = self._perform_search_logic(project_id, query_text, limit, search_filters)
                span.set_attribute("code_nav.num_results", len(results))
                return results
            except Exception as e:
                logger_codenav.error(f"Exception during perform_search: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, "Search failed"))
                return []

    def _perform_search_logic(self, project_id: str, query_text: str, limit: int = 10, search_filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        # ... (actual search logic from previous CodeNavAgent detailed version) ...
        # ... (encode query, call weaviate_manager.semantic_search, format results) ...
        if not self.embedding_model or not self.weaviate_manager.client:
            logger_codenav.error("Cannot search: Agent not fully initialized.")
            return []
        logger_codenav.info(f"Performing search in project '{project_id}' for query: '{query_text}' with limit {limit}")
        query_vector = self.embedding_model.encode(query_text).tolist()
        search_results_raw = self.weaviate_manager.semantic_search(query_vector, project_id=project_id, limit=limit, filters=search_filters)
        results = [] # Format into ApiCodeSnippet compatible dicts
        for raw_item in search_results_raw:
            # ... (formatting logic as before) ...
             metadata_json = raw_item.get("metadataJson", "{}")
             try: metadata = json.loads(metadata_json)
             except json.JSONDecodeError: metadata = {}
             results.append({
                "file_path": raw_item.get("filePath"), "snippet": raw_item.get("content"),
                "score": raw_item.get("_additional", {}).get("certainty", 0.0),
                "language": raw_item.get("language"), "start_line": raw_item.get("startLine"),
                "end_line": raw_item.get("endLine"), "metadata": metadata,
                "raw_weaviate_id": raw_item.get("_additional", {}).get("id")
            })
        return results


    def create_api_app(self):
        api_app = FastAPI(title=f"{SERVICE_NAME_CODENAV} API")
        logger_codenav.info(f"FastAPI app created for {SERVICE_NAME_CODENAV}")

        # Instrument FastAPI app if OTel is setup
        # from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        # if tracer_codenav: # Check if tracing was initialized
        #     FastAPIInstrumentor.instrument_app(api_app, tracer_provider=tracer_codenav.provider) # Pass provider
        #     logger_codenav.info("FastAPI app instrumented with OpenTelemetry.")


        @api_app.post("/search", response_model=ApiSearchResponse, summary="Perform semantic code search")
        async def search_endpoint(request_data: ApiSearchRequest):
            logger_codenav.info(f"API /search called with project: {request_data.project_id}, query: '{request_data.query_text}'")
            if not self.embedding_model or not self.weaviate_manager.client: # Check again in request context
                raise HTTPException(status_code=503, detail=f"{SERVICE_NAME_CODENAV} not fully initialized.")
            try:
                # Use the agent's perform_search method
                results = self.perform_search( # This method already includes tracing
                    project_id=request_data.project_id,
                    query_text=request_data.query_text,
                    limit=request_data.limit,
                    search_filters=request_data.filters
                )
                return ApiSearchResponse(results=[ApiCodeSnippet(**res) for res in results])
            except Exception as e:
                logger_codenav.error(f"Error in /search endpoint: {e}", exc_info=True)
                # trace.get_current_span().record_exception(e) # Record exception on current span
                # trace.get_current_span().set_status(trace.Status(trace.StatusCode.ERROR, "Search endpoint error"))
                raise HTTPException(status_code=500, detail="Internal server error during search.")

        @api_app.post("/index_project", summary="Trigger indexing of a project directory")
        async def index_project_endpoint(request_data: ApiIndexRequest):
            logger_codenav.info(f"API /index_project called for project: {request_data.project_id}, path: {request_data.directory_path_in_container}")

            # This path needs to be accessible within the CodeNavAgent's container
            # Typically, you'd have a base path (e.g., from CODE_BASE_PATH env var)
            # and project_id might map to a subfolder within that base path.
            # For this example, assume directory_path_in_container is relative to some known root.
            # It's better to make directory_path_in_container an absolute path within the container
            # or a path relative to a configured CODE_BASE_PATH.

            # Sanitize/validate path?
            # path_to_index = os.path.join(CODE_BASE_PATH, request_data.project_id, request_data.directory_path_in_container) # Example
            path_to_index = request_data.directory_path_in_container # If it's already an absolute path in container

            if not os.path.isdir(path_to_index): # Check if path exists
                 logger_codenav.error(f"Directory not found for indexing: {path_to_index}")
                 raise HTTPException(status_code=404, detail=f"Directory not found in container: {path_to_index}")

            # In a production system, indexing should be an async background task.
            # FastAPI's BackgroundTasks is one way, or a proper task queue like Celery.
            # For now, direct call (will block the API response until done).
            try:
                self.index_project_directory(request_data.project_id, path_to_index)
                return {"message": f"Indexing started/completed for project {request_data.project_id}, directory {path_to_index}"}
            except Exception as e:
                logger_codenav.error(f"Error triggering indexing via API: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail="Error during indexing process.")

        return api_app

    def run_worker(self):
        logger_codenav.info(f"{SERVICE_NAME_CODENAV} worker starting up...")
        # ... (initial indexing scan from CODE_BASE_PATH as defined before) ...
        if CODE_BASE_PATH and os.path.isdir(CODE_BASE_PATH):
            for project_id_dir in os.listdir(CODE_BASE_PATH):
                project_full_path = os.path.join(CODE_BASE_PATH, project_id_dir)
                if os.path.isdir(project_full_path):
                    logger_codenav.info(f"Initial scan: Indexing project directory: {project_full_path} (ID: {project_id_dir})")
                    self.index_project_directory(project_id=project_id_dir, directory_path=project_full_path)
        else:
            logger_codenav.warning(f"CODE_BASE_PATH ('{CODE_BASE_PATH}') not set or not a directory. No initial indexing scan.")

        # Start the FastAPI server
        api_app = self.create_api_app()
        api_port = int(os.getenv("PORT", "8001")) # PORT for Railway
        uvicorn.run(api_app, host="0.0.0.0", port=api_port, log_config=None) # log_config=None to let our logging setup take over

if __name__ == "__main__":
    agent = CodeNavAgent()
    agent.run_worker()
