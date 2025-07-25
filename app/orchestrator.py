# ==============================================
# 📁 orchestrator/main_orchestrator.py (V0.3)
# ==============================================

# [Existing content preserved above. Only additional methods added below.]

    async def request_mcp_strategy_optimization(self, 
                                              project_id: str, 
                                              current_dag: Optional[DagDefinition] = None
                                             ) -> Optional[Dict[str, Any]]:
        if not self.forgeiq_sdk_client:
            logger.error(f"Orchestrator: SDK client not available to request MCP strategy for '{project_id}'.")
            return None

        span = self._start_trace_span_if_available("request_mcp_strategy", project_id=project_id)
        logger.info(f"Orchestrator: Requesting MCP build strategy optimization for project '{project_id}'.")
        try:
            with span:  # type: ignore
                dag_info_payload = current_dag if current_dag else None

                sdk_context_for_mcp = {
                    "project_id": project_id,
                    "dag_representation": current_dag.get("nodes") if current_dag else None,
                    "telemetry_data": {"source": "Orchestrator_MCP_Request"}
                }

                mcp_response = await self.forgeiq_sdk_client.request_mcp_build_strategy(
                    project_id=project_id,
                    current_dag_info=dag_info_payload
                )

                if mcp_response:
                    logger.info(f"Orchestrator: Received strategy from MCP for '{project_id}': {message_summary(mcp_response)}")
                    if _trace_api and span:
                        span.set_attribute("mcp.response_status", mcp_response.get("status"))
                        span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                    return mcp_response
                else:
                    logger.warning(f"Orchestrator: No strategy response from MCP for '{project_id}'.")
                    if _trace_api and span:
                        span.set_attribute("mcp.response_received", False)
                    return None
        except Exception as e:
            logger.error(f"Orchestrator: Error requesting MCP strategy for '{project_id}': {e}", exc_info=True)
            if _trace_api and span:
                span.record_exception(e)
                span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR))
            return None

    async def request_and_apply_mcp_optimization(
        self, 
        project_id: str, 
        current_dag: DagDefinition,
        flow_id: str
        ) -> Optional[DagDefinition]:

        if not self.forgeiq_sdk_client:
            logger.error(f"Flow {flow_id}: SDK client not available for MCP optimization request for project '{project_id}'.")
            return None

        span_attrs = {"project_id": project_id, "flow_id": flow_id, "mcp_task": "optimize_dag"}
        span = self._start_trace_span_if_available("request_mcp_optimization", **span_attrs)
        logger.info(f"Flow {flow_id}: Orchestrator requesting MCP build strategy optimization for project '{project_id}'.")

        await self._update_flow_state(flow_id, {"current_stage": f"REQUESTING_MCP_OPTIMIZATION_FOR_DAG_{current_dag['dag_id']}"})

        try:
            with span:  # type: ignore
                mcp_request_context = SDKMCPStrategyRequestContext(
                    project_id=project_id,
                    current_dag_snapshot=[dict(node) for node in current_dag.get("nodes", [])],
                    optimization_goal="general_build_efficiency",
                    additional_mcp_context={"triggering_flow_id": flow_id}
                )

                mcp_response: SDKMCPStrategyResponse = await self.forgeiq_sdk_client.request_mcp_build_strategy(
                    context=mcp_request_context  # type: ignore
                )

                if mcp_response and mcp_response.get("status") == "strategy_provided" and mcp_response.get("strategy_details"):
                    strategy_details = mcp_response["strategy_details"]
                    new_dag_raw = strategy_details.get("new_dag_definition_raw")

                    if new_dag_raw and isinstance(new_dag_raw, dict):
                        validated_new_dag = DagDefinition(**new_dag_raw)  # type: ignore
                        logger.info(f"Flow {flow_id}: MCP provided optimized DAG '{validated_new_dag.get('dag_id')}' for project '{project_id}'.")
                        if _trace_api and span:
                            span.set_attribute("mcp.strategy_id", strategy_details.get("strategy_id"))
                            span.set_attribute("mcp.new_dag_id", validated_new_dag.get("dag_id"))
                            span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                        await self._update_flow_state(flow_id, {"current_stage": f"MCP_OPTIMIZATION_RECEIVED_DAG_{validated_new_dag.get('dag_id')}"})
                        return validated_new_dag
                    else:
                        logger.info(f"Flow {flow_id}: MCP provided strategy but no new DAG definition for project '{project_id}'. Directives: {strategy_details.get('directives')}")
                        if _trace_api and span:
                            span.set_attribute("mcp.directives_only", True)
                else:
                    msg = f"MCP strategy optimization failed or no strategy provided for project '{project_id}'. Response: {mcp_response}"
                    logger.warning(f"Flow {flow_id}: {msg}")
                    if _trace_api and span:
                        span.set_attribute("mcp.error", msg)

                await self._update_flow_state(flow_id, {"current_stage": f"MCP_OPTIMIZATION_NO_NEW_DAG"})
        except Exception as e:
            logger.error(f"Flow {flow_id}: Error requesting/processing MCP strategy for project '{project_id}': {e}", exc_info=True)
            await self._update_flow_state(flow_id, {"current_stage": "MCP_OPTIMIZATION_ERROR", "error_message": str(e)})
            if _trace_api and span:
                span.record_exception(e)
                span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR))
        return None

    async def run_full_build_flow(self, project_id: str, commit_sha: str, changed_files_list: List[str], user_prompt_for_pipeline: Optional[str] = None, flow_id_override: Optional[str] = None):
        flow_id = flow_id_override or f"build_flow_{project_id}_{commit_sha[:7]}_{str(uuid.uuid4())[:4]}"
        initial_state = OrchestrationFlowState(
            flow_id=flow_id, flow_name="full_build_flow", project_id=project_id, commit_sha=commit_sha,
            status="PENDING", current_stage="INITIALIZING", 
            started_at=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
            updated_at=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
            last_event_id_processed=None, dag_id=None, deployment_request_id=None, error_message=None,
            context_data={"user_prompt": user_prompt_for_pipeline, "num_changed_files": len(changed_files_list)}
        )
        await self._update_flow_state(flow_id, initial_state, initial_state_if_new=initial_state)  # type: ignore
        logger.info(f"Orchestrator: V0.3 Full Build Flow '{flow_id}' initiated.")

        span = self._start_trace_span_if_available("run_full_build_flow", project_id=project_id, commit_sha=commit_sha, flow_id=flow_id)
        try:
            with span:  # type: ignore
                await self._update_flow_state(flow_id, {"status": "RUNNING", "current_stage": "AWAITING_AFFECTED_TASKS"})
                new_commit_event_id = str(uuid.uuid4())
                commit_payload = {
                    "event_id": new_commit_event_id, "project_id": project_id, "commit_sha": commit_sha,
                    "changed_files": [{"file_path": fp, "change_type": "modified"} for fp in changed_files_list],
                    "timestamp": datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
                }
                await self.message_router.dispatch(logical_message_type="SubmitNewCommit", payload=commit_payload)

                # Wait for affected tasks identification
                affected_tasks_event = await self._wait_for_event(
                    expected_event_type="AffectedTasksIdentifiedEvent",
                    correlation_id=new_commit_event_id,
                    correlation_id_field_in_event="commit_event_id",
                    event_channel_pattern=f"project:{project_id}:*",
                )

                if not affected_tasks_event:
                    raise OrchestrationError("No AffectedTasksIdentifiedEvent received.", flow_id, "AWAITING_AFFECTED_TASKS")

                # Optionally optimize DAG via MCP after tasks are identified
                current_dag = affected_tasks_event.get("dag_definition")  # assumed from event
                if current_dag:
                    optimized_dag = await self.request_and_apply_mcp_optimization(
                        project_id=project_id,
                        current_dag=current_dag,
                        flow_id=flow_id
                    )
                    # Further logic to forward optimized_dag to PlanAgent would follow here.

                await self._update_flow_state(flow_id, {"status": "COMPLETED_SUCCESS", "current_stage": "FLOW_COMPLETE_PLACEHOLDER"})
                if _tracer and _trace_api:
                    span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))

        except Exception as e:
            logger.error(f"Orchestrator: Error in full build flow '{flow_id}': {e}", exc_info=True)
            await self._update_flow_state(flow_id, {"status": "FAILED", "current_stage": "ORCHESTRATION_ERROR", "error_message": str(e)})
            if _tracer and _trace_api and span:
                span.record_exception(e)
                span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Full build flow error"))
