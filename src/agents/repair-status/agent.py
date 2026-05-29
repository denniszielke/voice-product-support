"""Repair Status Agent.

Hosted agent built with LangGraph that handles:
- Checking the status of an existing bike repair job
- Scheduling a new repair appointment
- Updating or cancelling a repair booking

Uses in-memory repair job data from src/data/bikes.py.
"""

import asyncio
import os
import logging
import sys
from pathlib import Path
from datetime import datetime, timedelta

from dotenv import load_dotenv
import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import (
    END,
    START,
    MessagesState,
    StateGraph,
)
from typing_extensions import Literal
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

from azure.ai.agentserver.responses import (
    CreateResponse,
    ResponseContext,
    ResponsesAgentServerHost,
    ResponsesServerOptions,
    TextResponse,
)
from azure.monitor.opentelemetry import configure_azure_monitor

# Allow imports from src root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

load_dotenv()

if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    configure_azure_monitor(enable_live_metrics=True, logger_name="__main__")
    logging.getLogger("azure").setLevel(logging.WARNING)

deployment_name = os.environ.get("MODEL_DEPLOYMENT_NAME") or os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"]
project_endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT") or os.environ["AZURE_AI_PROJECT_ENDPOINT"]

_token_provider = get_bearer_token_provider(
    DefaultAzureCredential(), "https://ai.azure.com/.default"
)


class _AzureTokenAuth(httpx.Auth):
    """Inject a fresh Entra token on every request to the Foundry OpenAI endpoint."""

    def auth_flow(self, request):
        request.headers["Authorization"] = "Bearer " + _token_provider()
        yield request


try:
    llm = ChatOpenAI(
        base_url=f"{project_endpoint}/openai/v1",
        api_key="placeholder",  # overridden by _AzureTokenAuth
        model=deployment_name,
        use_responses_api=True,
        http_client=httpx.Client(auth=_AzureTokenAuth()),
    )
except Exception:
    logger.exception("Repair Status Agent failed to start")
    raise


# ---------------------------------------------------------------------------
# In-memory repair job store (imported from shared data module)
# ---------------------------------------------------------------------------
from data.bikes import REPAIR_JOBS, get_next_job_id


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def get_repair_status(job_id: str) -> dict:
    """Get the status of an existing repair job.

    Args:
        job_id: The repair job ID (e.g. 'REP-1001')
    """
    job_id_upper = job_id.strip().upper()
    job = REPAIR_JOBS.get(job_id_upper)
    if not job:
        return {
            "found": False,
            "job_id": job_id_upper,
            "message": f"No repair job found with ID {job_id_upper}. Please check the job ID and try again.",
        }
    return {"found": True, **job}


@tool
def list_repair_jobs_for_customer(customer_name: str) -> dict:
    """Find all repair jobs for a given customer name.

    Args:
        customer_name: The customer's full name (partial match supported)
    """
    name_lower = customer_name.lower()
    matches = [
        job for job in REPAIR_JOBS.values()
        if name_lower in job["customer_name"].lower()
    ]
    if not matches:
        return {
            "found": False,
            "customer_name": customer_name,
            "message": f"No repair jobs found for customer '{customer_name}'.",
            "jobs": [],
        }
    return {
        "found": True,
        "customer_name": customer_name,
        "job_count": len(matches),
        "jobs": matches,
    }


@tool
def schedule_repair(
    customer_name: str,
    bike_model: str,
    issue_description: str,
    preferred_date: str,
) -> dict:
    """Schedule a new bike repair appointment.

    Args:
        customer_name: Full name of the customer
        bike_model: Model name of the bike (e.g. 'CityRider Pro')
        issue_description: Description of the problem or service required
        preferred_date: Preferred date for the repair (YYYY-MM-DD format)
    """
    # Validate and adjust date — must be a future weekday
    try:
        requested = datetime.strptime(preferred_date, "%Y-%m-%d")
    except ValueError:
        # Default to next Monday if date is invalid
        today = datetime.today()
        days_ahead = 7 - today.weekday()
        requested = today + timedelta(days=days_ahead)

    today = datetime.today()
    if requested <= today:
        # Schedule for next available weekday
        requested = today + timedelta(days=1)
        while requested.weekday() >= 5:  # Skip weekends
            requested += timedelta(days=1)

    scheduled_date = requested.strftime("%Y-%m-%d")
    job_id = get_next_job_id()
    mechanics = ["Jonas Weber", "Maria Hoffman"]
    mechanic = mechanics[len(REPAIR_JOBS) % len(mechanics)]

    new_job = {
        "job_id": job_id,
        "customer_name": customer_name,
        "bike_model": bike_model,
        "bike_id": None,
        "issue": issue_description,
        "status": "scheduled",
        "scheduled_date": scheduled_date,
        "completion_date": None,
        "mechanic": mechanic,
        "estimated_cost_eur": None,
        "actual_cost_eur": None,
        "notes": "Newly booked via support hotline.",
    }
    REPAIR_JOBS[job_id] = new_job

    return {
        "success": True,
        "job_id": job_id,
        "customer_name": customer_name,
        "bike_model": bike_model,
        "issue": issue_description,
        "scheduled_date": scheduled_date,
        "mechanic": mechanic,
        "status": "scheduled",
        "message": (
            f"Repair appointment booked! Your job ID is {job_id}. "
            f"Please bring your bike to the workshop on {scheduled_date}. "
            f"Your mechanic will be {mechanic}."
        ),
    }


@tool
def cancel_repair(job_id: str, reason: str = "") -> dict:
    """Cancel an existing repair appointment.

    Args:
        job_id: The repair job ID (e.g. 'REP-1004')
        reason: Optional reason for cancellation
    """
    job_id_upper = job_id.strip().upper()
    job = REPAIR_JOBS.get(job_id_upper)
    if not job:
        return {
            "success": False,
            "job_id": job_id_upper,
            "message": f"No repair job found with ID {job_id_upper}.",
        }
    if job["status"] == "completed":
        return {
            "success": False,
            "job_id": job_id_upper,
            "message": f"Job {job_id_upper} is already completed and cannot be cancelled.",
        }
    job["status"] = "cancelled"
    job["notes"] = (job.get("notes") or "") + f" Cancelled by customer. Reason: {reason}".strip()
    return {
        "success": True,
        "job_id": job_id_upper,
        "message": f"Repair job {job_id_upper} has been cancelled. We hope to see you again soon!",
    }


@tool
def get_available_slots(date_from: str, date_to: str) -> dict:
    """Get available repair appointment slots between two dates.

    Args:
        date_from: Start date (YYYY-MM-DD)
        date_to: End date (YYYY-MM-DD)
    """
    try:
        start = datetime.strptime(date_from, "%Y-%m-%d")
        end = datetime.strptime(date_to, "%Y-%m-%d")
    except ValueError:
        today = datetime.today()
        start = today
        end = today + timedelta(days=14)

    slots = []
    current = start
    while current <= end:
        if current.weekday() < 5:  # Monday–Friday
            # Each day has morning and afternoon slots
            slots.append({
                "date": current.strftime("%Y-%m-%d"),
                "day": current.strftime("%A"),
                "slots": ["09:00-11:00", "14:00-16:00"],
            })
        current += timedelta(days=1)

    return {
        "date_from": date_from,
        "date_to": date_to,
        "available_slots": slots[:10],  # Return up to 10 days
        "note": "Appointments are available Monday to Friday. Saturday appointments available on request.",
    }


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

tools = [
    get_repair_status,
    list_repair_jobs_for_customer,
    schedule_repair,
    cancel_repair,
    get_available_slots,
]
tools_by_name = {t.name: t for t in tools}
llm_with_tools = llm.bind_tools(tools)

SYSTEM_MESSAGE = SystemMessage(
    content="""\
You are the Repair Status Hotline for CyclePro Support, a bike repair workshop assistant.
Help customers with:
- Checking the status of their existing repair jobs
- Scheduling new repair appointments
- Cancelling or rescheduling existing appointments
- Finding available appointment slots

Guidelines:
- Always ask for the job ID when checking repair status.
- If the customer doesn't know their job ID, offer to search by customer name.
- When scheduling, confirm all details (bike model, issue, date) before booking.
- Always provide the job ID after scheduling so the customer can reference it.
- Be empathetic when bikes are taking longer than expected to repair.
- Mention estimated costs where available.
- Workshop hours: Monday–Friday 8:00–18:00, Saturday 9:00–14:00.
"""
)


def llm_call(state: MessagesState):
    return {
        "messages": [
            llm_with_tools.invoke([SYSTEM_MESSAGE] + state["messages"])
        ]
    }


def tool_node(state: dict):
    result = []
    for tool_call in state["messages"][-1].tool_calls:
        t = tools_by_name[tool_call["name"]]
        observation = t.invoke(tool_call["args"])
        result.append(ToolMessage(content=observation, tool_call_id=tool_call["id"]))
    return {"messages": result}


def should_continue(state: MessagesState) -> Literal["environment", "__end__"]:
    last_message = state["messages"][-1]
    if last_message.tool_calls:
        return "Action"
    return END


def build_agent() -> "StateGraph":
    agent_builder = StateGraph(MessagesState)

    agent_builder.add_node("llm_call", llm_call)
    agent_builder.add_node("environment", tool_node)

    agent_builder.add_edge(START, "llm_call")
    agent_builder.add_conditional_edges(
        "llm_call",
        should_continue,
        {"Action": "environment", END: END},
    )
    agent_builder.add_edge("environment", "llm_call")

    return agent_builder.compile()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

graph = build_agent()

app = ResponsesAgentServerHost(
    options=ResponsesServerOptions(default_fetch_history_count=20)
)


@app.response_handler
async def handle(
    request: CreateResponse,
    context: ResponseContext,
    cancellation_signal: asyncio.Event,
):
    async def run_graph():
        try:
            history = await context.get_history()
        except Exception:
            history = []
        user_input = await context.get_input_text() or ""

        lc_messages: list = []
        for item in history:
            if hasattr(item, "content"):
                for c in item.content:
                    if hasattr(c, "text") and c.text:
                        if item.role == "user":
                            lc_messages.append(HumanMessage(content=c.text))
                        else:
                            lc_messages.append(AIMessage(content=c.text))
        lc_messages.append(HumanMessage(content=user_input))

        result = await graph.ainvoke({"messages": lc_messages})
        raw = result["messages"][-1].content
        if isinstance(raw, list):
            yield "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in raw
            )
        else:
            yield raw or ""

    return TextResponse(context, request, text=run_graph())


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", type=str, default=None, help="Run a single query and exit")
    args = parser.parse_args()

    try:
        if args.query:
            result = graph.invoke({"messages": [HumanMessage(content=args.query)]})
            for msg in result["messages"]:
                print(f"{msg.type}: {msg.content}")
        else:
            app.run()
    except Exception:
        logger.exception("Repair Status Agent encountered an error while running")
        raise
