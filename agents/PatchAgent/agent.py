# agents/PatchAgent/agent.py
import os
import time
import json
import datetime
import uuid
import httpx # For async HTTP requests
import asyncio
import logging

# --- Observability Setup ---
SERVICE_NAME_PATCHAGENT = "PatchAgent"
LOG_LEVEL_PATCHAGENT = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL_PATCHAGENT,
    format=f'%(asctime)s - {SERVICE_NAME_PATCHAGENT} - %(name)s - %(levelname)s - %(message)s'
)
tracer_patchagent = None
try:
    from core.observability.tracing import setup_tracing
    tracer_patchagent = setup_tracing(SERVICE_NAME_PATCHAGENT)
except ImportError:
    logging.getLogger(SERVICE_NAME_PATCHAGENT).warning(
        "PatchAgent: Tracing setup failed."
    )
logger_patchagent = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary
from interfaces.types.events import (
    TestFailedEvent, CodeNavSearchQuery, CodeNavSearchResults, 
    PatchSuggestion, PatchSuggestedEvent, CodeNavSearchResultItem # Make sure these are complete
)

CODE_NAV_AGENT_API_URL = os.getenv("CODE_NAV_AGENT_API_URL") # e.g., http://codenav-agent:8001/search
# OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") # If using OpenAI for patch generation
EVENT_BUS_TEST_FAILURE_CHANNEL_PATTERN = "events.project.*.test.failures"
EVENT_BUS_PATCH_SUGGESTION_CHANNEL_TEMPLATE = "events.project.{project_id}.patch.suggestions"


class PatchAgent:
    def __init__(self):
        logger_patchagent.info("Initializing PatchAgent...")
        self.event_bus = EventBus()
        self.http_client = httpx.AsyncClient(timeout=60.0) # Longer timeout for CodeNav searches

        if not self.event_bus.redis_client:
            logger_patchagent.error("PatchAgent critical: EventBus not connected.")
        if not CODE_NAV_AGENT_API_URL:
            logger_patchagent.warning("CODE_NAV_AGENT_API_URL not set. Cannot query CodeNav.")
        logger_patchagent.info("PatchAgent Initialized.")

    @property
    def tracer(self):
        return tracer_patchagent

    async def query_codenav_for_context(self, project_id: str, query_text: str, commit_sha: str) -> List[CodeNavSearchResultItem]:
        if not CODE_NAV_AGENT_API_URL:
            logger_patchagent.error("Cannot query CodeNavAgent: API URL not configured.")
            return []

        span_name = "query_codenav_agent"
        current_span_context = None
        if self.tracer:
            current_span = trace.get_current_span()
            # Start a new child span for the HTTP call
            child_span = self.tracer.start_span(span_name, context=trace.set_span_in_context(current_span))
            current_span_context = trace.set_span_in_context(child_span)

        # Payload for CodeNavAgent's /search endpoint (matches ApiSearchRequest Pydantic model)
        payload = {
            "project_id": project_id,
            "query_text": query_text,
            "limit": 5, # Request a few relevant snippets
            # "filters": {"language": "python"} # Example filter if needed
        }

        results = []
        try:
            logger_patchagent.info(f"Querying CodeNavAgent at {CODE_NAV_AGENT_API_URL} for project {project_id}")
            logger_patchagent.debug(f"CodeNav payload: {payload}")

            # OTel httpx instrumentation should automatically create a client span if http_client was instrumented
            response = await self.http_client.post(CODE_NAV_AGENT_API_URL, json=payload)
            response.raise_for_status()

            data = response.json() # Expects {"results": List[ApiCodeSnippet-like-dict]}
            raw_results = data.get("results", [])

            # Convert/validate to CodeNavSearchResultItem TypedDict
            for item_data in raw_results:
                if isinstance(item_data, dict) and "file_path" in item_data and "snippet" in item_data:
                    results.append(CodeNavSearchResultItem(
                        file_path=item_data.get("file_path",""),
                        snippet=item_data.get("snippet",""),
                        score=item_data.get("score",0.0),
                        metadata=item_data.get("metadata",{})
                        # Ensure all fields from CodeNavSearchResultItem are mapped
                    ))
            logger_patchagent.info(f"CodeNavAgent returned {len(results)} snippets for commit {commit_sha}")
            if self.tracer and child_span: child_span.set_attribute("codenav.results_count", len(results))

        except httpx.RequestError as e:
            logger_patchagent.error(f"HTTP RequestError querying CodeNavAgent: {e.request.method} {e.request.url} - {e}")
            if self.tracer and child_span: child_span.record_exception(e); child_span.set_status(trace.Status(trace.StatusCode.ERROR))
        except httpx.HTTPStatusError as e:
            logger_patchagent.error(f"HTTP HTTPStatusError querying CodeNavAgent: {e.response.status_code} - {e.response.text}")
            if self.tracer and child_span: child_span.record_exception(e); child_span.set_status(trace.Status(trace.StatusCode.ERROR))
        except Exception as e:
            logger_patchagent.error(f"Unexpected error querying CodeNavAgent: {e}", exc_info=True)
            if self.tracer and child_span: child_span.record_exception(e); child_span.set_status(trace.Status(trace.StatusCode.ERROR))
        finally:
            if self.tracer and child_span: child_span.end()

        return results


    async def generate_actual_patches(self, test_failure_context: TestFailedEvent, code_nav_snippets: List[CodeNavSearchResultItem]) -> List[PatchSuggestion]:
        """
        Core logic for generating patches.
        This is where LLM calls or advanced heuristics would go.
        For robust V0.1, it will generate more structured "TODO" patches.
        """
        span_name = "generate_patch_suggestions"
        if not self.tracer: # Fallback
             return self._generate_actual_patches_logic(test_failure_context, code_nav_snippets)

        with self.tracer.start_as_current_span(span_name) as span:
            span.set_attribute("patch_agent.num_codenav_snippets", len(code_nav_snippets))
            span.set_attribute("patch_agent.num_failed_tests", len(test_failure_context.get("failed_tests", [])))

            # Call the actual logic
            suggestions = self._generate_actual_patches_logic(test_failure_context, code_nav_snippets)

            span.set_attribute("patch_agent.num_suggestions", len(suggestions))
            return suggestions

    def _generate_actual_patches_logic(self, test_failure_context: TestFailedEvent, code_nav_snippets: List[CodeNavSearchResultItem]) -> List[PatchSuggestion]:
        logger_patchagent.info(f"Formulating patch suggestions for project {test_failure_context['project_id']}")
        suggestions: List[PatchSuggestion] = []

        # Combine context from all failed tests and CodeNav snippets
        # This is a very high-level placeholder for the complex patch generation.
        # A production system would involve much more sophisticated analysis.

        # Context for LLM (if used):
        # llm_context = "Context:\n"
        # for test_res in test_failure_context['failed_tests']:
        #     llm_context += f"Failed Test: {test_res['test_name']}\nError: {test_res['message']}\nStack: {test_res['stack_trace']}\n\n"
        # for i, snippet in enumerate(code_nav_snippets):
        #     llm_context += f"Relevant Code Snippet {i+1} from {snippet['file_path']}:\n{snippet['snippet']}\n\n"
        # llm_context += "Suggest a fix for the primary error based on the context above."
        # logger_patchagent.debug(f"LLM Context (first 500 chars): {llm_context[:500]}")
        # generated_patch_text = call_llm_service(llm_context) # Placeholder for actual LLM call

        # For now, create more structured "TODO" suggestions based on CodeNav snippets
        if not code_nav_snippets and test_failure_context['failed_tests']:
            # If no CodeNav results, try to pinpoint from stack trace (very naive)
            test_res = test_failure_context['failed_tests'][0] # Take first failure
            # ... (naive stack trace parsing as in previous PatchAgent version) ...
            # This part needs to be robust
            file_path_from_stack = "unknown_file.py" # Placeholder
            line_from_stack = 1 # Placeholder
            suggestions.append(PatchSuggestion(
                file_path=file_path_from_stack, start_line=line_from_stack, end_line=line_from_stack,
                suggested_code=f"# TODO: Fix error from test: {test_res['test_name']}. Error: {test_res['message']}",
                confidence=0.1, reasoning="Patch based on direct test failure, no CodeNav context."
            ))

        for snippet in code_nav_snippets: # Iterate through CodeNav results
            # This logic needs to be much smarter. E.g., map test failure details to specific snippets.
            reason = "CodeNav identified this snippet as relevant to the test failures."
            if test_failure_context['failed_tests']:
                reason += f" Primary error: {test_failure_context['failed_tests'][0]['message']}"

            suggestions.append(PatchSuggestion(
                file_path=snippet['file_path'],
                # Using metadata if CodeNav provides it, otherwise default
                start_line=snippet.get('metadata',{}).get('startLine', 1),
                end_line=snippet.get('metadata',{}).get('endLine', snippet.get('metadata',{}).get('startLine', 1) + len(snippet['snippet'].splitlines())),
                suggested_code=(
                    f"# PATCH SUGGESTION (PatchAgent V0.1)\n"
                    f"# Issue context: Test failure related. Relevant code identified by CodeNav.\n"
                    f"# Original snippet from {snippet['file_path']}:\n"
                    f"# {snippet['snippet'].replacechr(10), '# ')}\n" # Comment out original snippet
                    f"# TODO: Implement intelligent patch based on failure context and this snippet."
                ),
                confidence= snippet.get('score', 0.5) * 0.5, # Combine CodeNav score with agent confidence
                reasoning=reason
            ))
            if len(suggestions) >= 2: break # Limit suggestions for now

        logger_patchagent.info(f"Formulated {len(suggestions)} patch suggestions for project {test_failure_context['project_id']}.")
        return suggestions


    async def handle_test_failure_event(self, event_data_str: str):
        span_name = "handle_test_failure_event"
        parent_span_context = None # If event carries trace context, extract it here

        if not self.tracer: # Fallback
            await self._handle_test_failure_event_logic(event_data_str)
            return

        # Start a new span linked to the event if possible, or as a new trace
        with self.tracer.start_as_current_span(span_name, context=parent_span_context) as span:
            try:
                event_data: TestFailedEvent = json.loads(event_data_str) # Parse JSON string
                # Basic validation, Pydantic would be better for production
                if not (event_data.get("event_type") == "TestFailedEvent" and 
                        "project_id" in event_data and 
                        "commit_sha" in event_data and
                        "failed_tests" in event_data):
                    logger_patchagent.error(f"Received malformed or unexpected event: {event_data_str[:200]}")
                    span.set_attribute("patch_agent.event_error", "malformed_event")
                    span.set_status(trace.Status(trace.StatusCode.ERROR, "Malformed event data"))
                    return

                span.set_attributes({
                    "messaging.system": "redis",
                    "messaging.operation": "process",
                    "messaging.message.payload_size_bytes": len(event_data_str),
                    "patch_agent.project_id": event_data["project_id"],
                    "patch_agent.commit_sha": event_data["commit_sha"],
                })
                logger_patchagent.info(f"PatchAgent handling TestFailedEvent for project {event_data['project_id']}")
                await self._handle_test_failure_event_logic(event_data) # Pass parsed dict

            except json.JSONDecodeError:
                logger_patchagent.error(f"Could not decode JSON from event: {event_data_str[:200]}")
                span.set_attribute("patch_agent.event_error", "json_decode_error")
                span.set_status(trace.Status(trace.StatusCode.ERROR, "JSON decode error"))
            except Exception as e:
                logger_patchagent.error(f"Unexpected error in handle_test_failure_event: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, "Core event handling failed"))


    async def _handle_test_failure_event_logic(self, event_data: TestFailedEvent):
        # (Logic from previous PatchAgent's handle_test_failure_event, now made async and using await)
        query_text_parts = []
        for test_res in event_data['failed_tests']:
            query_text_parts.append(test_res['test_name'])
            if test_res.get('message'): query_text_parts.append(test_res['message'])

        if not query_text_parts: logger_patchagent.warning("No query text from TestFailedEvent."); return
        combined_query_text = " ".join(query_text_parts)

        relevant_code_snippets = await self.query_codenav_for_context(
            event_data['project_id'], 
            combined_query_text,
            event_data['commit_sha'] # Pass commit_sha for context if needed
        )

        if not relevant_code_snippets:
            logger_patchagent.info("CodeNavAgent found no relevant snippets. Cannot formulate patches.")
            return

        patch_suggestions = await self.generate_actual_patches(event_data, relevant_code_snippets)

        if patch_suggestions and self.event_bus.redis_client:
            # Assuming TestFailedEvent might have an 'id' field, if not generate one
            # For TypedDict, ensure 'id' is optional or always present in TestFailedEvent
            related_failure_id = event_data.get("id") # This key might not exist in your current TypedDict
            if not related_failure_id: related_failure_id = str(uuid.uuid4()) # Fallback if no ID

            patch_event = PatchSuggestedEvent(
                event_type="PatchSuggestedEvent",
                project_id=event_data['project_id'],
                commit_sha=event_data['commit_sha'],
                related_test_failure_id=related_failure_id, 
                suggestions=patch_suggestions,
                timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
            )
            channel = EVENT_BUS_PATCH_SUGGESTION_CHANNEL_TEMPLATE.format(project_id=event_data['project_id'])
            self.event_bus.publish(channel=channel, event_data=patch_event)
            trace.get_current_span().add_event("Published PatchSuggestedEvent", {"num_suggestions": len(patch_suggestions), "channel": channel})
        elif not patch_suggestions:
            logger_patchagent.info("No patch suggestions were formulated.")


    async def main_event_loop(self):
        if not self.event_bus.redis_client:
            logger_patchagent.critical("PatchAgent: Cannot start main event loop, Redis client not connected. Exiting.")
            return

        pubsub = self.event_bus.subscribe_to_channel(EVENT_BUS_TEST_FAILURE_CHANNEL_PATTERN)
        if not pubsub:
            logger_patchagent.critical(f"PatchAgent: Failed to subscribe to {EVENT_BUS_TEST_FAILURE_CHANNEL_PATTERN}. Worker cannot start.")
            return

        logger_patchagent.info(f"PatchAgent worker subscribed to {EVENT_BUS_TEST_FAILURE_CHANNEL_PATTERN}, listening for events...")
        try:
            while True:
                # Use asyncio.to_thread for the blocking pubsub.get_message call
                message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "pmessage": # pmessage for pattern subscriptions
                    await self.handle_test_failure_event(message["data"]) # Pass the JSON string
                # Add a small sleep to yield control and prevent tight loop if no messages immediately
                await asyncio.sleep(0.01) 
        except KeyboardInterrupt:
            logger_patchagent.info("PatchAgent event loop interrupted.")
        except Exception as e:
            logger_patchagent.error(f"Critical error in PatchAgent event loop: {e}", exc_info=True)
        finally:
            logger_patchagent.info("PatchAgent shutting down pubsub and HTTP client...")
            if pubsub:
                try:
                    # Unsubscribe might also need to be run in a thread if it's blocking
                    await asyncio.to_thread(pubsub.punsubscribe, EVENT_BUS_TEST_FAILURE_CHANNEL_PATTERN)
                    await asyncio.to_thread(pubsub.close)
                except Exception as e:
                    logger_patchagent.error(f"Error closing pubsub: {e}")
            await self.http_client.aclose()
            logger_patchagent.info("PatchAgent shutdown complete.")

async def main(): # Main async entry point for the agent
    agent = PatchAgent()
    await agent.main_event_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger_patchagent.info("PatchAgent main execution stopped by user.")
    except Exception as e: # Catch-all for any other unexpected error during startup
        logger_patchagent.critical(f"PatchAgent failed to start or unhandled error in main: {e}", exc_info=True)
