# agents/CodeNavAgent/code_parser.py
import os
import hashlib
from typing import List, Dict, Tuple, Optional
import logging

logger = logging.getLogger(__name__)

# Basic support for chunking by trying to identify functions/classes
# This is a very simplistic parser. For robust production use, consider
# tree-sitter or other language-specific AST parsers.
CHUNK_PATTERNS = {
    'python': [r"^\s*def\s+\w+\s*\(", r"^\s*class\s+\w+\s*[:\(]"],
    'javascript': [r"^\s*(async\s+)?function\s+\w+\s*\(", r"^\s*class\s+\w+\s*\{", r"^\s*(\w+)\s*=\s*(async\s+)?function\s*\(", r"^\s*(\w+)\s*=\s*\((\w+,?\s*)*\)\s*=>"],
    'typescript': [r"^\s*(async\s+)?function\s+\w+\s*\(", r"^\s*class\s+\w+\s*\{", r"^\s*interface\s+\w+\s*\{", r"^\s*type\s+\w+\s*=", r"^\s*(\w+)\s*:\s*\((\w+: \w+,?\s*)*\)\s*=>"],
    # Add patterns for other languages
}
# Max lines for a chunk if no structural pattern matches, or as a fallback
MAX_LINES_PER_CHUNK = 100 
OVERLAP_LINES = 10 # Overlap for sliding window if structural parsing fails

def get_language_from_extension(file_path: str) -> Optional[str]:
    _, ext = os.path.splitext(file_path)
    lang_map = {
        ".py": "python", ".js": "javascript", ".ts": "typescript", ".java": "java",
        ".go": "go", ".rs": "rust", ".c": "c", ".cpp": "cpp", ".h": "c", ".hpp": "cpp",
        ".md": "markdown", ".txt": "text"
    }
    return lang_map.get(ext.lower())

def chunk_code_content(content: str, language: Optional[str] = None, file_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Chunks code content into smaller pieces for embedding.
    Returns a list of dictionaries, each representing a chunk.
    Each dict contains: 'content', 'startLine', 'endLine', 'metadataJson'.
    """
    chunks = []
    lines = content.splitlines()
    if not lines:
        return []

    # TODO: Implement more sophisticated chunking, possibly using tree-sitter
    # For v0.1, we can use a simpler line-based or very basic regex-based approach.

    # Basic approach: fixed-size overlapping chunks as a fallback
    # A more advanced approach would try to identify functions/classes first.
    current_line_num = 0
    while current_line_num < len(lines):
        start_line = current_line_num + 1 # 1-indexed
        end_line_idx = min(current_line_num + MAX_LINES_PER_CHUNK, len(lines))
        chunk_content_lines = lines[current_line_num:end_line_idx]
        chunk_content = "\n".join(chunk_content_lines)

        # Create a hash for the content to detect changes easily
        content_hash = hashlib.sha256(chunk_content.encode('utf-8')).hexdigest()

        chunks.append({
            "content": chunk_content,
            "startLine": start_line,
            "endLine": end_line_idx, # This is 1-indexed end line number
            "contentHash": content_hash,
            "metadataJson": json.dumps({"type": "generic_chunk"}) # Add more metadata later
        })

        if end_line_idx == len(lines): # Reached end of file
            break
        current_line_num += (MAX_LINES_PER_CHUNK - OVERLAP_LINES) # Move window with overlap
        if current_line_num >= len(lines) : break # Ensure we don't go past the end on the next iteration's start

    if not chunks and content: # If no chunks were made but there's content, make one big chunk
         content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
         chunks.append({
            "content": content, "startLine": 1, "endLine": len(lines), "contentHash": content_hash,
            "metadataJson": json.dumps({"type": "full_file_chunk"})
         })

    logger.debug(f"Chunked {file_path or 'content'} into {len(chunks)} chunks.")
    return chunks


# Placeholder for a function that would walk a directory and yield file paths
def scan_code_directory(dir_path: str, ignore_patterns: Optional[List[str]] = None) -> List[str]:
    # TODO: Implement robust directory scanning, respecting .gitignore or other ignore files
    # For now, a very simple os.walk
    if ignore_patterns is None:
        ignore_patterns = ['.git', 'node_modules', '__pycache__', 'dist', 'build', '.venv', 'venv'] # Basic ignores

    found_files = []
    for root, dirs, files in os.walk(dir_path, topdown=True):
        # Filter out ignored directories
        dirs[:] = [d for d in dirs if d not in ignore_patterns]

        for file_name in files:
            # Filter out ignored file names/extensions if needed
            # For now, just basic check against ignored top-level dirs
            if any(ignored_part in root for ignored_part in ignore_patterns):
                continue
            if file_name in ignore_patterns: # e.g. if 'package-lock.json' was in ignore_patterns
                continue

            file_path = os.path.join(root, file_name)
            # Potentially filter by file extension here if desired
            # lang = get_language_from_extension(file_path)
            # if lang in ['python', 'javascript', 'typescript', 'markdown']: # Example filter
            found_files.append(file_path)
    logger.info(f"Scanned directory {dir_path}, found {len(found_files)} files to consider.")
    return found_files

# Need to import json for metadataJson
import json
