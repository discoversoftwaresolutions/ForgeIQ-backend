# ============================================
# 📁 agents/CodeNavAgent/agent.py (V0.2)
# ============================================
import os
import json
import time # For potential delays in worker loop, or if needed by other logic
from sentence_transformers import SentenceTransformer # Keep this import
import logging
from typing import List, Dict, Any, Optional

# --- Observability Setup (as before) ---
SERVICE_NAME_CODENAV = "CodeNavAgent"
LOG_LEVEL_CODENAV = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL_CODENAV, format=f'%(asctime)s - {SERVICE_NAME_CODENAV} - %(name)s - %(levelname)s - %(message)s')
_tracer_codenav = None; _trace_api_codenav = None # Use agent-specific names for tracer vars
try:
    from opentelemetry import trace as otel_trace_api
    from core.observability.tracing import setup_tracing
    _tracer_codenav = setup_tracing(SERVICE_NAME_CODENAV)
    _trace_api_codenav = otel_trace_api
except ImportError: logging.getLogger(SERVICE_NAME_CODENAV).warning("CodeNavAgent: Tracing setup failed.")
logger_codenav = logging.getLogger(__name__)
# --- End Observability Setup ---

# Use the core embedding services
from core.embeddings import EmbeddingModelService, VectorStoreClient, CODE_SNIPPET_CLASS_NAME, CODE_SNIPPET_SCHEMA
# Use the new core code parser
from core.code_utils.code_parser import scan_code_directory, chunk_code_content, get_language_from_extension, CodeChunk

# FastAPI and Pydantic for API (as defined in V0.1 of CodeNavAgent)
from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel, Field 
import uvicorn

CODE_BASE_PATH = os.getenv("CODE_BASE_PATH", "/codesrc")
# EMBEDDING_MODEL_NAME is now handled by EmbeddingModelService using its own env var

# API Models (as defined in V0.1 of CodeNavAgent - response #43, section 6)
class ApiSearchRequest(BaseModel): # ... (definition from response #43)
    project_id: str; query_text: str
    limit: Optional[int] = Field(default=10, ge=1, le=100)
    filters: Optional[Dict[str, Any]] = None
class ApiCodeSnippet(BaseModel): # ... (definition from response #43)
    file_path: str; snippet: str; score: float
    language: Optional[str] = None; start_line: Optional[int] = None
    end_line: Optional[int] = None; metadata: Optional[Dict[str, Any]] = None
    raw_weaviate_id: Optional[str] = None
class ApiSearchResponse(BaseModel): results: List[ApiCodeSnippet]
class ApiIndexRequest(BaseModel): project_id: str; directory_path_in_container: str


class CodeNavAgent:
    def __init__(self):
        logger_codenav.info(f"Initializing CodeNavAgent (V0.2)...")
        self.embedding_service: Optional[EmbeddingModelService] = None
        self.vector_store_client: Optional[VectorStoreClient] = None
        try:
            self.embedding_service = EmbeddingModelService() # Uses EMBEDDING_MODEL_NAME from env
            self.vector_store_client = VectorStoreClient() # Uses WEAVIATE_URL from env

            if self.vector_store_client.is_ready():
                self.vector_store_client.ensure_schema_class_exists(CODE_SNIPPET_CLASS_NAME, CODE_SNIPPET_SCHEMA)
            else:
                logger_codenav.error("CodeNavAgent: VectorStoreClient not ready. Indexing and search will fail.")
                self.vector_store_client = None # Ensure it's None
        except RuntimeError as e: 
            logger_codenav.error(f"CodeNavAgent initialization failed (model or Weaviate): {e}", exc_info=True)
            self.embedding_service = None
            self.vector_store_client = None
        logger_codenav.info("CodeNavAgent V0.2 Initialized.")

    @property
    def tracer(self): return _tracer_codenav # For OpenTelemetry

    def _start_trace_span_if_available(self, operation_name: str, parent_context: Optional[Any] = None, **attrs):
        # ... (same _start_trace_span_if_available helper as in other agents)
        if self.tracer and _trace_api_codenav:
            span = self.tracer.start_span(f"codenav_agent.{operation_name}", context=parent_context)
            for k, v in attrs.items(): span.set_attribute(k, v)
            return span
        class NoOpSpan: # Fallback
            def __enter__(self): return self; 
            def __exit__(self,tp,vl,tb): pass; 
            def set_attribute(self,k,v): pass; 
            def record_exception(self,e,attributes=None): pass; 
            def set_status(self,s): pass;
            def end(self): pass
        return NoOpSpan()


    def index_project_directory(self, project_id: str, directory_path: str):
        """Indexes code from a given directory for a project."""
        span = self._start_trace_span_if_available("index_project_directory", project_id=project_id, directory_path=directory_path)
        try:
            with span: # type: ignore
                if not self.embedding_service or not self.vector_store_client or not self.vector_store_client.is_ready():
                    logger_codenav.error("Cannot index: Embedding service or VectorStoreClient not ready.")
                    if _trace_api_codenav: span.set_status(_trace_api_codenav.Status(_trace_api_codenav.StatusCode.ERROR, "Agent not ready"))
                    return

                logger_codenav.info(f"CodeNavAgent V0.2: Starting indexing for project '{project_id}' in '{directory_path}'")

                # Use the new core code_parser
                files_to_index = scan_code_directory(directory_path, 
                                                     allowed_extensions=[".py", ".js", ".ts", ".java", ".go", ".md", ".txt"] # Example
                                                    ) 
                if not files_to_index:
                    logger_codenav.info(f"No files found to index in {directory_path} for project {project_id}.")
                    if _trace_api_codenav: span.set_attribute("indexing.files_found", 0); span.set_status(_trace_api_codenav.Status(_trace_api_codenav.StatusCode.OK))
                    return

                if _trace_api_codenav: span.set_attribute("indexing.files_to_process_count", len(files_to_index))
                all_objects_for_weaviate: List[Dict[str, Any]] = []

                for i, file_path in enumerate(files_to_index):
                    relative_file_path = os.path.relpath(file_path, directory_path)
                    logger_codenav.debug(f"Processing file {i+1}/{len(files_to_index)}: {relative_file_path}")
                    file_span = self._start_trace_span_if_available("index_file", parent_context=_trace_api_codenav.set_span_in_context(span) if _trace_api_codenav and span else None, file_path=relative_file_path)
                    try:
                        with file_span: #type: ignore
                            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f: content = f.read()
                            if not content.strip(): continue

                            language = get_language_from_extension(file_path)
                            if not language: continue # Or treat as 'text'

                            code_chunks_typed: List[CodeChunk] = chunk_code_content(content, language=language, file_path=relative_file_path)
                            if not code_chunks_typed: continue

                            chunk_contents_for_embedding = [chunk["content"] for chunk in code_chunks_typed]
                            vectors = self.embedding_service.generate_embeddings(chunk_contents_for_embedding)

                            for chunk_idx, chunk_data_typed in enumerate(code_chunks_typed):
                                if not vectors[chunk_idx]: continue # Skip if embedding failed

                                # V0.2 Incremental Indexing Concept:
                                # Before adding, check if contentHash + filePath already exists.
                                # This requires querying Weaviate or maintaining a local hash set.
                                # For this V0.2, we'll rely on Weaviate's UUID-based upsert if we generate
                                # deterministic UUIDs, or just add and let Weaviate handle potential duplicates if any.
                                # A more robust check:
                                # existing_uuid = self.vector_store_client.find_by_hash_and_path(CODE_SNIPPET_CLASS_NAME, chunk_data_typed['contentHash'], relative_file_path, project_id)
                                # if existing_uuid: continue # Skip if identical content exists

                                weaviate_data_obj = {
                                    "projectId": project_id, "filePath": relative_file_path, 
                                    "content": chunk_data_typed["content"], "language": language, 
                                    "startLine": chunk_data_typed["startLine"], "end_Line": chunk_data_typed["endLine"], # Corrected key
                                    "contentHash": chunk_data_typed["contentHash"], 
                                    "metadataJson": chunk_data_typed["metadataJson"]
                                }
                                all_objects_for_weaviate.append({
                                    "data_object": weaviate_data_obj,
                                    "vector": vectors[chunk_idx]
                                    # "uuid": self._generate_deterministic_uuid(project_id, relative_file_path, chunk_data_typed['contentHash']) # For upserts
                                })
                    except Exception as e_file:
                        logger_codenav.error(f"Error processing file {relative_file_path} for indexing: {e_file}", exc_info=True)
                        if _trace_api_codenav and file_span: file_span.record_exception(e_file); file_span.set_status(_trace_api_codenav.Status(_trace_api_codenav.StatusCode.ERROR))

                if all_objects_for_weaviate:
                    logger_codenav.info(f"CodeNavAgent V0.2: Batch adding/updating {len(all_objects_for_weaviate)} snippets for project {project_id}.")
                    self.vector_store_client.batch_upsert_objects(CODE_SNIPPET_CLASS_NAME, all_objects_for_weaviate)
                else:
                    logger_codenav.info(f"CodeNavAgent V0.2: No valid chunks to index for project {project_id} in {directory_path}.")

                if _trace_api_codenav: span.set_status(_trace_api_codenav.Status(_trace_api_codenav.StatusCode.OK))
                logger_codenav.info(f"CodeNavAgent V0.2: Finished indexing attempt for project '{project_id}'")
        except Exception as e_main:
            logger_codenav.error(f"Major error during indexing project '{project_id}': {e_main}", exc_info=True)
            if _trace_api_codenav and span: span.record_exception(e_main); span.set_status(_trace_api_codenav.Status(_trace_api_codenav.StatusCode.ERROR, "Main indexing error"))


    def perform_search(self, project_id: str, query_text: str, limit: int = 10, search_filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        # ... (perform_search logic using self.embedding_service and self.vector_store_client as in response #68, CodeNavAgent section) ...
        # This method should be largely the same, just ensure it uses the V0.2 core services.
        span = self._start_trace_span_if_available("perform_search", project_id=project_id, query_length=len(query_text), limit=limit)
        try:
            with span: # type: ignore
                if not self.embedding_service or not self.vector_store_client or not self.vector_store_client.is_ready():
                    logger_codenav.error("Cannot search: Agent not fully initialized.")
                    if _trace_api_codenav: span.set_status(_trace_api_codenav.Status(_trace_api_codenav.StatusCode.ERROR, "Agent not ready for search"))
                    return []

                logger_codenav.info(f"CodeNavAgent V0.2: Performing search in project '{project_id}' for query: '{query_text[:50]}...'")
                query_embeddings = self.embedding_service.generate_embeddings(query_text)
                if not query_embeddings or not query_embeddings[0]:
                    logger_codenav.error("Failed to generate embedding for search query."); return []
                query_vector = query_embeddings[0]

                properties_to_return = CODE_SNIPPET_SCHEMA["properties"] # Get all defined properties
                properties_to_return_names = [p["name"] for p in properties_to_return]

                raw_results = self.vector_store_client.semantic_search(
                    class_name=CODE_SNIPPET_CLASS_NAME, query_vector=query_vector,
                    properties_to_return=properties_to_return_names, limit=limit,
                    project_id_filter=project_id, additional_filters_weaviate=search_filters
                )

                # Format results into ApiCodeSnippet-like dicts (as in CodeNavAgent V0.1 API response)
                results = []
                for raw_item in raw_results:
                    metadata_json_str = raw_item.get("metadataJson", "{}")
                    try: metadata = json.loads(metadata_json_str)
                    except json.JSONDecodeError: metadata = {"parsing_error": "invalid_json", "raw": metadata_json_str[:100]}

                    results.append({
                        "file_path": raw_item.get("filePath"), "snippet": raw_item.get("content"),
                        "score": raw_item.get("_additional", {}).get("certainty", raw_item.get("_additional", {}).get("distance")), # Use certainty or distance
                        "language": raw_item.get("language"), "start_line": raw_item.get("startLine"),
                        "end_line": raw_item.get("endLine"), "metadata": metadata,
                        "raw_weaviate_id": raw_item.get("_additional", {}).get("id")
                    })
                if _trace_api_codenav: span.set_attribute("search.results_count", len(results)); span.set_status(_trace_api_codenav.Status(_trace_api_codenav.StatusCode.OK))
                return results
        except Exception as e:
            logger_codenav.error(f"Error during CodeNav search: {e}", exc_info=True)
            if _trace_api_codenav and span: span.record_exception(e); span.set_status(_trace_api_codenav.Status(_trace_api_codenav.StatusCode.ERROR, "Search logic error"))
            return []


    def create_api_app(self): # As defined in Response #43 CodeNavAgent V0.1
        # ... (FastAPI app with /search and /index_project endpoints, using self.perform_search and self.index_project_directory) ...
        api_app = FastAPI(title=f"{SERVICE_NAME_CODENAV} API (V0.2)")
        logger_codenav.info(f"FastAPI app created for {SERVICE_NAME_CODENAV}")
        if _tracer_codenav: # Instrument FastAPI app
            try:
                # Ensure FastAPIInstrumentor is imported where app is defined, or pass tracer_provider if needed
                from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
                FastAPIInstrumentor.instrument_app(api_app, tracer_provider=_tracer_codenav.provider if _tracer_codenav else None)
            except Exception as e_instr: logger_codenav.error(f"FastAPI OTel instrumentation failed: {e_instr}")

        @api_app.post("/search", response_model=ApiSearchResponse, summary="Perform semantic code search")
        async def search_endpoint(request_data: ApiSearchRequest): # Using Pydantic model from this file
            # ... (logic as in response #43, calling self.perform_search) ...
            logger_codenav.info(f"API /search: project='{request_data.project_id}', query='{request_data.query_text[:50]}...'")
            if not self.embedding_service or not self.vector_store_client: raise HTTPException(status_code=503, detail="Agent not fully initialized.")
            try:
                results = self.perform_search(project_id=request_data.project_id, query_text=request_data.query_text, limit=request_data.limit, search_filters=request_data.filters)
                return ApiSearchResponse(results=[ApiCodeSnippet(**res) for res in results]) # Ensure results match ApiCodeSnippet
            except Exception as e: logger_codenav.error(f"Error in /search: {e}", exc_info=True); raise HTTPException(status_code=500, detail=str(e))

        @api_app.post("/index_project", summary="Trigger indexing of a project directory")
        async def index_project_endpoint(request_data: ApiIndexRequest): # Using Pydantic model
            # ... (logic as in response #43, calling self.index_project_directory) ...
            # This should be an async background task in production
            logger_codenav.info(f"API /index_project: project='{request_data.project_id}', path='{request_data.directory_path_in_container}'")
            # Construct full path based on CODE_BASE_PATH and project_id
            # Assume directory_path_in_container is relative to project_id base under CODE_BASE_PATH
            # Or, if directory_path_in_container is already an absolute path in the container:
            path_to_index = request_data.directory_path_in_container
            if not path_to_index.startswith(CODE_BASE_PATH): # Basic check if it's under allowed base
                 # More robust path validation and construction needed here
                 # This example assumes client provides a safe, valid path within container.
                 pass # Allow for now, but needs hardening

            if not os.path.isdir(path_to_index): raise HTTPException(status_code=404, detail=f"Directory not found: {path_to_index}")
            try:
                async def do_index(): self.index_project_directory(request_data.project_id, path_to_index)
                asyncio.create_task(do_index()) # Fire-and-forget for V0.2 API
                return {"message": f"Indexing initiated for project {request_data.project_id}, directory {path_to_index}"}
            except Exception as e: logger_codenav.error(f"Error triggering indexing: {e}", exc_info=True); raise HTTPException(status_code=500, detail=str(e))
        return api_app

    def run_worker(self): # Main entry point called by Docker CMD
        logger_codenav.info(f"{SERVICE_NAME_CODENAV} worker (V0.2) starting up...")
        # Perform initial scan if CODE_BASE_PATH is set and valid
        if CODE_BASE_PATH and os.path.isdir(CODE_BASE_PATH):
            logger_codenav.info(f"Performing initial scan of CODE_BASE_PATH: {CODE_BASE_PATH}")
            # Assuming subdirectories in CODE_BASE_PATH are project_ids
            for project_id_entry in os.listdir(CODE_BASE_PATH):
                project_full_path = os.path.join(CODE_BASE_PATH, project_id_entry)
                if os.path.isdir(project_full_path):
                    logger_codenav.info(f"Initial scan: Queueing indexing for project directory: {project_full_path} (ID: {project_id_entry})")
                    # For V0.2, let's make indexing an async task to not block startup
                    # In a more robust system, this might publish messages to itself via event bus
                    # or use an internal task queue.
                    async def initial_index_task(pid, ppath):
                        self.index_project_directory(project_id=pid, directory_path=ppath)

                    # This needs an event loop to run the async task if run_worker itself is not async
                    # If run_worker is the main entry point for uvicorn, this approach for initial scan needs care.
                    # Uvicorn expects an ASGI app. Let's assume this scan is quick or done before starting server.
                    # For simplicity, direct call for now. If too long, it blocks server start.
                    self.index_project_directory(project_id=project_id_entry, directory_path=project_full_path)
        else:
            logger_codenav.warning(f"CODE_BASE_PATH ('{CODE_BASE_PATH}') not set or not a directory. No initial automatic indexing scan.")

        # Start the FastAPI server
        api_app_instance = self.create_api_app()
        api_port = int(os.getenv("PORT", "8001")) # Railway provides $PORT
        uvicorn.run(api_app_instance, host="0.0.0.0", port=api_port, log_config=None) # Use our logging

if __name__ == "__main__":
    # This ensures OTel and other initializations are done before agent runs
    agent = CodeNavAgent()
    agent.run_worker()
