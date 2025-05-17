# ===================================================
# 📁 tests/agent-unit/test_dependency_agent.py
# ===================================================
import pytest # Standard library for testing, ensure it's in requirements.txt
from unittest.mock import patch, MagicMock # Standard library for mocking

# Assuming your project structure and PYTHONPATH allow this import
# You might need to adjust relative paths or ensure 'agents' is a top-level package.
# If running pytest from root, and PYTHONPATH includes root:
from agents.DependencyAgent.app.agent import DependencyAgent 
# Also need to mock or use the actual core.build_graph functions
# For a unit test, we should mock external dependencies like core.build_graph

@pytest.fixture
def dependency_agent_instance():
    """Fixture to create a DependencyAgent instance with mocked EventBus."""
    # Mock EventBus so we don't need a live Redis for this unit test
    mock_event_bus = MagicMock()
    mock_event_bus.redis_client = MagicMock() # Simulate a connected client

    # Patch the EventBus import within the agent's module scope
    with patch('agents.DependencyAgent.app.agent.EventBus', return_value=mock_event_bus):
        agent = DependencyAgent()
    return agent

# Test cases for the _determine_affected_tasks method
# We need to mock 'core.build_graph.detect_changed_tasks' and 'core.build_graph.get_project_dag'
# as these are external dependencies for this unit test.

@patch('agents.DependencyAgent.app.agent.get_project_dag') # Path to the imported function in agent.py
@patch('agents.DependencyAgent.app.agent.detect_changed_tasks') # Path to the imported function in agent.py
def test_determine_affected_tasks_with_changes(
    mock_detect_changed, mock_get_dag, dependency_agent_instance: DependencyAgent
):
    project_id = "project_alpha"
    changed_files = ["src/main.py", "tests/test_main.py"]

    mock_detect_changed.return_value = ["lint", "test", "build_app"]

    affected = dependency_agent_instance._determine_affected_tasks(project_id, changed_files)

    mock_detect_changed.assert_called_once_with(project=project_id, changed_files=changed_files)
    mock_get_dag.assert_not_called() # Should not be called if changed_files is not empty
    assert affected == ["lint", "test", "build_app"]

@patch('agents.DependencyAgent.app.agent.get_project_dag')
@patch('agents.DependencyAgent.app.agent.detect_changed_tasks')
def test_determine_affected_tasks_no_changes_uses_full_dag(
    mock_detect_changed, mock_get_dag, dependency_agent_instance: DependencyAgent
):
    project_id = "project_beta"
    changed_files = [] # No changed files

    mock_get_dag.return_value = ["lint_all", "test_all", "build_all", "deploy_all"]

    affected = dependency_agent_instance._determine_affected_tasks(project_id, changed_files)

    mock_get_dag.assert_called_once_with(project_id)
    mock_detect_changed.assert_not_called() # Should not be called if changed_files is empty
    assert affected == ["lint_all", "test_all", "build_all", "deploy_all"]

@patch('agents.DependencyAgent.app.agent.get_project_dag')
@patch('agents.DependencyAgent.app.agent.detect_changed_tasks')
def test_determine_affected_tasks_detect_returns_empty(
    mock_detect_changed, mock_get_dag, dependency_agent_instance: DependencyAgent
):
    project_id = "project_gamma"
    changed_files = ["README.md"] # A file that might lead to few tasks

    mock_detect_changed.return_value = ["lint_readme"] # Example: only one task affected

    affected = dependency_agent_instance._determine_affected_tasks(project_id, changed_files)

    mock_detect_changed.assert_called_once_with(project=project_id, changed_files=changed_files)
    assert affected == ["lint_readme"]

# Add more unit tests for other methods in DependencyAgent as needed.
# For async methods in the agent, you would use pytest-asyncio:
# @pytest.mark.asyncio
# async def test_async_method(dependency_agent_instance: DependencyAgent):
#     result = await dependency_agent_instance.some_async_method()
#     assert result == "expected"
