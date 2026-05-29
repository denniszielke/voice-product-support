"""Orchestrator — deploys all agent types to Azure AI Foundry."""

import sys
from pathlib import Path

# Allow running from tools/ directory
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from deploy_prompt_agents import deploy as deploy_prompt
from deploy_toolbox import deploy as deploy_toolbox
from deploy_hosted_agents import deploy as deploy_hosted
from deploy_workflow_agents import deploy as deploy_workflow


def main() -> None:
    print("=== Deploying prompt agents ===")
    deploy_prompt()

    print("\n=== Deploying toolbox ===")
    deploy_toolbox()

    print("\n=== Deploying hosted agents ===")
    deploy_hosted()

    print("\n=== Deploying workflow agents ===")
    deploy_workflow()

    print("\nAll agents deployed.")


if __name__ == "__main__":
    main()
