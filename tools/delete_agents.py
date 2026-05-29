"""Delete hosted agents from Azure AI Foundry with force=true."""

import sys
from pathlib import Path

# Allow running from tools/ directory
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from agents import discover_hosted_agents
from deploy_helpers import get_client

# All agent names managed by this project
ALL_AGENT_NAMES = [
    "bike-concierge",   # prompt agent
    "product-guide",    # hosted agent
    "support-hotline",  # hosted agent
    "repair-status",    # hosted agent
    "bike-support",     # workflow agent
]


def delete(agent_names: list[str] | None = None) -> None:
    client = get_client()

    if agent_names:
        names = agent_names
    else:
        # Default: delete all known agents for this project
        hosted = [cfg.name for cfg in discover_hosted_agents()]
        prompt_and_workflow = [n for n in ALL_AGENT_NAMES if n not in hosted]
        names = hosted + prompt_and_workflow

    if not names:
        print("No agents found to delete.")
        return

    for name in names:
        try:
            result = client.agents.delete(name, params={"force": "true"})
            print(f"Deleted agent '{name}': {result}")
        except Exception as e:
            print(f"Failed to delete agent '{name}': {e}")


if __name__ == "__main__":
    args = sys.argv[1:] if len(sys.argv) > 1 else None
    delete(args)
