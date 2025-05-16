# agents/PatchAgent/app/agent.py
import os
import time
import json
import datetime
from core_python.event_bus.redis_bus import EventBus
from interfaces_python.types import TestFailedEvent, CodeNavSearchQuery, PatchSuggestedEvent, PatchSuggestion # Assuming types
import logging
# import httpx # For making API calls to CodeNavAgent if it's an API service

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger(__name__)

# Assume CodeNavAgent runs as an API service for simplicity here
CODE_NAV_AGENT_API_URL = os.getenv("CODE_NAV_AGENT_API_URL") # e.g., http://codenav-agent-railway-url/search

class PatchAgent:
    def __init__(self):
        self.event_bus = EventBus()
        logger.info("PatchAgent Initialized")

    async def query_codenav(self, project_id: str, query_text: str) -> list:
        if not CODE_NAV_AGENT_API_URL:
            logger.error("CODE_NAV_AGENT_API_URL not configured.")
            return []

        # This part would be async if using httpx properly
        # For now, synchronous placeholder for concept
        # payload = CodeNavSearchQuery(query_type="CodeNavSearchQuery", project_id=project_id, query_text=query_text, limit=3)
        # async with httpx.AsyncClient() as client:
        #     try:
        #         response = await client.post(CODE_NAV_AGENT_API_URL, json=payload)
        #         response.raise_for_status()
        #         search_results = response.json() # Assuming it returns CodeNavSearchResults compatible dict
        #         logger.info(f"CodeNav returned {len(search_results.get('results',[]))} results for query: {query_text}")
        #         return search_results.get('results',[])
        #     except Exception as e:
        #         logger.error(f"Error querying CodeNavAgent: {e}")
        #         return []
        logger.info(f"Simulating CodeNav query for '{query_text}' in project '{project_id}'")
        # Placeholder results
        return [
            {"file_path": "src/moduleA.py", "snippet": "def func1():\n  pass", "score": 0.9, "metadata":{}},
            {"file_path": "tests/test_moduleA.py", "snippet": "assert func1() is None", "score": 0.8, "metadata":{}}
        ]


    def handle_test_failure(self, event_data: TestFailedEvent):
        logger.info(f"PatchAgent received TestFailedEvent for project {event_data['project_id']}")

        # In a real scenario, this would be async if query_codenav is async
        # asyncio.run(self._process_failure_async(event_data))
        self._process_failure_sync(event_data)


    def _process_failure_sync(self, event_data: TestFailedEvent):
        for failed_test in event_data['failed_tests']:
            logger.info(f"Processing failed test: {failed_test['test_name']}")
            error_snippet = failed_test.get('message', '') or failed_test.get('stack_trace', '')
            if not error_snippet:
                continue

            # Query CodeNavAgent (sync simulation)
            relevant_code_snippets = self.query_codenav(event_data['project_id'], error_snippet[:200]) # Use a snippet of error

            if not relevant_code_snippets:
                logger.info("No relevant code snippets found by CodeNav.")
                continue

            # Formulate patch suggestions (very simplistic placeholder)
            suggestions: List[PatchSuggestion] = []
            for snippet in relevant_code_snippets:
                logger.info(f"Considering snippet: {snippet['file_path']}")
                # TODO: Add actual patch generation logic here (e.g., using LLM with context)
                suggestions.append(PatchSuggestion(
                    file_path=snippet['file_path'],
                    start_line=snippet.get('metadata',{}).get('startLine', 1), # Example
                    end_line=snippet.get('metadata',{}).get('endLine', 1) + 5, # Example
                    suggested_code="# TODO: Implement suggested fix based on error and this snippet\n" + snippet['snippet'],
                    confidence=0.7,
                    reasoning=f"Related to error: {error_snippet[:50]}..."
                ))

            if suggestions and self.event_bus.redis_client:
                patch_event = PatchSuggestedEvent(
                    event_type="PatchSuggestedEvent",
                    project_id=event_data['project_id'],
                    commit_sha=event_data['commit_sha'],
                    related_test_failure_id=event_data.get('id', str(uuid.uuid4())), # Assuming TestFailedEvent has an ID
                    suggestions=suggestions,
                    timestamp=datetime.datetime.utcnow().isoformat()
                )
                self.event_bus.publish(channel=f"project.{event_data['project_id']}.patch_suggestions", event_data=patch_event)
                logger.info(f"Published PatchSuggestedEvent for project {event_data['project_id']}")


    def listen_for_events(self):
        if not self.event_bus.redis_client:
            logger.error("PatchAgent: Cannot listen for events, Redis client not connected.")
            return

        # For simplicity, subscribe to a wildcard or specific project channels
        # In production, might use a more robust consumer group pattern with Redis Streams.
        channel_pattern = "project.*.test_failures" # Listen to all test failures
        pubsub = self.event_bus.redis_client.pubsub()
        pubsub.psubscribe(channel_pattern)
        logger.info(f"PatchAgent subscribed to event pattern: {channel_pattern}")

        for message in pubsub.listen():
            if message["type"] == "pmessage": # pmessage for pattern subscriptions
                try:
                    event_data = json.loads(message["data"])
                    if event_data.get("event_type") == "TestFailedEvent":
                        self.handle_test_failure(event_data) # Type check would be good here
                except json.JSONDecodeError:
                    logger.error(f"Could not decode JSON from event: {message['data']}")
                except Exception as e:
                    logger.error(f"Error handling event: {e}")

if __name__ == "__main__":
    # Requires async setup if using async httpx for CodeNav queries
    # For now, assuming synchronous operation or simulation
    agent = PatchAgent()
    agent.listen_for_events()
