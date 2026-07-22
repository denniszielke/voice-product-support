"""FastAPI server for Voice Live API with WebRTC using Agent Invocation.

Architecture:
  Browser <──WebRTC──> Voice Live API (audio + data channel)
  Browser <──WS──> This server <──WS──> Voice Live API (signaling + session control)

The server acts as a signaling proxy:
1. Opens a control WebSocket to Voice Live API (/voice-live/realtime/calls)
2. Forwards SDP offers from the browser to the service
3. Relays SDP answers back to the browser
4. Keeps the control channel open for session.update, tool calls, etc.

Audio flows directly between the browser and Voice Live API over WebRTC
(peer-to-peer RTP). Non-audio events travel over the WebRTC data channel.

Agent Invocation:
  Uses AgentSessionConfig pattern to connect with a Foundry Agent.
  The agent encapsulates model, instructions, and voice config —
  no model deployment name is needed on the client side.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

import logging

import uvicorn
import websockets
from azure.identity.aio import DefaultAzureCredential
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Load .env from workspace root
_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_WORKSPACE_ROOT / ".env", override=True)

# ---------------------------------------------------------------------------
# Configuration — Agent Invocation API
# ---------------------------------------------------------------------------

_FOUNDRY_ENDPOINT = os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "").strip().rstrip("/")

# Bind to the `bike-support` *workflow* agent by default (deployed by
# scripts/deploy_workflow_agents.py). The workflow is what actually performs
# the handoff to the specialist agents: it calls `bike-concierge` to
# classify intent, then routes to product-guide / support-hotline /
# repair-status. Binding to `bike-concierge` alone only returns routing
# JSON and never reaches a specialist.
_DEFAULT_AGENT_NAME = "bike-support"
_AGENT_NAME = (
    os.environ.get("AZURE_AI_AGENT_NAME", "").strip()
    or os.environ.get("AZURE_AI_WORKFLOW_AGENT_NAME", "").strip()
    or _DEFAULT_AGENT_NAME
)
_AGENT_VERSION = os.environ.get("AZURE_AI_AGENT_VERSION", "").strip()
_PROJECT_NAME = os.environ.get("AZURE_AI_PROJECT_NAME", "").strip()
# Fall back to the project name embedded in the Foundry endpoint path, e.g.
# https://<resource>.services.ai.azure.com/api/projects/<project> -> <project>
if not _PROJECT_NAME and _FOUNDRY_ENDPOINT:
    _PROJECT_NAME = _FOUNDRY_ENDPOINT.rstrip("/").rsplit("/", 1)[-1]
_CONVERSATION_ID = os.environ.get("AZURE_AI_CONVERSATION_ID", "").strip()
_FOUNDRY_RESOURCE_OVERRIDE = os.environ.get("FOUNDRY_RESOURCE_OVERRIDE", "").strip()
_API_VERSION = "2026-01-01-preview"

# Voice Live WebRTC endpoint
_VOICE_LIVE_HOST = os.environ.get("AZURE_VOICELIVE_ENDPOINT", "").strip().rstrip("/")

# Startup diagnostics
logger.info("Config: AGENT_NAME=%r, PROJECT_NAME=%r, HOST=%r", _AGENT_NAME, _PROJECT_NAME, _VOICE_LIVE_HOST)


def _build_voicelive_ws_url() -> str:
    """Build the Voice Live WebSocket URL for agent invocation.

    Documented endpoint pattern:
      wss://<resource>.services.ai.azure.com/voice-live/realtime
        ?api-version=...&agent-name=...&agent-project-name=...

    For agent invocation, agent-name and agent-project-name are passed as
    query parameters (no model parameter needed).
    """
    if _VOICE_LIVE_HOST:
        host = _VOICE_LIVE_HOST.replace("https://", "").replace("http://", "").split("/")[0]
    elif _FOUNDRY_ENDPOINT:
        # Extract just the hostname from project endpoint
        # e.g. https://<resource>.services.ai.azure.com/api/projects/<project>
        host = _FOUNDRY_ENDPOINT.replace("https://", "").replace("http://", "").split("/")[0]
    else:
        raise RuntimeError(
            "Set AZURE_VOICELIVE_ENDPOINT or AZURE_AI_PROJECT_ENDPOINT"
        )

    # Build query parameters for agent invocation
    # model is required for WebRTC /calls endpoint (Voice Live managed model)
    params: dict[str, str] = {"api-version": _API_VERSION, "model": "gpt-realtime"}
    if _AGENT_NAME:
        params["agent-name"] = _AGENT_NAME
    if _PROJECT_NAME:
        params["agent-project-name"] = _PROJECT_NAME
    if _AGENT_VERSION:
        params["agent-version"] = _AGENT_VERSION
    if _CONVERSATION_ID:
        params["conversation_id"] = _CONVERSATION_ID
    if _FOUNDRY_RESOURCE_OVERRIDE:
        params["foundry_resource_override"] = _FOUNDRY_RESOURCE_OVERRIDE

    url = f"wss://{host}/voice-live/realtime/calls?{urlencode(params)}"
    return url


async def _get_auth_token() -> str:
    """Get a bearer token for the Voice Live API (Entra ID required for agent invocation)."""
    credential = DefaultAzureCredential()
    # Agent invocation requires a token scoped to the AI Foundry Agent
    # service audience (https://ai.azure.com), not cognitiveservices.
    token = await credential.get_token(
        "https://ai.azure.com/.default"
    )
    await credential.close()
    return token.token


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="CyclePro Voice Guide - WebRTC (Voice Live Agent Invocation)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "agent_name": _AGENT_NAME or None,
        "project_name": _PROJECT_NAME or None,
    }


@app.get("/")
async def index():
    """Serve the browser client."""
    return FileResponse(Path(__file__).parent / "index.html")


# ---------------------------------------------------------------------------
# WebSocket signaling endpoint
# ---------------------------------------------------------------------------


@app.websocket("/ws")
async def signaling_ws(ws: WebSocket):
    """Signaling relay between browser and Voice Live API.

    Protocol (browser -> server):
      {"type": "rtc.call.sdp.create", "sdp_offer": "..."}
      {"type": "session.update", "session": {...}}
      {"type": "conversation.item.create", ...}

    Protocol (server -> browser):
      {"type": "rtc.call.sdp.created", "sdp_answer": "..."}
      {"type": "session.created", ...}
      {"type": "session.updated", ...}
      {"type": "response.function_call_arguments.done", ...}
    """
    await ws.accept()
    logger.info("Browser signaling WebSocket connected")

    # Get auth token and build Voice Live WebSocket URL (agent invocation)
    try:
        token = await _get_auth_token()
        voicelive_url = _build_voicelive_ws_url()
    except Exception as exc:
        logger.error(f"Failed to build connection: {exc}")
        await ws.send_json({"type": "error", "message": str(exc)})
        await ws.close()
        return

    logger.info(
        "Connecting to Voice Live API (agent invocation): agent=%s, project=%s, version=%s",
        _AGENT_NAME,
        _PROJECT_NAME,
        _AGENT_VERSION or "latest",
    )
    logger.info(f"Voice Live URL: {voicelive_url}")

    # Connect to Voice Live API control channel
    headers = {"Authorization": f"Bearer {token}"}
    try:
        service_ws = await websockets.connect(
            voicelive_url,
            additional_headers=headers,
            max_size=None,
        )
    except websockets.exceptions.InvalidStatus as exc:
        body = exc.response.body.decode() if exc.response.body else ""
        logger.error(
            "Voice Live API rejected connection: HTTP %s\nURL: %s\nResponse: %s",
            exc.response.status_code,
            voicelive_url,
            body,
        )
        await ws.send_json({
            "type": "error",
            "message": f"Service connection failed: HTTP {exc.response.status_code} - {body}",
        })
        await ws.close()
        return
    except Exception as exc:
        logger.error(f"Failed to connect to Voice Live API: {exc}")
        await ws.send_json({"type": "error", "message": f"Service connection failed: {exc}"})
        await ws.close()
        return

    logger.info("Connected to Voice Live API control channel")

    # Relay messages bidirectionally
    async def _relay_service_to_browser():
        """Forward messages from Voice Live API to browser."""
        try:
            async for raw in service_ws:
                msg = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())
                msg_type = msg.get("type", "")
                logger.debug(f"Service -> Browser: {msg_type}")
                await ws.send_json(msg)
        except websockets.exceptions.ConnectionClosed:
            logger.info("Voice Live API WebSocket closed")
        except WebSocketDisconnect:
            logger.info("Browser disconnected while relaying from service")
        except Exception as exc:
            logger.error(f"Error relaying service->browser: {exc}")

    async def _relay_browser_to_service():
        """Forward messages from browser to Voice Live API."""
        try:
            while True:
                data = await ws.receive_text()
                msg = json.loads(data)
                msg_type = msg.get("type", "")
                logger.debug(f"Browser -> Service: {msg_type}")
                await service_ws.send(json.dumps(msg))
        except WebSocketDisconnect:
            logger.info("Browser signaling WebSocket disconnected")
        except Exception as exc:
            logger.error(f"Error relaying browser->service: {exc}")

    # Run both relay tasks concurrently
    relay_task = asyncio.gather(
        _relay_service_to_browser(),
        _relay_browser_to_service(),
        return_exceptions=True,
    )

    try:
        await relay_task
    finally:
        # Clean up
        try:
            await service_ws.close()
        except Exception:
            pass
        logger.info("Signaling session ended")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("WEBRTCLIVE_PORT", "8090"))
    logger.info(f"Starting WebRTC Voice Live server on port {port} (agent: {_AGENT_NAME})")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
