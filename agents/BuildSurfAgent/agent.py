# ============================================
# 📁 agents/BuildSurfAgent/app/agent.py (V0.2 Enhancements)
# ============================================
import os
import json
import datetime
import uuid
import httpx 
import asyncio
import logging
from typing import Dict, Any, Optional, List, Union # Added Union

# --- Observability Setup (as before) ---
SERVICE_NAME = "BuildSurfAgent"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format=f'%(asctime)s - {SERVICE_NAME} - %(name)s - %(levelname)s - %(message)s')
_tracer = None; _trace_api = None
try:
    from opentelemetry import trace as otel_trace_api
    from core.observability.tracing import setup_tracing
    _tracer = setup_tracing(SERVICE_NAME)
    _trace_api = otel_trace_api
except ImportError: logging.getLogger(SERVICE_NAME).warning("BuildSurfAgent: Tracing setup failed.")
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary
from core.agent_registry import AgentRegistry # For fetching available agent handlers
from core.build_system_config import get_project_config, get_task_weight # For context
from interfaces.types.events import (
    PipelineGenerationRequestEvent, DagNode, DagDefinition, DagDefinitionCreatedEvent, PipelineGenerationUserPrompt
)
# If using Pydantic for internal validation:
# from pydantic import BaseModel, ValidationError, validator
# class PydanticDagNode(BaseModel): ...
# class PydanticDagDefinition(BaseModel): ...

LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "gpt-3.5-turbo-instruct") # Adjusted for completion if not chat
LLM_API_BASE_URL = os.getenv("LLM_API_BASE_URL")
MAX_LLM_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
LLM_RETRY_DELAY_SECONDS = int(os.getenv("LLM_RETRY_DELAY_SECONDS", "5"))

PIPELINE_REQUEST_EVENT_CHANNEL = "events.buildsurf.generate_dag.request"
DAG_CREATED_EVENT_CHANNEL_TEMPLATE = "events.project.{project_id}.dag.created"


class BuildSurfAgent:
    def __init__(self, agent_registry: Optional[AgentRegistry] = None): # Optional AgentRegistry
        logger.info(f"Initializing BuildSurfAgent (V0.2) with LLM model: {LLM_MODEL_NAME}")
        self.event_bus = EventBus()
        self.http_client = httpx.AsyncClient(timeout=180.0) # Increased timeout for potentially long LLM calls
        self.agent_registry = agent_registry # Store for fetching agent capabilities if needed for prompt

        if not self.event_bus.redis_client: logger.error("BuildSurfAgent critical: EventBus not connected.")
        if not LLM_API_KEY: logger.warning("LLM_API_KEY not set. BuildSurfAgent may not function.")

        self.llm_client = None
        if LLM_API_KEY and ("gpt" in LLM_MODEL_NAME.lower() or "openai" in (LLM_API_BASE_URL or "").lower()):
            try:
                from openai import AsyncOpenAI # Ensure 'openai' is in requirements.txt
                client_args = {"api_key": LLM_API_KEY}
                if LLM_API_BASE_URL: client_args["base_url"] = LLM_API_BASE_URL
                self.llm_client = AsyncOpenAI(**client_args)
                logger.info("OpenAI client initialized.")
            except ImportError: logger.error("OpenAI Python client not installed.")
            except Exception as e: logger.error(f"Failed to initialize OpenAI client: {e}")
        # Add similar blocks for other LLM providers (e.g., Anthropic) if needed

        logger.info("BuildSurfAgent V0.2 Initialized.")

    @property
    def tracer(self): return _tracer

    def _start_trace_span_if_available(self, operation_name: str, parent_context: Optional[Any] = None, **attrs):
        # ... (same as Orchestrator's _start_trace_span_if_available from response #61)
        if _tracer and _trace_api:
            span = _tracer.start_span(f"buildsurf_agent.{operation_name}", context=parent_context)
            for k, v in attrs.items(): span.set_attribute(k, v)
            return span
        class NoOpSpan:
            def __enter__(self): return self; 
            def __exit__(self,tp,vl,tb): pass; 
            def set_attribute(self,k,v): pass; 
            def record_exception(self,e,attributes=None): pass; 
            def set_status(self,s): pass;
            def end(self): pass
        return NoOpSpan()


    async def _get_additional_context_for_llm(self, project_id: Optional[str]) -> Dict[str, Any]:
        """Gathers additional context to help the LLM generate a relevant DAG."""
        context = {}
        if project_id:
            # Fetch project-specific config from core.build_system_config
            # Note: get_project_config was async in my V0.1 definition
            project_specific_config = await get_project_config(project_id) # Assuming get_project_config exists
            if project_specific_config:
                context["project_config_summary"] = {
                    "description": project_specific_config.get("description"),
                    "supported_tasks_hint": project_specific_config.get("supported_tasks"),
                    "default_environment": project_specific_config.get("default_environment")
                }

        # Fetch available agent types/capabilities from AgentRegistry if available
        if self.agent_registry:
            active_agents = self.agent_registry.discover_agents(status="active") # discover_agents is sync in V0.1
            agent_capabilities_summary = defaultdict(list)
            for agent_info in active_agents:
                for cap in agent_info.get("capabilities", []):
                    agent_capabilities_summary[agent_info["agent_type"]].append(cap.get("name"))
            if agent_capabilities_summary:
                context["available_agent_capabilities"] = dict(agent_capabilities_summary)

        # TODO: Potentially add examples of existing DAGs or common task patterns
        return context

    async def _construct_llm_prompt_v2(self, user_prompt_data: PipelineGenerationUserPrompt) -> str:
        project_id = user_prompt_data.get("target_project_id")
        user_prompt = user_prompt_data["prompt_text"]
        user_context = user_prompt_data.get("additional_context", {})

        # Fetch dynamic context
        dynamic_context = await self._get_additional_context_for_llm(project_id)
        full_context = {**dynamic_context, **user_context} # User context can override dynamic

        # Example DagNode TypedDict structure for LLM to follow
        # (from interfaces.types.graph)
        dag_node_example_fields = """
            - "id": str (unique task ID, e.g., "lint-python-code")
            - "task_type": str (e.g., "lint", "test", "build", "deploy", "security_scan", "code_analysis", "custom_script")
            - "command": Optional[List[str]] (e.g., ["flake8", "."])
            - "agent_handler": Optional[str] (e.g., "SecurityAgent", "TestAgent", "PlanAgent" itself if it has sub-dag logic)
            - "params": Optional[Dict[str, Any]] (e.g., {"severity_threshold": "HIGH"} for SecurityAgent)
            - "dependencies": List[str] (list of other node 'id's)
        """
        dag_json_example = """
        {
          "dag_id": "llm_generated_dag_<timestamp_or_uuid>",
          "project_id": "<project_id_if_known_else_null>",
          "description": "<A brief description of the pipeline generated from the prompt>",
          "nodes": [
            {"id": "lint_code", "task_type": "lint", "command": ["make", "lint"], "dependencies": [], "params": {"strict": true}},
            {"id": "unit_tests", "task_type": "test", "agent_handler": "TestAgent", "params": {"suite": "unit"}, "dependencies": ["lint_code"]}
          ]
        }
        """
        # Using f-string for project_id placeholder in the example
        dag_json_example = dag_json_example.replace("<project_id_if_known_else_null>", json.dumps(project_id))

        prompt = f"""
        You are an AI assistant that designs build and deployment pipeline DAGs (Directed Acyclic Graphs).
        Your goal is to translate the user's request into a valid JSON object representing this DAG.

        The root JSON object must contain:
        - "dag_id": A unique string identifier for the DAG (e.g., "dag_" followed by a short UUID).
        - "project_id": The string project identifier if provided (use "{project_id}" if applicable, otherwise null).
        - "description": A brief string describing the generated pipeline.
        - "nodes": A list of node objects, where each node represents a task in the pipeline.

        Each node object in the "nodes" list must have the following fields:
        {dag_node_example_fields}

        Available context for this request:
        {json.dumps(full_context, indent=2, default=str)}

        User's request for the pipeline:
        "{user_prompt}"

        Based on the user's request and the provided context, generate ONLY the valid JSON object representing the pipeline DAG.
        Ensure all node 'id's are unique within the DAG.
        Ensure all 'dependencies' for a node refer to 'id's of other nodes defined in the same DAG.
        Do not include any explanatory text, apologies, or markdown formatting outside the JSON object itself.
        The output must be a single, valid JSON object.
        """
        logger.debug(f"Constructed LLM prompt for project '{project_id}': {prompt[:300]}...") # Log snippet
        return prompt.strip()

    async def _call_llm_api_v2(self, prompt_text: str, request_id: str) -> Optional[str]:
        if not self.llm_client:
            logger.error(f"LLM client not available for request_id: {request_id}")
            return None

        current_span = _trace_api.get_current_span() if _trace_api else None
        if current_span:
            current_span.set_attribute("llm.request.model", LLM_MODEL_NAME)
            current_span.set_attribute("llm.prompt_length", len(prompt_text))

        for attempt in range(MAX_LLM_RETRIES):
            try:
                logger.info(f"Sending request to LLM (Attempt {attempt + 1}/{MAX_LLM_RETRIES}) for request_id: {request_id}. Model: {LLM_MODEL_NAME}")
                # Using Chat Completions for models like gpt-3.5-turbo, gpt-4 etc.
                # If using older completion models, the API call would be different.
                if isinstance(self.llm_client, AsyncOpenAI): # Check if it's OpenAI client
                    chat_completion = await self.llm_client.chat.completions.create(
                        messages=[{"role": "user", "content": prompt_text}],
                        model=LLM_MODEL_NAME,
                        temperature=0.1, # Low temperature for more deterministic/structured JSON
                        # response_format={"type": "json_object"} # For newer OpenAI models
                        max_tokens=2048 # Adjust as needed for expected DAG size
                    )
                    response_content = chat_completion.choices[0].message.content
                    if current_span: 
                        usage = chat_completion.usage
                        if usage:
                            span.set_attribute("llm.usage.total_tokens", usage.total_tokens)
                            span.set_attribute("llm.usage.prompt_tokens", usage.prompt_tokens)
                            span.set_attribute("llm.usage.completion_tokens", usage.completion_tokens)
                else:
                    # Placeholder for other LLM client types
                    logger.error(f"LLM client type not recognized for API call for request {request_id}")
                    return None

                logger.debug(f"LLM raw response for {request_id} (first 200 chars): {response_content[:200] if response_content else 'None'}")
                if current_span and response_content: span.set_attribute("llm.response_length", len(response_content))
                return response_content

            except httpx.ReadTimeout as e: # Specific for httpx based clients like OpenAI's
                logger.warning(f"LLM API call timeout (Attempt {attempt + 1}/{MAX_LLM_RETRIES}) for request {request_id}: {e}")
                if current_span: current_span.add_event("LLM API Timeout", {"attempt": attempt + 1})
                if attempt == MAX_LLM_RETRIES - 1: raise # Re-raise on last attempt
            except Exception as e: # Catch other API errors (rate limits, auth, etc.)
                logger.error(f"Error during LLM API call (Attempt {attempt + 1}/{MAX_LLM_RETRIES}) for request {request_id}: {e}", exc_info=True)
                if current_span: current_span.record_exception(e)
                if attempt == MAX_LLM_RETRIES - 1: raise # Re-raise on last attempt

            await asyncio.sleep(LLM_RETRY_DELAY_SECONDS * (2**attempt)) # Exponential backoff
        return None


    async def _parse_and_validate_llm_dag_output_v2(self, llm_response_text: str, project_id_from_request: Optional[str]) -> Optional[DagDefinition]:
        logger.debug(f"Attempting to parse LLM DAG output V0.2 (first 500 chars): {llm_response_text[:500]}...")
        current_span = _trace_api.get_current_span() if _trace_api else None
        try:
            # Standardize cleaning of potential markdown code blocks
            cleaned_response = llm_response_text.strip()
            if cleaned_response.startswith("```json"): cleaned_response = cleaned_response[7:]
            if cleaned_response.endswith("```"): cleaned_response = cleaned_response[:-3]
            cleaned_response = cleaned_response.strip()

            if not cleaned_response:
                logger.error("LLM response is empty after cleaning.")
                if current_span and _trace_api: current_span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Empty LLM response"))
                return None

            data = json.loads(cleaned_response) # Throws json.JSONDecodeError on failure

            # --- Rigorous Validation ---
            if not isinstance(data, dict):
                raise ValueError("DAG root must be a JSON object.")

            dag_id = data.get("dag_id")
            if not isinstance(dag_id, str) or not dag_id:
                raise ValueError("'dag_id' field is missing or not a non-empty string.")

            project_id = project_id_from_request or data.get("project_id") # Prefer request's
            if project_id is not None and not isinstance(project_id, str): # Allow project_id to be null from LLM
                raise ValueError("'project_id' field must be a string or null.")

            description = data.get("description", f"DAG generated for {project_id or 'unknown project'}")
            if not isinstance(description, str):
                raise ValueError("'description' field must be a string.")

            nodes_data = data.get("nodes")
            if not isinstance(nodes_data, list):
                raise ValueError("'nodes' field must be a list.")

            validated_nodes: List[DagNode] = []
            node_ids_seen: Set[str] = set()

            for i, node_data in enumerate(nodes_data):
                if not isinstance(node_data, dict): 
                    raise ValueError(f"Node at index {i} is not a JSON object.")

                node_id = node_data.get("id")
                if not isinstance(node_id, str) or not node_id:
                    raise ValueError(f"Node at index {i} is missing 'id' or it's not a non-empty string.")
                if node_id in node_ids_seen:
                    raise ValueError(f"Duplicate node ID '{node_id}' found in DAG.")
                node_ids_seen.add(node_id)

                task_type = node_data.get("task_type")
                if not isinstance(task_type, str) or not task_type:
                    raise ValueError(f"Node '{node_id}' is missing 'task_type' or it's not a non-empty string.")

                dependencies = node_data.get("dependencies")
                if not isinstance(dependencies, list) or not all(isinstance(dep, str) for dep in dependencies):
                    raise ValueError(f"Node '{node_id}' has invalid 'dependencies' field; must be a list of strings.")

                # Ensure all dependencies exist as other node IDs in this DAG
                # This check will be done after all nodes are initially parsed for IDs.

                node: DagNode = {
                    "id": node_id,
                    "task_type": task_type,
                    "command": node_data.get("command"), 
                    "agent_handler": node_data.get("agent_handler"),
                    "params": node_data.get("params", {}),
                    "dependencies": dependencies
                }
                # Validate command and agent_handler types
                if node["command"] is not None and (not isinstance(node["command"], list) or not all(isinstance(c, str) for c in node["command"])): #type: ignore
                    raise ValueError(f"Node '{node_id}' has invalid 'command'; must be a list of strings.")
                if node["agent_handler"] is not None and not isinstance(node["agent_handler"], str):
                    raise ValueError(f"Node '{node_id}' has invalid 'agent_handler'; must be a string.")
                if node["params"] is not None and not isinstance(node["params"], dict):
                     raise ValueError(f"Node '{node_id}' has invalid 'params'; must be a dictionary.")

                validated_nodes.append(node)

            # Second pass: Validate dependencies
            for node in validated_nodes:
                for dep_id in node["dependencies"]:
                    if dep_id not in node_ids_seen:
                        raise ValueError(f"Node '{node['id']}' has an unknown dependency: '{dep_id}'.")

            # TODO: Add cycle detection in DAG (more complex, V0.2+ for BuildSurfAgent)

            dag_def: DagDefinition = {
                "dag_id": dag_id,
                "project_id": project_id,
                "description": description, # Add description to TypedDict if not there
                "nodes": validated_nodes
            }
            logger.info(f"Successfully parsed and validated DAG definition: {dag_def['dag_id']}")
            if current_span and _trace_api: current_span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
            return dag_def

        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON from LLM response: {e}. Response: {cleaned_response}")
            if current_span and _trace_api: current_span.record_exception(e); current_span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "JSONDecodeError"))
        except ValueError as e: # Our custom validation errors
            logger.error(f"Invalid DAG structure from LLM: {e}. Response: {cleaned_response}")
            if current_span and _trace_api: current_span.record_exception(e); current_span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, f"InvalidDAGStructure: {e}"))
        except Exception as e: 
            logger.error(f"Unexpected error parsing/validating LLM DAG output: {e}. Response: {cleaned_response}", exc_info=True)
            if current_span and _trace_api: current_span.record_exception(e); current_span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "DAGParseUnexpectedError"))
        return None

    # _generate_dag_for_request_logic needs to use _construct_llm_prompt_v2, _call_llm_api_v2, _parse_and_validate_llm_dag_output_v2
    async def _generate_dag_for_request_logic(self, request: PipelineGenerationRequestEvent):
        # (This is the core logic from the previous V0.1 BuildSurfAgent, now using the V2 helpers)
        user_prompt_data = request["user_prompt_data"]
        request_id = request["request_id"]
        project_id = user_prompt_data.get("target_project_id")

        full_llm_prompt = await self._construct_llm_prompt_v2(user_prompt_data) # Now async
        raw_llm_response = await self._call_llm_api_v2(full_llm_prompt, request_id)

        if not raw_llm_response:
            logger.error(f"No response from LLM for request_id: {request_id}. Cannot generate DAG.")
            # TODO: Publish a DagGenerationFailedEvent
            return

        dag_definition = await self._parse_and_validate_llm_dag_output_v2(raw_llm_response, project_id)

        if dag_definition:
            if self.event_bus.redis_client:
                event_data = DagDefinitionCreatedEvent(
                    event_type="DagDefinitionCreatedEvent", request_id=request_id,
                    project_id=dag_definition["project_id"], dag=dag_definition,
                    raw_llm_response=raw_llm_response, 
                    timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
                )
                channel = DAG_CREATED_EVENT_CHANNEL_TEMPLATE.format(project_id=dag_definition["project_id"] or "global")
                self.event_bus.publish(channel, event_data)
                logger.info(f"Published DagDefinitionCreatedEvent for DAG ID: {dag_definition['dag_id']} on channel {channel}")
            else: logger.error("Cannot publish DagDefinitionCreatedEvent: EventBus not connected.")
        else:
            logger.error(f"Failed to generate a valid DAG for request_id: {request_id}.")
            # TODO: Publish a DagGenerationFailedEvent (new event type)

    # generate_dag_for_request (traced wrapper) and main_event_loop, main, if __name__ == "__main__"
    # remain structurally similar to V0.1 (response #46), but call the _v2 methods.
    # Ensure main_event_loop and its handlers are async and use await.
    # For brevity, I won't re-paste the full event loop here, just assume it calls the _v2 logic.
    # The key change is making _construct_llm_prompt_v2 async because it awaits _get_additional_context_for_llm

    async def generate_dag_for_request(self, request: PipelineGenerationRequestEvent): # Traced wrapper
        span_name = "buildsurf_agent.generate_dag_for_request_traced" # Renamed to avoid conflict with logic method
        if not self.tracer: return await self._generate_dag_for_request_logic(request)
        with self.tracer.start_as_current_span(span_name) as span:
            # ... (set attributes as in V0.1) ...
            span.set_attributes({ "buildsurf.request_id": request["request_id"], "buildsurf.project_id": request["user_prompt_data"].get("target_project_id", "N/A")})
            try:
                await self._generate_dag_for_request_logic(request)
                if _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK)) # Assuming success if no exception
            except Exception as e:
                if _trace_api: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Core DAG logic failed"))
                raise # Re-raise to be caught by higher level error handlers if any

    async def handle_pipeline_generation_request(self, event_data_str: str): # Event handler
        # ... (tracing and parsing from V0.1) ...
        span = self._start_trace_span_if_available("handle_pipeline_generation_request")
        try:
            with span: #type: ignore
                request_data: PipelineGenerationRequestEvent = json.loads(event_data_str) #type: ignore
                if not (request_data.get("event_type") == "PipelineGenerationRequestEvent" and "request_id" in request_data):
                    logger.error(f"Malformed event: {event_data_str[:200]}"); return
                if _tracer: span.set_attribute("buildsurf.request_id", request_data["request_id"])
                logger.info(f"BuildSurfAgent handling request: {request_data['request_id']}")
                await self.generate_dag_for_request(request_data) # Call the traced wrapper
        except Exception as e:
            logger.error(f"Error in handle_pipeline_generation_request: {e}", exc_info=True)
            if _tracer and _trace_api and span: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR))


    async def main_event_loop(self):
        # ... (Same robust event loop from V0.1 in response #46, ensuring it calls the async handler)
        if not self.event_bus.redis_client: logger.critical("BuildSurfAgent: EventBus not connected. Exiting."); await asyncio.sleep(60); return
        pubsub = self.event_bus.subscribe_to_channel(PIPELINE_REQUEST_EVENT_CHANNEL)
        if not pubsub: logger.critical(f"BuildSurfAgent: Failed to subscribe to {PIPELINE_REQUEST_EVENT_CHANNEL}. Exiting."); await asyncio.sleep(60); return
        logger.info(f"BuildSurfAgent worker subscribed to {PIPELINE_REQUEST_EVENT_CHANNEL}, listening...")
        try:
            while True:
                message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "message":
                    await self.handle_pipeline_generation_request(message["data"])
                await asyncio.sleep(0.01) 
        except KeyboardInterrupt: logger.info("BuildSurfAgent event loop interrupted.")
        except Exception as e: logger.error(f"Critical error in BuildSurfAgent event loop: {e}", exc_info=True)
        finally: # ... (pubsub cleanup) ...
            logger.info("BuildSurfAgent shutting down...");
            if pubsub:
                try: await asyncio.to_thread(pubsub.unsubscribe, PIPELINE_REQUEST_EVENT_CHANNEL); await asyncio.to_thread(pubsub.close)
                except: pass
            await self.http_client.aclose(); logger.info("BuildSurfAgent shutdown complete.")


async def main_async_runner(): # Main async entry point for the agent
    # In a real app, AgentRegistry might be fetched from a shared context or DI
    # For now, BuildSurfAgent doesn't strictly need it for V0.2 prompt construction only.
    # If _get_additional_context_for_llm needs live agent data, pass registry here.
    agent_registry_instance = AgentRegistry() if AgentRegistry else None # Example instantiation
    agent = BuildSurfAgent(agent_registry=agent_registry_instance)
    await agent.main_event_loop()

if __name__ == "__main__":
    # Add defaultdict to imports if using it in _get_additional_context_for_llm
    from collections import defaultdict # For agent_capabilities_summary
    # Add AgentRegistry to imports if used in __main__
    from core.agent_registry import AgentRegistry 

    try:
        asyncio.run(main_async_runner())
    except KeyboardInterrupt:
        logger.info("BuildSurfAgent main execution stopped by user.")
    except Exception as e:
        logger.critical(f"BuildSurfAgent failed to start or unhandled error in main: {e}", exc_info=True)
