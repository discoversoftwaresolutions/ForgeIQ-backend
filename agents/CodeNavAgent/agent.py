# agents/CodeNavAgent/app/agent.py (Simplified Example)
import os
import logging
# from core.event_bus.redis_bus import EventBus # if needed
from core.embeddings import EmbeddingModelService, VectorStoreClient, CODE_SNIPPET_CLASS_NAME, CODE_SNIPPET_SCHEMA # Import from new core module
from .code_parser import chunk_code_content, scan_code_directory, get_language_from_extension # Keep local parser for now

# ... (Observability setup as before for CodeNavAgent) ...
logger = logging.getLogger(__name__) # Assuming basicConfig is done by OTel setup or earlier

CODE_BASE_PATH = os.getenv("CODE_BASE_PATH", "/codesrc")

class CodeNavAgent:
    def __init__(self):
        logger.info("Initializing CodeNavAgent...")
        try:
            self.embedding_service = EmbeddingModelService() # Uses EMBEDDING_MODEL_NAME from env
            self.vector_store_client = VectorStoreClient() # Uses WEAVIATE_URL from env
            
            if self.vector_store_client.is_ready():
                self.vector_store_client.ensure_schema_class_exists(CODE_SNIPPET_CLASS_NAME, CODE_SNIPPET_SCHEMA)
            else:
                logger.error("CodeNavAgent: VectorStoreClient not ready. Indexing and search will fail.")
        except RuntimeError as e: # Catch model initialization errors
            logger.error(f"CodeNavAgent initialization failed: {e}", exc_info=True)
            self.embedding_service = None
            self.vector_store_client = None
        # self.event_bus = EventBus()
        logger.info("CodeNavAgent Initialized.")

    # ... (index_project_directory method would now use self.embedding_service.generate_embeddings
    #      and self.vector_store_client.batch_upsert_objects) ...

    def index_project_directory(self, project_id: str, directory_path: str):
        if not self.embedding_service or not self.vector_store_client or not self.vector_store_client.is_ready():
            logger.error("Cannot index: Embedding service or VectorStoreClient not ready.")
            return
        
        logger.info(f"CodeNavAgent: Starting indexing for project '{project_id}' in '{directory_path}'")
        # ... (scan_code_directory logic as before) ...
        files_to_index = scan_code_directory(directory_path)
        all_objects_for_weaviate = []

        for file_path in files_to_index:
            relative_file_path = os.path.relpath(file_path, directory_path)
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f: content = f.read()
                if not content.strip(): continue
                language = get_language_from_extension(file_path)
                if not language: continue
                
                code_chunks_info = chunk_code_content(content, language=language, file_path=relative_file_path)
                if not code_chunks_info: continue
                
                chunk_contents = [chunk["content"] for chunk in code_chunks_info]
                vectors = self.embedding_service.generate_embeddings(chunk_contents)

                for i, chunk_info in enumerate(code_chunks_info):
                    if not vectors[i]: # Skip if embedding failed for this chunk
                        logger.warning(f"Skipping chunk due to embedding failure: {relative_file_path} lines {chunk_info['startLine']}-{chunk_info['endLine']}")
                        continue
                    
                    data_obj = { # Matches CODE_SNIPPET_SCHEMA properties
                        "projectId": project_id, "filePath": relative_file_path, "content": chunk_info["content"],
                        "language": language, "startLine": chunk_info["startLine"], "endLine": chunk_info["endLine"],
                        "contentHash": chunk_info["contentHash"], "metadataJson": chunk_info["metadataJson"]
                    }
                    all_objects_for_weaviate.append({
                        "data_object": data_obj,
                        "vector": vectors[i]
                        # "uuid": generate_deterministic_uuid_here_if_needed_for_upsert
                    })
            except Exception as e:
                logger.error(f"Error processing file {file_path} for indexing: {e}", exc_info=True)

        if all_objects_for_weaviate:
            logger.info(f"CodeNavAgent: Batch adding {len(all_objects_for_weaviate)} snippets for project {project_id}.")
            self.vector_store_client.batch_upsert_objects(CODE_SNIPPET_CLASS_NAME, all_objects_for_weaviate)
        else:
            logger.info(f"CodeNavAgent: No valid chunks generated for project {project_id} in {directory_path}.")
        logger.info(f"CodeNavAgent: Finished indexing attempt for project '{project_id}'")


    # ... (perform_search method would use self.embedding_service.generate_embeddings for the query
    #      and self.vector_store_client.semantic_search) ...

    def perform_search(self, project_id: str, query_text: str, limit: int = 10, search_filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        if not self.embedding_service or not self.vector_store_client or not self.vector_store_client.is_ready():
            logger.error("Cannot search: Embedding service or VectorStoreClient not ready.")
            return []

        logger.info(f"CodeNavAgent: Performing search in project '{project_id}' for query: '{query_text}'")
        query_embeddings = self.embedding_service.generate_embeddings(query_text)
        if not query_embeddings or not query_embeddings[0]:
            logger.error("Failed to generate embedding for search query.")
            return []
        query_vector = query_embeddings[0] # generate_embeddings returns a list

        properties_to_return = ["content", "filePath", "projectId", "language", "startLine", "endLine", "metadataJson"]
        
        # Convert simple dict filters to Weaviate 'where' filter if needed by VectorStoreClient
        # For now, assuming VectorStoreClient's semantic_search can take basic dict or handles conversion
        weaviate_filters = None
        if search_filters:
            # This is a placeholder: transform search_filters into the format VectorStoreClient expects if necessary
            # For example, if search_filters = {"language": "python"}, it might become:
            # weaviate_filters = {"operator": "And", "operands": [{"path": ["language"], "operator": "Equal", "valueString": "python"}]}
            # The VectorStoreClient's semantic_search currently has a simple project_id_filter and a generic additional_filters_weaviate
            pass # For now, VectorStoreClient takes project_id_filter separately.

        raw_results = self.vector_store_client.semantic_search(
            class_name=CODE_SNIPPET_CLASS_NAME,
            query_vector=query_vector,
            properties_to_return=properties_to_return,
            limit=limit,
            project_id_filter=project_id, # VectorStoreClient handles adding this to where clause
            additional_filters_weaviate=search_filters # Pass other filters directly
        )
        
        # Format results (same as before, but now using raw_results from common client)
        results = []
        for raw_item in raw_results:
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
        
    # ... (run_worker and API server setup using FastAPI as defined in CodeNavAgent V0.1,
    #      now calling self.perform_search and self.index_project_directory) ...
    # The create_api_app and run_worker methods previously defined for CodeNavAgent would largely remain,
    # but their internal calls would now use self.embedding_service and self.vector_store_client.

    # (Continuing with the rest of CodeNavAgent.py: create_api_app, run_worker, if __name__ == "__main__": ...)
    # This part is mostly the same as the CodeNavAgent V0.1 from response #43, section 6,
    # just ensuring it uses self.embedding_service and self.vector_store_client.
    # For brevity here, I'll assume those methods are adapted.
    def create_api_app(self):
        from fastapi import FastAPI, HTTPException, Body
        from pydantic import BaseModel, Field # For request/response validation
        
        class ApiSearchRequest(BaseModel):
            project_id: str
            query_text: str
            limit: Optional[int] = Field(default=10, ge=1, le=100)
            filters: Optional[Dict[str, Any]] = None

        class ApiCodeSnippet(BaseModel): 
            file_path: str; snippet: str; score: float
            language: Optional[str] = None; start_line: Optional[int] = None
            end_line: Optional[int] = None; metadata: Optional[Dict[str, Any]] = None
            raw_weaviate_id: Optional[str] = None

        class ApiSearchResponse(BaseModel): results: List[ApiCodeSnippet]
        class ApiIndexRequest(BaseModel): project_id: str; directory_path_in_container: str

        api_app = FastAPI(title=f"{SERVICE_NAME} API for CodeNav")
        logger.info(f"FastAPI app created for {SERVICE_NAME}")
        
        # OTel Instrumentation (if tracer is available)
        # from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        # if self.tracer: FastAPIInstrumentor.instrument_app(api_app, tracer_provider=self.tracer.provider)

        @api_app.post("/search", response_model=ApiSearchResponse, summary="Perform semantic code search")
        async def search_endpoint(request_data: ApiSearchRequest):
            logger.info(f"API /search: project='{request_data.project_id}', query='{request_data.query_text[:50]}...'")
            if not self.embedding_service or not self.vector_store_client:
                raise HTTPException(status_code=503, detail="CodeNavAgent not fully initialized.")
            try:
                results = self.perform_search(
                    project_id=request_data.project_id, query_text=request_data.query_text,
                    limit=request_data.limit, search_filters=request_data.filters
                )
                # Convert dicts to ApiCodeSnippet if needed for Pydantic response model validation
                return ApiSearchResponse(results=[ApiCodeSnippet(**res) for res in results])
            except Exception as e:
                logger.error(f"Error in /search endpoint: {e}", exc_info=True)
                if self.tracer: trace.get_current_span().record_exception(e)
                raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

        @api_app.post("/index_project", summary="Trigger indexing of a project directory")
        async def index_project_endpoint(request_data: ApiIndexRequest):
            logger.info(f"API /index_project: project='{request_data.project_id}', path='{request_data.directory_path_in_container}'")
            path_to_index = os.path.join(CODE_BASE_PATH, request_data.project_id, request_data.directory_path_in_container)
            if not os.path.isdir(path_to_index):
                 raise HTTPException(status_code=404, detail=f"Directory not found in container: {path_to_index}")
            try:
                # Indexing can be long; ideally run as background task
                async def do_index(): self.index_project_directory(request_data.project_id, path_to_index)
                asyncio.create_task(do_index()) # Fire and forget for this example
                return {"message": f"Indexing initiated for project {request_data.project_id}, directory {path_to_index}"}
            except Exception as e:
                logger.error(f"Error triggering indexing via API: {e}", exc_info=True)
                if self.tracer: trace.get_current_span().record_exception(e)
                raise HTTPException(status_code=500, detail=f"Error during indexing process: {str(e)}")
        return api_app

    def run_worker(self): # Combined worker for initial scan + API server
        logger.info(f"{SERVICE_NAME} worker starting up...")
        if CODE_BASE_PATH and os.path.isdir(CODE_BASE_PATH):
            for project_id_dir in os.listdir(CODE_BASE_PATH):
                project_full_path = os.path.join(CODE_BASE_PATH, project_id_dir)
                if os.path.isdir(project_full_path):
                    logger.info(f"Initial scan: Indexing project directory: {project_full_path} (ID: {project_id_dir})")
                    self.index_project_directory(project_id=project_id_dir, directory_path=project_full_path)
        else:
            logger.warning(f"CODE_BASE_PATH ('{CODE_BASE_PATH}') not set or not a directory. No initial indexing.")

        api_app = self.create_api_app()
        api_port = int(os.getenv("PORT", "8001"))
        import uvicorn
        uvicorn.run(api_app, host="0.0.0.0", port=api_port, log_config=None)


if __name__ == "__main__":
    agent = CodeNavAgent()
    agent.run_worker()
