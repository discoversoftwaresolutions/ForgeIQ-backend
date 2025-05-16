# agents/BuildSurfAgent/agent.py
import os
import json
import datetime
import uuid
import httpx # For async HTTP calls to LLM
import asyncio
import logging
from typing import Dict, Any, Optional, List

# --- Observability Setup ---
SERVICE_NAME = "BuildSurfAgent"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format=f'%(asctime)s - {SERVICE_NAME} - %(name)s - %(levelname)s - %(message)s'
)
tracer = None
try:
    from core.observability.tracing import setup_tracing
    tracer = setup_tracing(SERVICE_NAME)
except ImportError:
    logging.getLogger(SERVICE_NAME).warning(
        "BuildSurfAgent: Tracing setup failed. Ensure core.observability.tracing is available."
    )
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary
from interfaces.types.events import (
    PipelineGenerationRequest, DagNode, DagDefinition, DagDefinitionCreatedEvent
)

# Configuration for LLM
LLM_API_KEY = os.getenv("LLM_API_KEY") # e.g., OPENAI_API_KEY
LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "gpt-3.5-turbo") # Example
LLM_API_BASE_URL = os.getenv("LLM_API_BASE_URL") # For OpenAI compatible APIs or proxies

# Channels
PIPELINE_REQUEST_CHANNEL = "commands.buildsurf.generate_dag" # Agent listens on this
DAG_CREATED_EVENT_CHANNEL_TEMPLATE = "events.project.{project_id}.dag.created"


class BuildSurfAgent:
    def __init__(self):
        logger.info(f"Initializing BuildSurfAgent with LLM model: {LLM_MODEL_NAME}")
        self.event_bus = EventBus()
        self.http_client = httpx.AsyncClient(timeout=120.0) # Longer timeout for LLM calls

        if not self.event_bus.redis_client:
            logger.error("BuildSurfAgent critical: EventBus not connected.")
        if not LLM_API_KEY:
            logger.warning("LLM_API_KEY not set. BuildSurfAgent may not be able to generate DAGs.")

        # Initialize OpenAI client if that's the chosen LLM
        self.llm_client = None
        if LLM_API_KEY and "gpt" in LLM_MODEL_NAME.lower(): # Basic check for OpenAI
            try:
                from openai import AsyncOpenAI
                client_args = {"api_key": LLM_API_KEY}
                if LLM_API_BASE_URL:
                    client_args["base_url"] = LLM_API_BASE_URL
                self.llm_client = AsyncOpenAI(**client_args)
                logger.info("OpenAI client initialized.")
            except ImportError:
                logger.error("OpenAI Python client not installed. Please add 'openai' to requirements.txt")
            except Exception as e:
                logger.error(f"Failed to initialize OpenAI client: {e}")

        logger.info("BuildSurfAgent Initialized.")

    @property
    def tracer(self): # For OpenTelemetry
        return tracer

    def _construct_llm_prompt(self, user_prompt: str, project_id: Optional[str], context: Optional[Dict[str, Any]]) -> str:
        # This prompt engineering is critical for good results.
        # It should instruct the LLM on the desired output format (e.g., JSON for DAGDefinition).
        # Providing examples in the prompt (few-shot) can greatly improve output quality.

        # Example of expected JSON output structure for the LLM
        dag_json_example = """
        {
          "dag_id": "generated_dag_123",
          "project_id": "project_alpha",
          "nodes": [
            {
              "id": "lint_code",
              "task_type": "lint",
              "command": ["flake8", "."],
              "dependencies": []
            },
            {
              "id": "run_tests",
              "task_type": "test",
              "command": ["pytest"],
              "dependencies": ["lint_code"]
            },
            {
              "id": "build_app",
              "task_type": "build",
              "agent_handler": "BuildAgent", // Example: if a specific agent handles it
              "params": {"target": "production"},
              "dependencies": ["run_tests"]
            },
            {
              "id": "deploy_app",
              "task_type": "deploy",
              "agent_handler": "CI_CD_Agent",
              "params": {"environment": "staging"},
              "dependencies": ["build_app"]
            }
          ]
        }
        """

        prompt = f"""
        You are an expert system that generates build and deployment pipeline DAGs (Directed Acyclic Graphs)
        based on user prompts. The DAG should be represented as a JSON object matching the following structure:
        A root object with "dag_id" (string, generate a unique one), "project_id" (string, optional, use "{project_id}" if provided, otherwise it can be null),
        and "nodes" (a list of node objects).
        Each node object in the "nodes" list must have:
        - "id": A unique string identifier for the node/task.
        - "task_type": A string categorizing the task (e.g., 'lint', 'test', 'build', 'deploy', 'security_scan', 'custom_script', 'approval_gate').
        - "command": An optional list of strings representing the command to execute for simple tasks.
        - "agent_handler": An optional string specifying which specialized agent should handle this task node (e.g., 'BuildAgent', 'TestAgent', 'DeployAgent', 'SecurityAgent').
        - "params": An optional dictionary of key-value parameters for the task or agent handler.
        - "dependencies": A list of string IDs of other nodes that this node depends on. An empty list means no dependencies.

        Ensure the generated JSON is valid. Here's an example of the desired JSON output format:
        ```json
        {dag_json_example}
        ```

        User's request for the pipeline:
        "{user_prompt}"

        Additional context for the project (if any):
        {json.dumps(context, indent=2) if context else "No additional context provided."}

        Please generate the JSON representation of the DAG for this pipeline.
        Output only the JSON object. Do not include any other explanatory text before or after the JSON.
        """
        return prompt.strip()

    async def _parse_and_validate_llm_dag_output(self, llm_response_text: str, project_id: Optional[str]) -> Optional[DagDefinition]:
        # This method needs robust JSON parsing and validation against the DagDefinition structure.
        # Pydantic models would be excellent here for validation.
        logger.debug(f"Attempting to parse LLM DAG output: {llm_response_text[:200]}...")
        try:
            # LLMs sometimes wrap JSON in markdown ```json ... ```
            if llm_response_text.strip().startswith("```json"):
                llm_response_text = llm_response_text.strip()[7:-3].strip()

            data = json.loads(llm_response_text)

            # Basic structural validation (Pydantic would be much better)
            if not all(k in data for k in ["dag_id", "nodes"]) or not isinstance(data["nodes"], list):
                logger.error(f"LLM output missing required DAG fields (dag_id, nodes list). Output: {llm_response_text}")
                return None

            # Further validate nodes
            for node in data["nodes"]:
                if not all(k in node for k in ["id", "task_type", "dependencies"]) or \
                   not isinstance(node["dependencies"], list):
                    logger.error(f"LLM output has malformed node: {node}. Output: {llm_response_text}")
                    return None

            # Cast to TypedDict for type hinting, assuming structure matches
            # This does not perform runtime validation of inner types, Pydantic would.
            dag_def: DagDefinition = {
                "dag_id": str(data["dag_id"]),
                "project_id": project_id or data.get("project_id"),
                "nodes": [DagNode(**node) for node in data["nodes"]] # Naive casting
            }
            logger.info(f"Successfully parsed and validated DAG definition: {dag_def['dag_id']}")
            return dag_def
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON from LLM response: {e}. Response: {llm_response_text}")
        except TypeError as e: # For TypedDict casting issues
            logger.error(f"TypeError when constructing TypedDicts from LLM response: {e}. Response: {ll_response_text}")
        except Exception as e:
            logger.error(f"Unexpected error parsing/validating LLM DAG output: {e}. Response: {llm_response_text}", exc_info=True)
        return None

    async def generate_dag_from_prompt(self, request: PipelineGenerationRequest) -> Optional[DagDefinition]:
        span_name = "generate_dag_from_prompt"
        if not self.tracer: # Fallback
            return await self._generate_dag_from_prompt_logic(request)

        with self.tracer.start_as_current_span(span_name) as span:
            span.set_attributes({
                "buildsurf.request_id": request["request_id"],
                "buildsurf.project_id": request.get("project_id", "N/A"),
                "buildsurf.prompt_length": len(request["user_prompt"]),
                "buildsurf.llm_model_name": LLM_MODEL_NAME
            })
            try:
                dag_definition = await self._generate_dag_from_prompt_logic(request)
                if dag_definition:
                    span.set_attribute("buildsurf.dag_id", dag_definition["dag_id"])
                    span.set_attribute("buildsurf.node_count", len(dag_definition["nodes"]))
                    span.set_status(trace.StatusCode.OK)
                else:
                    span.set_attribute("buildsurf.dag_generation_failed", True)
                    span.set_status(trace.Status(trace.StatusCode.ERROR, "DAG generation failed"))
                return dag_definition
            except Exception as e:
                logger.error(f"Exception in generate_dag_from_prompt: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, "Core DAG generation logic failed"))
                return None

    async def _generate_dag_from_prompt_logic(self, request: PipelineGenerationRequest) -> Optional[DagDefinition]:
        if not self.llm_client:
            logger.error("LLM client (e.g., OpenAI) not initialized. Cannot generate DAG.")
            return None

        full_prompt = self._construct_llm_prompt(request["user_prompt"], request.get("project_id"), request.get("context"))
        logger.info(f"Generating DAG for request_id: {request['request_id']}, project: {request.get('project_id')}")
        logger.debug(f"LLM prompt (first 200 chars): {full_prompt[:200]}...")

        raw_llm_response_text: Optional[str] = None
        try:
            # Assuming OpenAI client for this example
            chat_completion = await self.llm_client.chat.completions.create(
                messages=[{"role": "user", "content": full_prompt}],
                model=LLM_MODEL_NAME,
                temperature=0.2, # Lower temperature for more deterministic/structured output
                # response_format={"type": "json_object"} # For newer OpenAI models that support JSON mode
            )
            raw_llm_response_text = chat_completion.choices[0].message.content
            if not raw_llm_response_text:
                logger.error("LLM returned empty response content.")
                return None

            logger.debug(f"Raw LLM response: {raw_llm_response_text}")
            dag_definition = await self._parse_and_validate_llm_dag_output(raw_llm_response_text, request.get("project_id"))

            if dag_definition and self.event_bus.redis_client:
                event_data = DagDefinitionCreatedEvent(
                    event_type="DagDefinitionCreatedEvent",
                    request_id=request["request_id"],
                    project_id=dag_definition["project_id"],
                    dag=dag_definition,
                    raw_llm_response=raw_llm_response_text, # Optional: include for audit
                    timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
                )
                channel = DAG_CREATED_EVENT_CHANNEL_TEMPLATE.format(project_id=dag_definition["project_id"] or "global")
                self.event_bus.publish(channel, event_data)
                logger.info(f"Published DagDefinitionCreatedEvent for DAG ID: {dag_definition['dag_id']}")

            return dag_definition

        except Exception as e: # Catch exceptions from LLM call or subsequent processing
            logger.error(f"Error during LLM call or DAG processing for request {request['request_id']}: {e}", exc_info=True)
            # Optionally, publish an error event
            # self.event_bus.publish_error("buildsurf_errors", {"request_id": request["request_id"], "error": str(e)})
            return None


    async def handle_pipeline_generation_request(self, event_data_str: str):
        span_name = "handle_pipeline_generation_request"
        parent_context = None # Extract from event if available

        if not self.tracer: # Fallback
            await self._handle_pipeline_generation_request_logic(event_data_str)
            return

        with self.tracer.start_as_current_span(span_name, context=parent_context) as span:
            try:
                request_data: PipelineGenerationRequest = json.loads(event_data_str)
                if not (request_data.get("event_type") == "PipelineGenerationRequest" and 
                        "request_id" in request_data and "user_prompt" in request_data):
                    logger.error(f"Malformed PipelineGenerationRequest: {event_data_str[:200]}")
                    span.set_status(trace.Status(trace.StatusCode.ERROR, "Malformed event"))
                    return

                span.set_attributes({
                    "messaging.system": "redis",
                    "messaging.operation": "process",
                    "buildsurf.request_id": request_data["request_id"]
                })
                logger.info(f"BuildSurfAgent handling PipelineGenerationRequest: {request_data['request_id']}")
                await self._generate_dag_from_prompt_logic(request_data) # Use logic part

            except json.JSONDecodeError:
                logger.error(f"Could not decode JSON from event: {event_data_str[:200]}")
                span.set_status(trace.Status(trace.StatusCode.ERROR, "JSON decode error"))
            except Exception as e:
                logger.error(f"Error handling PipelineGenerationRequest: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, "Event handling failed"))


    async def main_event_loop(self):
        if not self.event_bus.redis_client:
            logger.critical("BuildSurfAgent: Cannot start, EventBus not connected.")
            return

        pubsub = self.event_bus.subscribe_to_channel(PIPELINE_REQUEST_CHANNEL)
        if not pubsub:
            logger.critical(f"BuildSurfAgent: Failed to subscribe to {PIPELINE_REQUEST_CHANNEL}. Worker cannot start.")
            return

        logger.info(f"BuildSurfAgent worker subscribed to {PIPELINE_REQUEST_CHANNEL}, listening for events...")
        try:
            while True:
                message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "message": # Standard message type
                    await self.handle_pipeline_generation_request(message["data"])
                await asyncio.sleep(0.01) # Yield control
        except KeyboardInterrupt:
            logger.info("BuildSurfAgent event loop interrupted.")
        except Exception as e:
            logger.error(f"Critical error in BuildSurfAgent event loop: {e}", exc_info=True)
        finally:
            logger.info("BuildSurfAgent shutting down pubsub and HTTP client...")
            if pubsub:
                try: await asyncio.to_thread(pubsub.unsubscribe, PIPELINE_REQUEST_CHANNEL)
                except: pass # Ignore errors on unsubscribe
                try: await asyncio.to_thread(pubsub.close)
                except: pass # Ignore errors on close
            await self.http_client.aclose()
            logger.info("BuildSurfAgent shutdown complete.")

async def main(): # Main async entry point for the agent
    agent = BuildSurfAgent()
    await agent.main_event_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("BuildSurfAgent main execution stopped by user.")
    except Exception as e:
        logger.critical(f"BuildSurfAgent failed to start or unhandled error in main: {e}", exc_info=True)
