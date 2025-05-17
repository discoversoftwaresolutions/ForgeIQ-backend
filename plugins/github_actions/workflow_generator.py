# ==================================================
# 📁 plugins/github_actions/workflow_generator.py
# ==================================================
import sys
import yaml # For YAML generation
from typing import Dict, Any, List

# This script would typically be added to requirements.txt:
# PyYAML

def generate_basic_python_ci_workflow(python_version: str = "3.11") -> Dict[str, Any]:
    """Generates a basic GitHub Actions workflow YAML structure for Python CI."""

    workflow = {
        "name": "Generated Python CI Workflow",
        "on": {
            "push": {"branches": ["main"]},
            "pull_request": {"branches": ["main"]},
        },
        "jobs": {
            "lint_and_test": {
                "name": "Lint & Test Python",
                "runs-on": "ubuntu-latest",
                "steps": [
                    {"name": "Checkout code", "uses": "actions/checkout@v4"},
                    {
                        "name": f"Set up Python {python_version}",
                        "uses": "actions/setup-python@v5",
                        "with": {"python-version": python_version},
                    },
                    {
                        "name": "Install dependencies",
                        "run": (
                            "python -m pip install --upgrade pip\n"
                            "pip install -r requirements.txt\n"
                            "pip install flake8 black pytest # Or load from dev-requirements.txt"
                        ),
                    },
                    {"name": "Lint with Flake8", "run": "flake8 . --count --exit-zero --show-source --statistics"},
                    {"name": "Check formatting with Black", "run": "black --check ."},
                    {"name": "Test with Pytest", "run": "pytest"},
                ],
            }
        },
    }
    return workflow

def main():
    """Prints the generated workflow to stdout or a file."""
    args = sys.argv[1:] # Basic argument parsing
    output_file = None
    if args and args[0] == "--output" and len(args) > 1:
        output_file = args[1]

    basic_workflow = generate_basic_python_ci_workflow()

    if output_file:
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                yaml.dump(basic_workflow, f, sort_keys=False, indent=2)
            print(f"Workflow YAML written to {output_file}")
        except IOError as e:
            print(f"Error writing to file {output_file}: {e}", file=sys.stderr)
    else:
        # Print to stdout
        print("# Generated GitHub Actions Workflow YAML:")
        print(yaml.dump(basic_workflow, sort_keys=False, indent=2))

if __name__ == "__main__":
    # Ensure PyYAML is installed to run this script: pip install PyYAML
    # Example usage from monorepo root:
    # export PYTHONPATH=$(pwd):$PYTHONPATH
    # python -m plugins.github_actions.workflow_generator --output .github/workflows/generated_ci.yml
    main()
