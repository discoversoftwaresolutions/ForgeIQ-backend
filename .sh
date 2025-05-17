export FORGEIQ_API_BASE_URL="<your_forgeiq_backend_api_url>"
# export FORGEIQ_API_KEY="<your_api_key_if_any>"
export PYTHONPATH=$(pwd):$PYTHONPATH  # Ensure Python can find your 'sdk', 'core', 'interfaces' packages
export LOG_LEVEL="INFO" # or DEBUG for more verbosity

python -m apps.agent_cli.app.main --help
python -m apps.agent_cli.app.main orchestrate start-build --project-id myproject --commit-sha mycommit --prompt "build it"
