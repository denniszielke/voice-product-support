"""Deploy prompt-based agents to Azure AI Foundry."""

import sys
from pathlib import Path

from azure.ai.projects.models import (
    PromptAgentDefinition,
    PromptAgentDefinitionTextOptions,
    TextResponseFormatJsonSchema,
    AgentEndpoint,
    AgentEndpointProtocol,
    AgentCard,
    AgentCardSkill,
)

# Allow running from tools/ directory
sys.path.insert(0, str(Path(__file__).parent))
from deploy_helpers import get_client, get_env


CONCIERGE_SYSTEM_PROMPT = """\
You are the Bike Support Concierge for CyclePro, a friendly voice-enabled product support hotline.
Classify the user's intent and respond with valid JSON only.

Respond ONLY with valid JSON (no markdown, no extra text):
{"next_agent": "<agent>", "reason": "<short reason>", "response": "<message for the user>"}

<agent> must be one of: product-guide | support-hotline | repair-status | none

Routing rules:
• product-guide — questions about bike models, comparing bikes, buying advice, specifications,
  which bike to choose for city/mountain/children, features and pricing
• support-hotline — troubleshooting bike problems, repair help, maintenance questions,
  how-to guides for fixing brakes, gears, suspension, electric systems
• repair-status — scheduling a repair appointment, checking status of an existing repair job,
  cancelling or rescheduling a repair booking
• none — greetings, general cycling tips, questions you can answer directly

The response field:
• When routing to another agent, write a brief acknowledgement like
  "Let me check our bike catalogue for you!" or "I'll connect you with our repair team."
• When next_agent is "none", write a full friendly answer to the user's question.

Examples:
- "What mountain bikes do you have?" → product-guide
- "My disc brakes are squealing" → support-hotline
- "What is the status of repair REP-1002?" → repair-status
- "I need to book a service for my bike" → repair-status
- "How do I adjust my saddle height?" → support-hotline
- "Compare the TrailBlaster and EnduroX" → product-guide
"""

CONCIERGE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "next_agent": {
            "type": "string",
            "enum": ["product-guide", "support-hotline", "repair-status", "none"],
            "description": "The agent to route to next, or none if done.",
        },
        "reason": {
            "type": "string",
            "description": "Short reason for the routing decision.",
        },
        "response": {
            "type": "string",
            "description": "Human-friendly message to show the user.",
        },
    },
    "required": ["next_agent", "reason", "response"],
    "additionalProperties": False,
}


def deploy() -> None:
    client = get_client()
    model = get_env("AZURE_AI_MODEL_DEPLOYMENT_NAME", default="gpt-4.1-mini")

    concierge = client.agents.create_version(
        agent_name="bike-concierge",
        description="CyclePro Bike Support Concierge — classifies intent and routes to specialist agents",
        metadata={"voiceLiveCompatible": "true"},
        definition=PromptAgentDefinition(
            model=model,
            instructions=CONCIERGE_SYSTEM_PROMPT,
            temperature=0.1,
            text=PromptAgentDefinitionTextOptions(
                format=TextResponseFormatJsonSchema(
                    name="concierge_routing",
                    schema=CONCIERGE_OUTPUT_SCHEMA,
                    strict=True,
                ),
            ),
        ),
    )

    endpoint_config = AgentEndpoint(
        protocols=[
            AgentEndpointProtocol.RESPONSES,
            AgentEndpointProtocol.A2A,
            AgentEndpointProtocol.INVOCATIONS,
        ],
    )

    agent_card = AgentCard(
        description="CyclePro Bike Support Concierge — classifies intent and routes to specialist agents",
        version="1.0",
        skills=[
            AgentCardSkill(
                id="intent-routing",
                name="Intent Routing",
                description="Classifies user intent and routes to product-guide, support-hotline, or repair-status",
            ),
        ],
    )

    client.beta.agents.patch_agent_details(
        agent_name="bike-concierge",
        agent_endpoint=endpoint_config,
        agent_card=agent_card,
    )

    endpoint = get_env("AZURE_AI_PROJECT_ENDPOINT").rstrip("/")
    agent_name = "bike-concierge"
    a2a_base = f"{endpoint}/agents/{agent_name}/endpoint/protocols/a2a"
    card_url = f"{a2a_base}/agentCard/v0.3"

    print(f"\nPrompt agent '{agent_name}' created: {concierge.id}")
    print(f"A2A base path: {a2a_base}")
    print(f"Agent card URL: {card_url}")


if __name__ == "__main__":
    deploy()
