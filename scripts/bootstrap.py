# ==============================
# 📁 scripts/bootstrap.py
# ==============================
import os
import subprocess
import sys
import venv # For virtual environment creation
import logging # Standard library

# Configure basic logging for the script
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("bootstrap")

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) # Monorepo root
VENV_NAME = ".venv" # Standard virtual environment name
VENV_PATH = os.path.join(ROOT_DIR, VENV_NAME)
REQUIREMENTS_FILE = os.path.join(ROOT_DIR, "requirements.txt")
ENV_EXAMPLE_FILE = os.path.join(ROOT_DIR, ".env.example")
ENV_FILE = os.path.join(ROOT_DIR, ".env")

def run_command(command: list, cwd: Optional[str] = ROOT_DIR, check: bool = True, shell: bool = False):
    """Helper to run a shell command."""
    logger.info(f"Executing: {' '.join(command)}")
    try:
        process = subprocess.run(command, cwd=cwd, check=check, capture_output=True, text=True, shell=shell)
        if process.stdout:
            logger.debug(f"Stdout: {process.stdout.strip()}")
        if process.stderr: # Log stderr even on success, as some tools output info here
            logger.debug(f"Stderr: {process.stderr.strip()}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed: {' '.join(command)}")
        logger.error(f"Stdout: {e.stdout.strip()}")
        logger.error(f"Stderr: {e.stderr.strip()}")
        return False
    except FileNotFoundError:
        logger.error(f"Command not found: {command[0]}. Ensure it's installed and in PATH.")
        return False


def setup_virtual_environment():
    logger.info("--- Setting up Python virtual environment ---")
    if sys.prefix == VENV_PATH:
        logger.info("Already in the correct virtual environment.")
    elif os.path.exists(VENV_PATH):
        logger.info(f"Virtual environment '{VENV_NAME}' already exists at {VENV_PATH}.")
        logger.info(f"To activate it, run: source {VENV_NAME}/bin/activate (Linux/macOS) or .\\{VENV_NAME}\\Scripts\\activate (Windows)")
    else:
        logger.info(f"Creating virtual environment '{VENV_NAME}' at {VENV_PATH}...")
        try:
            builder = venv.EnvBuilder(with_pip=True)
            builder.create(VENV_PATH)
            logger.info(f"Virtual environment created. Please activate it and re-run bootstrap if needed, or install dependencies now.")
        except Exception as e:
            logger.error(f"Failed to create virtual environment: {e}")
            return False
    return True


def install_dependencies():
    logger.info("--- Installing Python dependencies ---")
    if not os.path.exists(REQUIREMENTS_FILE):
        logger.error(f"'{REQUIREMENTS_FILE}' not found. Cannot install dependencies.")
        return False

    # Determine pip command based on whether we are in a venv
    if sys.prefix == VENV_PATH: # If running inside the venv created by this script
        pip_executable = os.path.join(VENV_PATH, "bin", "pip") if os.name != 'nt' else os.path.join(VENV_PATH, "Scripts", "pip.exe")
    else: # Use system pip or pip from current venv, hoping it's the right one
        pip_executable = "pip" 
        logger.warning("Not running in the project's .venv. Using system/current 'pip'. Consider activating the project venv first.")

    if not run_command([pip_executable, "install", "--upgrade", "pip", "setuptools", "wheel"]):
        logger.error("Failed to upgrade pip.")
        # Continue anyway, might work

    if run_command([pip_executable, "install", "-r", REQUIREMENTS_FILE]):
        logger.info("Python dependencies installed successfully.")
        return True
    else:
        logger.error("Failed to install Python dependencies.")
        return False

def setup_env_file():
    logger.info("--- Setting up .env file ---")
    if os.path.exists(ENV_FILE):
        logger.info(f"'{ENV_FILE}' already exists. Skipping creation from example.")
        return True
    if not os.path.exists(ENV_EXAMPLE_FILE):
        logger.warning(f"'{ENV_EXAMPLE_FILE}' not found. Cannot create '.env' file.")
        return False
    try:
        import shutil
        shutil.copy(ENV_EXAMPLE_FILE, ENV_FILE)
        logger.info(f"'{ENV_FILE}' created from '{ENV_EXAMPLE_FILE}'. Please review and update it with your settings.")
        return True
    except Exception as e:
        logger.error(f"Failed to create '{ENV_FILE}': {e}")
        return False

def init_agents_placeholder():
    logger.info("--- Initializing Agents (Conceptual V0.1) ---")
    logger.info("Agent initialization might involve:")
    logger.info("1. Ensuring necessary environment variables are set (check your .env file).")
    logger.info("2. Seeding databases (e.g., Weaviate schema for CodeNavAgent - see seed-embeddings.py).")
    logger.info("3. For local development, you would typically run each agent as a separate process/service:")
    logger.info("   Example: `PYTHONPATH=$(pwd) python -m agents.CodeNavAgent.app.agent` (from monorepo root)")
    logger.info("   (Ensure REDIS_URL, WEAVIATE_URL etc. are set in your .env file for local runs)")
    logger.info("   Consider using Docker Compose for managing multiple local agent services if needed (not defined in this bootstrap).")
    # In a more advanced bootstrap, you could:
    # - Check for Docker and Docker Compose
    # - Offer to run `docker-compose up -d redis weaviate` (if we define a docker-compose.yml)
    # - Check if required schemas exist in Weaviate and offer to create them.
    return True

def main():
    logger.info("Starting bootstrap process for the Agentic Build System...")

    success = setup_virtual_environment()
    if not success:
        logger.warning("Virtual environment setup had issues. Dependencies might not install correctly if not in a venv.")

    if not install_dependencies():
        logger.error("Bootstrap failed during dependency installation.")
        sys.exit(1)

    if not setup_env_file():
        logger.warning("Could not set up .env file. Please create it manually from .env.example.")

    if not init_agents_placeholder():
        logger.warning("Agent initialization conceptual step had issues.")

    logger.info("Bootstrap process completed.")
    logger.info(f"If you created a new venv ({VENV_NAME}), please activate it: source {VENV_NAME}/bin/activate (or .\\{VENV_NAME}\\Scripts\\activate on Windows)")
    logger.info("Review and update your .env file with necessary API keys and URLs.")

if __name__ == "__main__":
    # This script is intended to be run from the root of the monorepo
    # Example: python scripts/bootstrap.py
    # It assumes your current working directory *is* the monorepo root when it calculates ROOT_DIR.
    # If running `python scripts/bootstrap.py` from root, ROOT_DIR will be correct.
    # If running `python bootstrap.py` from inside `scripts/`, ROOT_DIR will be correct.

    # Verify we are in the expected directory structure
    if not os.path.basename(ROOT_DIR) == os.path.basename(os.getcwd()) and \
       not os.path.dirname(os.path.abspath(__file__)) == os.path.join(os.getcwd(), "scripts"):
         # This check is a bit complex, better to just instruct user to run from root.
         pass

    logger.info(f"Script running from: {os.path.abspath(__file__)}")
    logger.info(f"Calculated Monorepo Root: {ROOT_DIR}")
    if not os.path.exists(os.path.join(ROOT_DIR, "requirements.txt")):
        logger.error("This script should be run from the monorepo root, or requirements.txt is missing.")
        logger.error("Example: `python scripts/bootstrap.py` (run from monorepo root)")
        # sys.exit(1) # Let it try anyway, install_dependencies will fail more gracefully

    main()
