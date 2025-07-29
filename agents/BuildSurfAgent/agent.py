# ============================================
# 📁 agents/BuildSurfAgent/agent.py (V0.2 Enhancements) - With Dual LLM (Codex CLI & Gemini API)
# ============================================
import os
import json
import datetime
import uuid
import httpx # For HTTP requests and error types
import asyncio
import logging
import subprocess # For calling CLI tools like Codex CLI
from typing import Dict, Any, Optional, List, Union, Set # Added Set for node_ids_seen
from collections import defaultdict # Added for agent_capabilities_summary

# --- Observability Setup (as before) ---
SERVICE_NAME = "BuildSurfAgent"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format=f'%(asctime)s - {SERVICE_NAME} - %(name)s - %(levelname)s - %(message)s')
_tracer = None; _trace_api = None
try:
    from opentelemetry import trace as otel_trace_api
    from core.observability.tracing import setup_tracing # Assuming this path is correct
    _tracer = setup_tracing(SERVICE_NAME)
    _trace_api = otel_trace_api
except ImportError: logging.getLogger(SERVICE_NAME).warning("BuildSurfAgent: Tracing setup failed.")
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary # Assuming these paths are correct
from core.agent_registry import AgentRegistry # For fetching available agent handlers
from core.build_system_config import get_project_config # For context (assuming async version)
from interfaces.types.events import ( # Assuming these paths are correct
    PipelineGenerationRequestEvent, DagNode, DagDefinition, DagDefinitionCreatedEvent, PipelineGenerationUserPrompt
)

# === LLM Provider Configuration ===
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower() # 'codex' for CLI, 'gemini' for API
LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "gemini-pro") # Default for Gemini
LLM_API_KEY = os.getenv("LLM_API_KEY") # Generic API key for either OpenAI or Gemini

# --- OpenAI Specific (if LLM_PROVIDER could be OpenAI API) ---
OPENAI_API_BASE_URL = os.getenv("OPENAI_API_BASE_URL")
# --- Gemini Specific ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") # Specifically for Gemini, can be same as LLM_API_KEY if desired

MAX_LLM_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
LLM_RETRY_DELAY_SECONDS = int(os.getenv("LLM_RETRY_DELAY_SECONDS", "5"))

PIPELINE_REQUEST_EVENT_CHANNEL = "events.buildsurf.generate_dag.request"
DAG_CREATED_EVENT_CHANNEL_TEMPLATE = "events.project.{project_id}.dag.created"


class BuildSurfAgent:
    def __init__(self, agent_registry: Optional[AgentRegistry] = None):
        logger.info(f"Initializing BuildSurfAgent (V0.2) with LLM provider: {LLM_PROVIDER}, model: {LLM_MODEL_NAME}")
        self.event_bus = EventBus()
        self.http_client = httpx.AsyncClient(timeout=180.0) # Increased timeout for potentially long LLM calls
        self.agent_registry = agent_registry

        if not self.event_bus.redis_client: logger.error("BuildSurfAgent critical: EventBus not connected.")
        
        self.llm_api_client = None # For OpenAI or Gemini API clients
        
        # --- Initialize OpenAI Client (if chosen) ---
        if LLM_PROVIDER == "openai" or ("gpt" in LLM_MODEL_NAME.lower() and not LLM_PROVIDER == "gemini"):
            if not LLM_API_KEY: logger.warning("LLM_API_KEY not set. OpenAI client will not be initialized.")
            try:
                from openai import AsyncOpenAI # Ensure 'openai' is in requirements.txt
                client_args = {"api_key": LLM_API_KEY}
                if OPENAI_API_BASE_URL: client_args["base_url"] = OPENAI_API_BASE_URL
                self.llm_api_client = AsyncOpenAI(**client_args)
                logger.info("OpenAI client initialized for BuildSurfAgent.")
            except ImportError: logger.error("OpenAI Python client not installed.")
            except Exception as e: logger.error(f"Failed to initialize OpenAI client: {e}")
        
        # --- Initialize Gemini Client (if chosen) ---
        elif LLM_PROVIDER == "gemini":
            if not GEMINI_API_KEY: 
                logger.warning("GEMINI_API_KEY not set. Gemini client will not be initialized.")
            else:
                try:
                    import google.generativeai as genai # Redundant import but good for local clarity
                    genai.configure(api_key=GEMINI_API_KEY)
                    # For Gemini, the 'client' is typically configured globally via genai.configure()
                    # You then call methods directly on genai.GenerativeModel.
                    # We'll set a flag or dummy object if we need a 'client' handle.
                    self.llm_api_client = "gemini_configured" # Simple flag
                    logger.info("Gemini API configured for BuildSurfAgent.")
                except ImportError: logger.error("Google GenerativeAI client not installed.")
                except Exception as e: logger.error(f"Failed to initialize Gemini client: {e}")
        
        elif LLM_PROVIDER == "codex":
            logger.info("BuildSurfAgent configured to use Codex CLI.")
            if not os.path.exists("/usr/local/bin/codex-cli"): # Basic check if CLI tool exists
                logger.warning("Codex CLI executable not found at /usr/local/bin/codex-cli. Ensure it's installed and in PATH.")

        else:
            logger.error(f"Unsupported LLM_PROVIDER '{LLM_PROVIDER}'. BuildSurfAgent will not function for LLM calls.")

        logger.info("BuildSurfAgent V0.2 Initialized.")

    @property
    def tracer(self): return _tracer

    def _start_trace_span_if_available(self, operation_name: str, parent_context: Optional[Any] = None, **attrs):
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
        context = {}
        if project_id:
            project_specific_config = await get_project_config(project_id)
            if project_specific_config:
                context["project_config_summary"] = {
                    "description": project_specific_config.get("description"),
                    "supported_tasks_hint": project_specific_config.get("supported_tasks"),
                    "default_environment": project_specific_config.get("default_environment")
                }

        if self.agent_registry:
            active_agents = self.agent_registry.discover_agents(status="active")
            agent_capabilities_summary = defaultdict(list)
            for agent_info in active_agents:
                for cap in agent_info.get("capabilities", []):
                    agent_capabilities_summary[agent_info["agent_type"]].append(cap.get("name"))
            if agent_capabilities_summary:
                context["available_agent_capabilities"] = dict(agent_capabilities_summary)

        return context

    async def _construct_llm_prompt_v2(self, user_prompt_data: PipelineGenerationUserPrompt) -> Dict[str, Union[str, List[Dict[str, str]]]]:
        """
        Constructs the LLM prompt, now also preparing conversation history for chat models.
        Returns a dict with 'prompt_text' and 'history' (empty list if not provided).
        """
        project_id = user_prompt_data.get("target_project_id")
        user_prompt = user_prompt_data["prompt_text"]
        user_context = user_prompt_data.get("additional_context", {})
        conversation_history = user_prompt_data.get("conversation_history", []) # New: Extract conversation history

        dynamic_context = await self._get_additional_context_for_llm(project_id)
        full_context = {**dynamic_context, **user_context}

        dag_node_example_fields = """
            - "id": str (unique task ID, e.g., "lint-python-code")
            - "task_type": str (e.g., "lint", "test", "build", "deploy", "security_scan", "code_analysis", "custom_script")
            - "command": Optional[List[str]] (e.g., ["flake8", "."])
            - "agent_handler": Optional[str] (e.g., "SecurityAgent", "TestAgent", "PlanAgent" itself if it has sub-dag logic)
            - "params": Optional[Dict[str, Any]] (e.g., {"severity_threshold": "HIGH"} for SecurityAgent)
            - "dependencies": List[str] (list of other node 'id's)
        """
        dag_json_example = f"""
        {{
          "dag_id": "llm_generated_dag_{uuid.uuid4().hex[:8]}",
          "project_id": {json.dumps(project_id)},
          "description": "<A brief description of the pipeline generated from the prompt>",
          "nodes": [
            {{"id": "lint_code", "task_type": "lint", "command": ["make", "lint"], "dependencies": [], "params": {{"strict": true}}}},
            {{"id": "unit_tests", "task_type": "test", "agent_handler": "TestAgent", "params": {{"suite": "unit"}}, "dependencies": ["lint_code"]}}
          ]
        }}
        """

        system_instruction = f"""
        You are an AI assistant that designs build and deployment pipeline DAGs (Directed Acyclic Graphs).
        Your goal is to translate the user's request into a valid JSON object representing this DAG.

        The root JSON object must contain:
        - "dag_id": A unique string identifier for the DAG (e.g., "dag_" followed by a short UUID).
        - "project_id": The string project identifier if provided (use {json.dumps(project_id)} if applicable, otherwise null).
        - "description": A brief string describing the generated pipeline.
        - "nodes": A list of node objects, where each node represents a task in the pipeline.

        Each node object in the "nodes" list must have the following fields:
        {dag_node_example_fields}

        Available context for this request:
        {json.dumps(full_context, indent=2, default=str)}

        The output must be a single, valid JSON object.
        """

        # For models that take system messages or explicit chat history (like Gemini, OpenAI Chat models)
        if LLM_PROVIDER == "gemini" or isinstance(self.llm_api_client, AsyncOpenAI):
            # Convert system_instruction and user_prompt into messages format
            messages = [{"role": "user", "parts": [system_instruction + "\nUser's request:\n\"" + user_prompt + "\""]}]
            # If conversation_history is provided, it should typically precede the current turn.
            # However, for DAG generation, often the system prompt is a single, strong instruction.
            # For simplicity, if conversation_history is included, we can prepend it
            # or just use the combined prompt. Given the DAG prompt is very strict,
            # we'll embed user_prompt directly into the system_instruction as the last "user" turn.
            # If `conversation_history` is meant for LLMs that handle multi-turn, it should be processed.
            # For now, `_construct_llm_prompt_v2` returns `prompt_text` for the LLM.
            # Let's adjust it to return structured messages if LLM is chat-based.

            # This method now returns a dict with prepared messages OR a single string prompt.
            # _call_llm_api_v2 will handle the interpretation.
            return {
                "prompt_text": system_instruction + "\nUser's request:\n\"" + user_prompt + "\"\n\n" + dag_json_example, # Combined for Codex CLI or simpler completion models
                "messages": [{"role": "user", "parts": [system_instruction + "\nUser's request:\n\"" + user_prompt + "\""]},
                             {"role": "model", "parts": [dag_json_example]} # Example of expected model response
                            ] if LLM_PROVIDER == "gemini" or isinstance(self.llm_api_client, AsyncOpenAI) else None
            }
        else: # For Codex CLI or older completion models
            full_prompt = f"""
            {system_instruction}

            User's request for the pipeline:
            "{user_prompt}"

            Example of expected JSON output:
            {dag_json_example}
            """
            return {"prompt_text": full_prompt.strip(), "messages": None} # Return simple prompt for CLI

    async def _call_llm_api_v2(self, prompt_data: Dict[str, Union[str, List[Dict[str, str]]]], request_id: str) -> Optional[str]:
        """
        Calls the selected LLM provider (Codex CLI or Gemini API) based on LLM_PROVIDER.
        `prompt_data` contains either 'prompt_text' (for CLI/completion models) or 'messages' (for chat models).
        """
        current_span = _trace_api.get_current_span() if _trace_api else None
        
        # Extract prompt_text and messages from prompt_data
        prompt_text_for_cli = prompt_data.get("prompt_text", "")
        messages_for_api = prompt_data.get("messages", [])

        if not (self.llm_api_client or LLM_PROVIDER == "codex"): # Ensure a client or CLI is configured
            logger.error(f"LLM client or provider not available/configured for request_id: {request_id}. Provider: {LLM_PROVIDER}")
            if current_span: current_span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "LLMProviderNotConfigured"))
            return None

        for attempt in range(MAX_LLM_RETRIES):
            try:
                logger.info(f"Sending request to LLM (Attempt {attempt + 1}/{MAX_LLM_RETRIES}) for request_id: {request_id}. Provider: {LLM_PROVIDER}")
                
                response_content = None

                if LLM_PROVIDER == "codex":
                    if not prompt_text_for_cli:
                        raise ValueError("Prompt text is empty for Codex CLI.")
                    # Run Codex CLI as a blocking call in a thread
                    def _blocking_codex_cli_call():
                        cmd = [
                            "codex-cli",
                            "--prompt", prompt_text_for_cli,
                            # Pass other config options if codex-cli supports it (adjust as needed)
                            # "--config", json.dumps(config_options) # If config_options are relevant
                        ]
                        process = subprocess.run(cmd, capture_output=True, text=True, check=True)
                        return process.stdout.strip()
                    
                    response_content = await asyncio.to_thread(_blocking_codex_cli_call)
                    
                    logger.debug(f"Codex CLI raw response for {request_id} (first 200 chars): {response_content[:200] if response_content else 'None'}")


                elif LLM_PROVIDER == "gemini":
                    if not messages_for_api:
                        raise ValueError("Messages are empty for Gemini API.")
                    
                    model = genai.GenerativeModel(LLM_MODEL_NAME) # Re-instantiate or use global if preferred
                    
                    response = await model.generate_content_async( # Use async method
                        messages_for_api,
                        generation_config=genai.GenerationConfig(
                            temperature=0.1, # Low temperature for structured JSON output
                            max_output_tokens=2048 # Max tokens for DAG JSON
                        ),
                        safety_settings=[
                            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                        ]
                    )
                    response_content = response.text
                    logger.debug(f"Gemini API raw response for {request_id} (first 200 chars): {response_content[:200] if response_content else 'None'}")


                elif isinstance(self.llm_api_client, AsyncOpenAI): # Existing OpenAI API handling
                    if not messages_for_api: # OpenAI chat API also uses messages
                        raise ValueError("Messages are empty for OpenAI API.")
                    
                    chat_completion = await self.llm_api_client.chat.completions.create(
                        messages=messages_for_api,
                        model=LLM_MODEL_NAME,
                        temperature=0.1,
                        max_tokens=2048
                    )
                    response_content = chat_completion.choices[0].message.content
                    if current_span:
                        usage = chat_completion.usage
                        if usage:
                            current_span.set_attribute("llm.usage.total_tokens", usage.total_tokens)
                            current_span.set_attribute("llm.usage.prompt_tokens", usage.prompt_tokens)
                            current_span.set_attribute("llm.usage.completion_tokens", usage.completion_tokens)
                    logger.debug(f"OpenAI API raw response for {request_id} (first 200 chars): {response_content[:200] if response_content else 'None'}")

                else:
                    logger.error(f"Unsupported LLM_PROVIDER '{LLM_PROVIDER}' or unrecognized API client for request {request_id}")
                    if current_span: current_span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "UnsupportedLLMProvider"))
                    return None

                if current_span and response_content: current_span.set_attribute("llm.response_length", len(response_content))
                return response_content

            except httpx.ReadTimeout as e: # Specific for httpx based clients like OpenAI's
                logger.warning(f"LLM API call timeout (Attempt {attempt + 1}/{MAX_LLM_RETRIES}) for request {request_id}: {e}")
                if current_span: current_span.add_event("LLM API Timeout", {"attempt": attempt + 1})
                if attempt == MAX_LLM_RETRIES - 1: raise # Re-raise on last attempt
            except subprocess.CalledProcessError as e: # Specific for CLI failures
                logger.error(f"CLI tool execution failed (Attempt {attempt + 1}/{MAX_LLM_RETRIES}) for request {request_id}: {e.stderr.strip()}", exc_info=True)
                if current_span: current_span.record_exception(e); current_span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, f"CLIExecutionFailed: {e.returncode}"))
                if attempt == MAX_LLM_RETRIES - 1: raise # Re-raise on last attempt
            except Exception as e: # Catch other API errors (rate limits, auth, etc.)
                logger.error(f"Error during LLM call (Attempt {attempt + 1}/{MAX_LLM_RETRIES}) for request {request_id}: {e}", exc_info=True)
                if current_span: current_span.record_exception(e); current_span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "LLMCallError"))
                if attempt == MAX_LLM_RETRIES - 1: raise # Re-raise on last attempt

            await asyncio.sleep(LLM_RETRY_DELAY_SECONDS * (2**attempt)) # Exponential backoff
        return None


    async def _parse_and_validate_llm_dag_output_v2(self, llm_response_text: str, project_id_from_request: Optional[str]) -> Optional[DagDefinition]:
        logger.debug(f"Attempting to parse LLM DAG output V0.2 (first 500 chars): {llm_response_text[:500]}...")
        current_span = _trace_api.get_current_span() if _trace_api else None
        try:
            cleaned_response = llm_response_text.strip()
            if cleaned_response.startswith("```json"): cleaned_response = cleaned_response[7:]
            if cleaned_response.endswith("```"): cleaned_response = cleaned_response[:-3]
            cleaned_response = cleaned_response.strip()

            if not cleaned_response:
                logger.error("LLM response is empty after cleaning.")
                if current_span and _trace_api: current_span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Empty LLM response"))
                return None

            data = json.loads(cleaned_response) # Throws json.JSONDecodeError on failure

            if not isinstance(data, dict):
                raise ValueError("DAG root must be a JSON object.")

            dag_id = data.get("dag_id")
            if not isinstance(dag_id, str) or not dag_id:
                raise ValueError("'dag_id' field is missing or not a non-empty string.")

            project_id = project_id_from_request or data.get("project_id")
            if project_id is not None and not isinstance(project_id, str):
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

                node: DagNode = {
                    "id": node_id,
                    "task_type": task_type,
                    "command": node_data.get("command"), 
                    "agent_handler": node_data.get("agent_handler"),
                    "params": node_data.get("params", {}),
                    "dependencies": dependencies
                }
                if node["command"] is not None and (not isinstance(node["command"], list) or not all(isinstance(c, str) for c in node["command"])): #type: ignore
                    raise ValueError(f"Node '{node_id}' has invalid 'command'; must be a list of strings.")
                if node["agent_handler"] is not None and not isinstance(node["agent_handler"], str):
                    raise ValueError(f"Node '{node_id}' has invalid 'agent_handler'; must be a string.")
                if node["params"] is not None and not isinstance(node["params"], dict):
                     raise ValueError(f"Node '{node_id}' has invalid 'params'; must be a dictionary.")

                validated_nodes.append(node)

            for node in validated_nodes:
                for dep_id in node["dependencies"]:
                    if dep_id not in node_ids_seen:
                        raise ValueError(f"Node '{node['id']}' has an unknown dependency: '{dep_id}'.")

            dag_def: DagDefinition = {
                "dag_id": dag_id,
                "project_id": project_id,
                "description": description,
                "nodes": validated_nodes
            }
            logger.info(f"Successfully parsed and validated DAG definition: {dag_def['dag_id']}")
            if current_span and _trace_api: current_span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
            return dag_def

        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON from LLM response: {e}. Response: {cleaned_response}")
            if current_span and _trace_api: current_span.record_exception(e); current_span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "JSONDecodeError"))
        except ValueError as e:
            logger.error(f"Invalid DAG structure from LLM: {e}. Response: {cleaned_response}")
            if current_span and _trace_api: current_span.record_exception(e); current_span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, f"InvalidDAGStructure: {e}"))
        except Exception as e: 
            logger.error(f"Unexpected error parsing/validating LLM DAG output: {e}. Response: {cleaned_response}", exc_info=True)
            if current_span and _trace_api: current_span.record_exception(e); current_span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "DAGParseUnexpectedError"))
        return None

    # _generate_dag_for_request_logic now calls _construct_llm_prompt_v2 and _call_llm_api_v2
    # It must now expect `_construct_llm_prompt_v2` to return a dictionary
    async def _generate_dag_for_request_logic(self, request: PipelineGenerationRequestEvent):
        user_prompt_data = request["user_prompt_data"]
        request_id = request["request_id"]
        project_id = user_prompt_data.get("target_project_id")

        # Now get prompt_data which contains both prompt_text and messages
        llm_prompt_data = await self._construct_llm_prompt_v2(user_prompt_data)
        
        raw_llm_response = await self._call_llm_api_v2(llm_prompt_data, request_id)

        if not raw_llm_response:
            logger.error(f"No response from LLM for request_id: {request_id}. Cannot generate DAG.")
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

    async def generate_dag_for_request(self, request: PipelineGenerationRequestEvent): # Traced wrapper
        span_name = "buildsurf_agent.generate_dag_for_request_traced"
        if not self.tracer: return await self._generate_dag_for_request_logic(request)
        with self.tracer.start_as_current_span(span_name) as span:
            span.set_attributes({ "buildsurf.request_id": request["request_id"], "buildsurf.project_id": request["user_prompt_data"].get("target_project_id", "N/A")})
            try:
                await self._generate_dag_for_request_logic(request)
                if _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
            except Exception as e:
                if _trace_api: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Core DAG logic failed"))
                raise

    async def handle_pipeline_generation_request(self, event_data_str: str): # Event handler
        span = self._start_trace_span_if_available("handle_pipeline_generation_request")
        try:
            with span: #type: ignore
                request_data: PipelineGenerationRequestEvent = json.loads(event_data_str) #type: ignore
                if not (request_data.get("event_type") == "PipelineGenerationRequestEvent" and "request_id" in request_data):
                    logger.error(f"Malformed event: {event_data_str[:200]}"); return
                if _tracer: span.set_attribute("buildsurf.request_id", request_data["request_id"])
                logger.info(f"BuildSurfAgent handling request: {request_data['request_id']}")
                await self.generate_dag_for_request(request_data)
        except Exception as e:
            logger.error(f"Error in handle_pipeline_generation_request: {e}", exc_info=True)
            if _tracer and _trace_api and span: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR))

    async def main_event_loop(self):
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
        finally:
            logger.info("BuildSurfAgent shutting down.");
            if pubsub:
                try: await asyncio.to_thread(pubsub.unsubscribe, PIPELINE_REQUEST_EVENT_CHANNEL); await asyncio.to_thread(pubsub.close)
                except: pass
            await self.http_client.aclose(); logger.info("BuildSurfAgent shutdown complete.")

async def main_async_runner():
    agent_registry_instance = AgentRegistry() if AgentRegistry else None
    agent = BuildSurfAgent(agent_registry=agent_registry_instance)
    await agent.main_event_loop()

if __name__ == "__main__":
    from collections import defaultdict
    from core.agent_registry import AgentRegistry 
    try:
        asyncio.run(main_async_runner())
    except KeyboardInterrupt:
        logger.info("BuildSurfAgent main execution stopped by user.")
    except Exception as e:
        logger.critical(f"BuildSurfAgent failed to start or unhandled error in main: {e}", exc_info=True)
