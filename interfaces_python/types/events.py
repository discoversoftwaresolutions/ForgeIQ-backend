# interfaces_python/types/events.py
from typing import TypedDict, List, Optional, Dict, Any

class TestResult(TypedDict):
    test_name: str
    status: str # 'passed', 'failed', 'skipped'
    message: Optional[str]
    stack_trace: Optional[str]

class TestFailedEvent(TypedDict):
    event_type: str # "TestFailedEvent"
    project_id: str
    commit_sha: str
    failed_tests: List[TestResult]
    full_log_path: Optional[str]
    timestamp: str

class CodeNavSearchQuery(TypedDict):
    query_type: str # "CodeNavSearchQuery"
    query_text: str
    project_id: str
    limit: Optional[int]
    filters: Optional[Dict[str, Any]] # e.g., {"file_extension": ".py"}

class CodeNavSearchResultItem(TypedDict):
    file_path: str
    snippet: str # or code_chunk
    score: float
    metadata: Optional[Dict[str, Any]]

class CodeNavSearchResults(TypedDict):
    event_type: str # "CodeNavSearchResults"
    query_id: str # Correlate with query
    results: List[CodeNavSearchResultItem]

class PatchSuggestion(TypedDict):
    file_path: str
    start_line: int
    end_line: int
    suggested_code: str
    confidence: Optional[float]
    reasoning: Optional[str]

class PatchSuggestedEvent(TypedDict):
    event_type: str # "PatchSuggestedEvent"
    project_id: str
    commit_sha: str
    related_test_failure_id: Optional[str]
    suggestions: List[PatchSuggestion]
    timestamp: str
# ... (existing TypedDicts) ...

class PipelineGenerationRequest(TypedDict):
    event_type: str  # "PipelineGenerationRequest"
    request_id: str  # Unique ID for this request
    user_prompt: str # Natural language prompt describing the pipeline
    project_id: Optional[str]
    context: Optional[Dict[str, Any]] # Any additional context for the LLM
    timestamp: str

class DagNode(TypedDict):
    id: str            # Unique ID for the node/task within the DAG
    task_type: str     # e.g., 'lint', 'test', 'build', 'deploy', 'custom_script'
    command: Optional[List[str]] # Actual command for task-runner if simple
    agent_handler: Optional[str] # Which agent should handle this node if not a simple command
    params: Optional[Dict[str, Any]]
    dependencies: List[str] # List of other node IDs this node depends on

class DagDefinition(TypedDict):
    dag_id: str
    project_id: Optional[str]
    nodes: List[DagNode]
    # Could add metadata like description, trigger conditions, etc.

class DagDefinitionCreatedEvent(TypedDict):
    event_type: str  # "DagDefinitionCreatedEvent"
    request_id: str  # Corresponds to the PipelineGenerationRequest
    project_id: Optional[str]
    dag: DagDefinition
    raw_llm_response: Optional[str] # For audit/debugging
    timestamp: str
