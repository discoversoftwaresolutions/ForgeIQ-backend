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
