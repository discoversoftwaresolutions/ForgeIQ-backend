# ======================================
# 📁 scripts/seed-embeddings.py
# ======================================
import os
import sys
import argparse
import logging
import json # For metadataJson if needed by code_parser
from typing import List, Dict, Any, Optional

# --- Setup PYTHONPATH to find core, interfaces, agents ---
# This allows running the script from the monorepo root: python scripts/seed-embeddings.py
# For this to work, this script assumes it's in 'scripts/' and ROOT_DIR is one level up.
# More robustly, ensure PYTHONPATH is set in your environment before running.
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
# --- End PYTHONPATH setup ---

# Configure basic logging for the script
LOG_LEVEL_SEEDER = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL_SEEDER, format='%(asctime)s - %(levelname)s - Seeder - %(name)s: %(message)s')
logger = logging.getLogger(__name__)

# Import from your project's Python modules
try:
    from core.embeddings import EmbeddingModelService, VectorStoreClient, CODE_SNIPPET_CLASS_NAME, CODE_SNIPPET_SCHEMA
    # Assuming code_parser is now part of CodeNavAgent or a core utility
    # If it's in CodeNavAgent: from agents.CodeNavAgent.app.code_parser import scan_code_directory, chunk_code_content, get_language_from_extension
    # Let's assume for now we made code_parser a core utility for wider use:
    from core.code_utils import code_parser # You'll need to create core/code_utils/code_parser.py
except ImportError as e:
    logger.critical(f"Failed to import core modules: {e}. Ensure PYTHONPATH is set correctly to include the monorepo root.")
    logger.critical(f"Current sys.path: {sys.path}")
    logger.critical("Try running from monorepo root: `PYTHONPATH=. python scripts/seed-embeddings.py ...`")
    sys.exit(1)


# Placeholder for core.code_utils.code_parser - you'd move the robust parsing logic here
# For this script to run, this module needs to exist.
# Let's define a minimal version here if not created separately yet.
if 'core.code_utils.code_parser' not in sys.modules:
    logger.warning("Using placeholder code_parser. Create core/code_utils/code_parser.py with robust logic.")
    # Minimal placeholder if the actual module isn't created yet
    def scan_code_directory(dir_path: str, ignore_patterns: Optional[List[str]] = None) -> List[str]:
        found = []
        for r, _, f_names in os.walk(dir_path):
            for f_name in f_names:
                # Basic filter for common code extensions
                if f_name.endswith(('.py', '.js', '.ts', '.java', '.go', '.c', '.cpp', '.h', '.hpp', '.md')):
                    found.append(os.path.join(r, f_name))
        return found
    def get_language_from_extension(file_path: str) -> Optional[str]:
        _, ext = os.path.splitext(file_path)
        return ext[1:] if ext else None # Simplistic
    def chunk_code_content(content: str, language: Optional[str] = None, file_path: Optional[str] = None) -> List[Dict[str, Any]]:
        import hashlib # Import hashlib here for this placeholder
        lines = content.splitlines()
        if not lines: return []
        # Super simple: one chunk per file for this placeholder
        content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
        return [{"content": content, "startLine": 1, "endLine": len(lines), 
                 "contentHash": content_hash, "metadataJson": json.dumps({"type": "full_file_placeholder"})}]

    # Make them accessible via the assumed import path
    class _ParserModule: pass
    code_parser = _ParserModule() # type: ignore
    code_parser.scan_code_directory = scan_code_directory # type: ignore
    code_parser.get_language_from_extension = get_language_from_extension # type: ignore
    code_parser.chunk_code_content = chunk_code_content # type: ignore
# End placeholder


def seed_embeddings_for_project(
    project_id: str,
    project_code_path: str, # Absolute path to the project's code on the machine running this script
    embedding_service: EmbeddingModelService,
    vector_store: VectorStoreClient,
    batch_size: int = 50
):
    logger.info(f"Starting embedding seeding for project '{project_id}' from path: '{project_code_path}'")

    if not os.path.isdir(project_code_path):
        logger.error(f"Project code path does not exist or is not a directory: {project_code_path}")
        return

    if not vector_store.is_ready():
        logger.error(f"Vector store (Weaviate) is not ready. Cannot seed embeddings for '{project_id}'.")
        return

    # Ensure schema exists (CodeNavAgent also does this, but good to have here too)
    vector_store.ensure_schema_class_exists(CODE_SNIPPET_CLASS_NAME, CODE_SNIPPET_SCHEMA)

    logger.info("Scanning directory for code files...")
    # Use the code_parser from core.code_utils (or agents.CodeNavAgent.app.code_parser)
    try:
        all_files = code_parser.scan_code_directory(project_code_path) # type: ignore
    except Exception as e:
        logger.error(f"Error scanning directory {project_code_path}: {e}", exc_info=True)
        return

    logger.info(f"Found {len(all_files)} files to process for project '{project_id}'.")

    objects_to_batch_upsert: List[Dict[str, Any]] = []

    for i, file_path in enumerate(all_files):
        relative_file_path = os.path.relpath(file_path, project_code_path)
        logger.info(f"Processing file {i+1}/{len(all_files)}: {relative_file_path}")

        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            if not content.strip():
                logger.debug(f"Skipping empty file: {relative_file_path}")
                continue

            language = code_parser.get_language_from_extension(file_path) # type: ignore
            if not language:
                logger.debug(f"Skipping file with unknown language: {relative_file_path}")
                continue

            code_chunks_info = code_parser.chunk_code_content(content, language=language, file_path=relative_file_path) # type: ignore
            if not code_chunks_info:
                logger.debug(f"No chunks generated for file: {relative_file_path}")
                continue

            chunk_contents = [chunk["content"] for chunk in code_chunks_info]

            # Generate embeddings for all chunks in the file
            # The service handles batching if underlying model prefers it for multiple texts
            chunk_vectors = embedding_service.generate_embeddings(chunk_contents)

            for chunk_idx, chunk_data in enumerate(code_chunks_info):
                if not chunk_vectors[chunk_idx]: # Check if embedding failed for this chunk
                    logger.warning(f"Embedding failed for chunk in {relative_file_path}, lines {chunk_data['startLine']}-{chunk_data['endLine']}. Skipping.")
                    continue

                weaviate_object_data = {
                    "projectId": project_id,
                    "filePath": relative_file_path,
                    "content": chunk_data["content"],
                    "language": language,
                    "startLine": chunk_data["startLine"],
                    "endLine": chunk_data["endLine"],
                    "contentHash": chunk_data["contentHash"], # From code_parser
                    "metadataJson": chunk_data["metadataJson"] # From code_parser
                }
                objects_to_batch_upsert.append({
                    "data_object": weaviate_object_data,
                    "vector": chunk_vectors[chunk_idx]
                    # "uuid": # Optionally generate a deterministic UUID here for upserts
                })

            if len(objects_to_batch_upsert) >= batch_size:
                logger.info(f"Upserting batch of {len(objects_to_batch_upsert)} snippets to Weaviate...")
                vector_store.batch_upsert_objects(CODE_SNIPPET_CLASS_NAME, objects_to_batch_upsert)
                objects_to_batch_upsert = [] # Reset batch

        except Exception as e:
            logger.error(f"Error processing file '{relative_file_path}' for project '{project_id}': {e}", exc_info=True)

    # Upsert any remaining snippets in the last batch
    if objects_to_batch_upsert:
        logger.info(f"Upserting final batch of {len(objects_to_batch_upsert)} snippets to Weaviate...")
        vector_store.batch_upsert_objects(CODE_SNIPPET_CLASS_NAME, objects_to_batch_upsert)

    logger.info(f"Finished embedding seeding for project '{project_id}'.")


def main():
    parser = argparse.ArgumentParser(description="Seed embeddings for CodeNavAgent into Weaviate.")
    parser.add_argument("project_id", type=str, help="A unique identifier for the project being indexed.")
    parser.add_argument("code_path", type=str, help="The local file system path to the root of the codebase to index.")
    parser.add_argument("--model", type=str, default=os.getenv("EMBEDDING_MODEL_NAME", 'all-MiniLM-L6-v2'),
                        help="Name of the sentence-transformer model to use.")
    parser.add_argument("--weaviate-url", type=str, default=os.getenv("WEAVIATE_URL"),
                        help="URL of the Weaviate instance.")
    parser.add_argument("--weaviate-key", type=str, default=os.getenv("WEAVIATE_API_KEY"),
                        help="API key for Weaviate Cloud (if applicable).")
    parser.add_argument("--batch-size", type=int, default=50, help="Batch size for Weaviate upserts.")
    parser.add_argument("--log-level", type=str, default=os.getenv("LOG_LEVEL", "INFO").upper(), 
                        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Logging level.")

    args = parser.parse_args()

    # Reconfigure logger if log level argument is different from env var default
    if args.log_level != LOG_LEVEL_SEEDER:
        logging.getLogger().setLevel(args.log_level) # Set root logger level
        for handler in logging.getLogger().handlers: # Update existing handlers
            handler.setLevel(args.log_level)
        logger.info(f"Logging level set to {args.log_level}")


    if not args.weaviate_url:
        logger.critical("WEAVIATE_URL must be provided via argument --weaviate-url or environment variable.")
        sys.exit(1)

    try:
        logger.info(f"Initializing services for embedding seeder...")
        # Override environment variables if CLI args are provided, for service initialization
        os.environ["EMBEDDING_MODEL_NAME"] = args.model 
        os.environ["WEAVIATE_URL"] = args.weaviate_url
        if args.weaviate_key:
             os.environ["WEAVIATE_API_KEY"] = args.weaviate_key

        embedding_svc = EmbeddingModelService(model_name=args.model)
        vector_store_client = VectorStoreClient(weaviate_url=args.weaviate_url, api_key=args.weaviate_key)

        if not embedding_svc._model: # Check if model loaded
             logger.critical("Failed to load embedding model. Exiting.")
             sys.exit(1)
        if not vector_store_client.is_ready():
             logger.critical("Failed to connect to Weaviate or Weaviate is not ready. Exiting.")
             sys.exit(1)

        seed_embeddings_for_project(
            project_id=args.project_id,
            project_code_path=args.code_path,
            embedding_service=embedding_svc,
            vector_store=vector_store_client,
            batch_size=args.batch_size
        )
        logger.info("Embedding seeding process completed.")

    except Exception as e:
        logger.critical(f"An error occurred during the embedding seeding process: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    # This script is intended to be run from the command line.
    # Ensure all necessary environment variables (WEAVIATE_URL, etc.) are set,
    # or pass them as arguments.
    # Also ensure PYTHONPATH is set up so it can find 'core' and 'agents' packages.
    # Example from monorepo root:
    # export PYTHONPATH=$(pwd):$PYTHONPATH
    # export WEAVIATE_URL="http://localhost:8080" 
    # python scripts/seed-embeddings.py my_project ./path_to_my_project_code
    main()
