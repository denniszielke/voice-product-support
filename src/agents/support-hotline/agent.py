"""Support Hotline Agent.

Hosted agent built with the agent framework that helps customers troubleshoot
bike issues. Uses Bing Custom Web Search via the Foundry Toolbox MCP endpoint
to provide up-to-date, internet-grounded repair and maintenance guidance.
"""

from __future__ import annotations

import os

import httpx
from agent_framework import MCPStreamableHTTPTool
from agent_framework_foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv

load_dotenv()


_SYSTEM_PROMPT = """\
You are the Bike Support Hotline assistant for CyclePro Support.
Your job is to help customers troubleshoot problems with their bikes — city bikes,
mountain bikes, and children's bikes.

Use the web search tool to find accurate, up-to-date repair guides, maintenance
tips, and manufacturer recommendations for the specific problem described.

Guidelines:
- Ask the customer for the bike model and a description of the issue before searching.
- Search for relevant troubleshooting guides and repair tutorials.
- Provide step-by-step instructions when possible.
- Recommend professional repair if the issue is complex or safety-critical.
- Always cite your sources with links.
- Be patient, clear, and encouraging — not all customers are technically skilled.

Common issues you handle:
- Brake adjustment and bleeding (V-brake, hydraulic disc)
- Gear shifting problems (cable tension, derailleur alignment, indexing)
- Suspension setup and servicing (air pressure, rebound, fork oil leaks)
- Electric bike battery and motor issues
- Tyre punctures and tubeless setup
- Chain and drivetrain maintenance
- Children's bike adjustments (saddle height, training wheels, brake pads)
"""

_TOOLBOX_NAME = os.environ.get("TOOLBOX_NAME", "bikesupport-tools")
_PROJECT_ENDPOINT = os.environ["AZURE_AI_PROJECT_ENDPOINT"]
_MODEL_DEPLOYMENT = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini")

_TOOLBOX_ENDPOINT = os.environ.get("TOOLBOX_MCP_ENDPOINT") or (
    f"{_PROJECT_ENDPOINT.rstrip('/')}/toolboxes/{_TOOLBOX_NAME}/mcp?api-version=v1"
)

_credential = DefaultAzureCredential()
_token_provider = get_bearer_token_provider(_credential, "https://ai.azure.com/.default")


class _ToolboxAuth(httpx.Auth):
    """Inject a fresh Entra token on every toolbox MCP request."""

    def __init__(self, token_provider):
        self._get_token = token_provider

    def auth_flow(self, request):
        request.headers["Authorization"] = "Bearer " + self._get_token()
        yield request


_http_client = httpx.AsyncClient(
    auth=_ToolboxAuth(_token_provider),
    headers={"Foundry-Features": "Toolboxes=V1Preview"},
    timeout=120.0,
)

_mcp_tool = MCPStreamableHTTPTool(
    name="toolbox",
    url=_TOOLBOX_ENDPOINT,
    http_client=_http_client,
    load_prompts=False,
)

_chat_client = FoundryChatClient(
    project_endpoint=_PROJECT_ENDPOINT,
    model=_MODEL_DEPLOYMENT,
    credential=_credential,
)

agent = _chat_client.as_agent(
    name="support-hotline",
    instructions=_SYSTEM_PROMPT,
    tools=[_mcp_tool],
)


if __name__ == "__main__":
    ResponsesHostServer(agent).run()
