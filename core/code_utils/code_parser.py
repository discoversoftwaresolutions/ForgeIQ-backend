# ==========================================
# 📁 core/code_utils/code_parser.py (V0.2)
# ==========================================
import os
import hashlib
import json # For metadataJson
import re # For basic function/class splitting
from typing import List, Dict, Any, Optional, TypedDict
import logging

logger = logging.getLogger(__name__)

class CodeChunk(TypedDict):
    content: str
    start_line: int
    end_line: int
    content_hash: str
    metadata_json: str # JSON string of additional metadata (e.g., function/class name)

# Max lines for a chunk if no structural pattern matches, or as a fallback
MAX_LINES_PER_CHUNK_GENERIC = 150
OVERLAP_LINES_GENERIC = 20

# Basic patterns to identify start of functions/classes for Python
# This is very rudimentary. Tree-sitter would be much more robust.
PYTHON_STRUCTURE_PATTERNS = [
    re.compile(r"^\s*async def\s+(\w+)\s*\((.*?)\):"),
    re.compile(r"^\s*def\s+(\w+)\s*\((.*?)\):"),
    re.compile(r"^\s*class\s+(\w+)\s*[\(:]"),
]
# Max lines for a recognized Python function/class chunk
MAX_LINES_PER_STRUCTURED_CHUNK_PYTHON = 300
def scan_code_directory(path: str) -> list: return []
def get_language_from_extension(file_path: str) -> Optional[str]: return None

def get_language_from_extension(file_path: str) -> Optional[str]:
    # (Same as previously defined in CodeNavAgent's parser)
    _, ext = os.path.splitext(file_path)
    lang_map = {
        ".py": "python", ".js": "javascript", ".ts": "typescript", ".java": "java",
        ".go": "go", ".rs": "rust", ".c": "c", ".cpp": "cpp", ".h": "c", ".hpp": "cpp",
        ".md": "markdown", ".txt": "text", ".json": "json", ".yaml": "yaml", ".yml": "yaml"
        # Add more mappings as needed
    }
    return lang_map.get(ext.lower())

def _chunk_python_content(content: str, file_path: Optional[str]) -> List[CodeChunk]:
    """Attempts to chunk Python code by functions and classes."""
    chunks: List[CodeChunk] = []
    lines = content.splitlines()
    n_lines = len(lines)
    current_line_idx = 0

    # Store (start_index, end_index, name, type) for found structures
    structures: List[Tuple[int, int, str, str]] = []

    # First pass: identify structures
    temp_lines = list(enumerate(lines)) # (idx, line_content)

    idx = 0
    while idx < n_lines:
        line_content = lines[idx]
        found_structure = False
        for pattern_idx, pattern in enumerate(PYTHON_STRUCTURE_PATTERNS):
            match = pattern.match(line_content)
            if match:
                structure_name = match.group(1) if match.groups() and len(match.groups()) > 0 else "anonymous_structure"
                # Naive way to find end of structure: look for next unindented line or pattern
                # This is very basic and will break with nested structures or complex formatting.
                # A proper AST parser (like tree-sitter) is needed for robustness.
                structure_start_line_idx = idx
                indentation = len(line_content) - len(line_content.lstrip())

                structure_end_line_idx = idx + 1
                while structure_end_line_idx < n_lines:
                    next_line_content = lines[structure_end_line_idx]
                    next_indentation = len(next_line_content) - len(next_line_content.lstrip())
                    if next_line_content.strip() and next_indentation <= indentation: # Dedent or same level
                        # Also check if it's another structure definition at same level
                        is_another_structure_at_same_level = False
                        for p_inner in PYTHON_STRUCTURE_PATTERNS:
                            if p_inner.match(next_line_content) and next_indentation == indentation:
                                is_another_structure_at_same_level = True
                                break
                        if is_another_structure_at_same_level:
                            break # End current structure here
                        # If just dedented text, it might still be part of multiline docstring or comment
                        # This simplistic parser doesn't handle that well.

                    # If structure gets too long, break it.
                    if (structure_end_line_idx - structure_start_line_idx) >= MAX_LINES_PER_STRUCTURED_CHUNK_PYTHON:
                        break
                    structure_end_line_idx += 1

                # Ensure structure_end_line_idx doesn't go past the actual end if it's the last structure
                structure_end_line_idx = min(structure_end_line_idx, n_lines)

                structure_type = "function" if "def" in line_content else "class"
                if "interface" in line_content: structure_type = "interface" # For other languages conceptually

                structures.append((structure_start_line_idx, structure_end_line_idx -1, structure_name, structure_type))
                idx = structure_end_line_idx -1 # Continue scan after this structure
                found_structure = True
                break # Found a pattern for this line
        idx += 1
        if not found_structure and current_line_idx == idx-1 : # current_line_idx wasn't updated by finding a structure
            current_line_idx = idx # advance current_line_idx if no structure was found starting at old current_line_idx

    # Create chunks from identified structures
    last_chunk_end_idx = -1
    for start_idx, end_idx, name, struct_type in sorted(structures, key=lambda x: x[0]):
        # Add unchunked content before this structure if there's a gap
        if start_idx > last_chunk_end_idx + 1:
            # This recursive call is problematic, can lead to infinite loop or deep recursion.
            # Instead, process gaps as generic chunks.
            gap_content_lines = lines[last_chunk_end_idx+1 : start_idx]
            if gap_content_lines:
                gap_content = "\n".join(gap_content_lines)
                content_hash = hashlib.sha256(gap_content.encode('utf-8')).hexdigest()
                chunks.append(CodeChunk(
                    content=gap_content, 
                    start_line=last_chunk_end_idx + 2, # 1-indexed
                    end_line=start_idx, # 1-indexed
                    content_hash=content_hash,
                    metadata_json=json.dumps({"type": "interstitial_code", "file": file_path or ""})
                ))

        chunk_content_lines = lines[start_idx : end_idx + 1]
        chunk_content = "\n".join(chunk_content_lines)
        content_hash = hashlib.sha256(chunk_content.encode('utf-8')).hexdigest()
        chunks.append(CodeChunk(
            content=chunk_content,
            start_line=start_idx + 1, # 1-indexed
            end_line=end_idx + 1,   # 1-indexed
            content_hash=content_hash,
            metadata_json=json.dumps({"type": struct_type, "name": name, "file": file_path or ""})
        ))
        last_chunk_end_idx = end_idx

    # Add any remaining content at the end of the file as a generic chunk
    if last_chunk_end_idx < n_lines - 1:
        remaining_content_lines = lines[last_chunk_end_idx+1:]
        if remaining_content_lines:
            remaining_content = "\n".join(remaining_content_lines)
            content_hash = hashlib.sha256(remaining_content.encode('utf-8')).hexdigest()
            chunks.append(CodeChunk(
                content=remaining_content,
                start_line=last_chunk_end_idx + 2, # 1-indexed
                end_line=n_lines, # 1-indexed
                content_hash=content_hash,
                metadata_json=json.dumps({"type": "trailing_code", "file": file_path or ""})
            ))

    if not chunks and content: # If no structures found, treat whole file as one chunk (or use sliding window)
         logger.debug(f"No structures found in {file_path or 'content'}, using generic chunking.")
         return _chunk_generic_content(content, file_path) # Fallback to generic

    logger.debug(f"Python-specific chunking for {file_path or 'content'} yielded {len(chunks)} chunks.")
    return chunks


def _chunk_generic_content(content: str, file_path: Optional[str]) -> List[CodeChunk]:
    """Generic chunking using a sliding window approach."""
    chunks: List[CodeChunk] = []
    lines = content.splitlines()
    n_lines = len(lines)
    if not lines:
        return []

    current_line_idx = 0
    while current_line_idx < n_lines:
        start_line_num = current_line_idx + 1 # 1-indexed for metadata
        end_line_idx = min(current_line_idx + MAX_LINES_PER_CHUNK_GENERIC, n_lines)

        chunk_content_lines = lines[current_line_idx:end_line_idx]
        chunk_content = "\n".join(chunk_content_lines)
        content_hash = hashlib.sha256(chunk_content.encode('utf-8')).hexdigest()

        chunks.append(CodeChunk(
            content=chunk_content,
            start_line=start_line_num,
            end_line=end_line_idx, # This is 0-indexed end + 1, so correct for 1-indexed end line number
            content_hash=content_hash,
            metadata_json=json.dumps({"type": "generic_text_chunk", "file": file_path or ""})
        ))

        if end_line_idx == n_lines: # Reached end of file
            break

        # Move window with overlap, ensuring we don't create tiny last chunk if possible
        advance_by = MAX_LINES_PER_CHUNK_GENERIC - OVERLAP_LINES_GENERIC
        if current_line_idx + advance_by >= n_lines - (MAX_LINES_PER_CHUNK_GENERIC // 2) and advance_by < n_lines - current_line_idx : # Avoid very small last chunk
             current_line_idx = n_lines # Force end if next chunk would be too small (less than half)
        else:
             current_line_idx += advance_by

    logger.debug(f"Generic chunking for {file_path or 'content'} yielded {len(chunks)} chunks.")
    return chunks


def chunk_code_content(content: str, language: Optional[str] = None, file_path: Optional[str] = None) -> List[CodeChunk]:
    """
    Main function to chunk code content. Uses language-specific chunkers if available.
    """
    if not content.strip():
        return []

    if language == "python":
        # Try Python specific chunking, if it fails or returns no chunks, fallback to generic
        py_chunks = _chunk_python_content(content, file_path)
        if py_chunks: # If Python chunker produced something meaningful
            return py_chunks
        else: # Fallback if Python chunker failed or found nothing on non-empty content
            logger.warning(f"Python-specific chunking failed or yielded no chunks for {file_path or 'content'}. Falling back to generic chunking.")
            return _chunk_generic_content(content, file_path)
    else:
        # For other languages or if language is None, use generic chunking
        return _chunk_generic_content(content, file_path)


def scan_code_directory(dir_path: str, 
                        ignore_dirs: Optional[List[str]] = None,
                        ignore_file_patterns: Optional[List[str]] = None, # e.g., ["*.min.js", "package-lock.json"]
                        allowed_extensions: Optional[List[str]] = None # e.g., [".py", ".js", ".md"]
                       ) -> List[str]:
    """
    Scans a directory for files, respecting ignore patterns and allowed extensions.
    More robust than the V0.1 version.
    """
    if ignore_dirs is None:
        ignore_dirs = ['.git', 'node_modules', '__pycache__', 'dist', 'build', '.venv', 'venv', '.vscode', '.idea']

    # For simplistic pattern matching, not full glob or regex yet
    if ignore_file_patterns is None:
        ignore_file_patterns = ["*.log", "*.tmp", "*.swp", "*.bak"] 

    found_files: List[str] = []
    for root, dirs, files in os.walk(dir_path, topdown=True):
        # Filter out ignored directories by modifying dirs list in place
        dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith('.')] # Also ignore hidden dirs

        for file_name in files:
            if file_name.startswith('.'): # Ignore hidden files
                continue

            # Check against ignore file patterns (basic endswith for now)
            if any(file_name.endswith(pattern.replace("*","")) for pattern in ignore_file_patterns if pattern.startswith("*")): # Basic wildcard end
                continue
            if file_name in ignore_file_patterns: # Exact match
                continue

            file_path = os.path.join(root, file_name)

            if allowed_extensions:
                if not any(file_path.endswith(ext) for ext in allowed_extensions):
                    continue # Skip if not in allowed extensions

            # Avoid re-adding if symlinks create cycles, though os.walk can be configured for this
            # For now, just add if it's a file
            if os.path.isfile(file_path): # Ensure it's a file, not a broken symlink etc.
                found_files.append(file_path)

    logger.info(f"Scanned directory '{dir_path}', found {len(found_files)} files matching criteria.")
    return found_files
