"""FastAPI server for Voice Live API with WebRTC.

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
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Optional

import logging

import uvicorn
import websockets
from azure.identity.aio import DefaultAzureCredential
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

# Load .env from workspace root
_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_WORKSPACE_ROOT / ".env", override=True)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_FOUNDRY_ENDPOINT = os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "").rstrip("/")
_MODEL = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4o-realtime")
_AGENT_NAME = os.environ.get("AZURE_AI_AGENT_NAME", "")
_PROJECT_NAME = os.environ.get("AZURE_AI_PROJECT_NAME", "")
_API_VERSION = "2026-01-01-preview"

# Voice Live WebRTC endpoint uses /voice-live/realtime/calls
# Build the base from the project endpoint (strip /api/projects/<name> if present)
_VOICE_LIVE_HOST = os.environ.get("AZURE_VOICELIVE_ENDPOINT", "").rstrip("/")


def _build_voicelive_ws_url() -> str:
    """Build the Voice Live WebRTC WebSocket URL.

    Documented pattern:
      wss://<resource>.services.ai.azure.com/voice-live/realtime/calls
        ?api-version=...&model=...&agent_name=...&project_name=...
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

    url = f"wss://{host}/voice-live/realtime/calls?api-version={_API_VERSION}"
    if _MODEL:
        url += f"&model={_MODEL}"
    if _AGENT_NAME:
        url += f"&agent_name={_AGENT_NAME}"
    if _PROJECT_NAME:
        url += f"&project_name={_PROJECT_NAME}"
    return url


async def _get_auth_token() -> str:
    """Get a bearer token for the Voice Live API."""
    credential = DefaultAzureCredential()
    token = await credential.get_token(
        "https://cognitiveservices.azure.com/.default"
    )
    await credential.close()
    return token.token


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="CyclePro Voice Guide - WebRTC (Voice Live)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


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

    # Get auth token and build Voice Live WebSocket URL
    try:
        token = await _get_auth_token()
        voicelive_url = _build_voicelive_ws_url()
    except Exception as exc:
        logger.error(f"Failed to build connection: {exc}")
        await ws.send_json({"type": "error", "message": str(exc)})
        await ws.close()
        return

    logger.info(f"Connecting to Voice Live API: {voicelive_url}")

    # Connect to Voice Live API control channel
    headers = {"Authorization": f"Bearer {token}"}
    try:
        service_ws = await websockets.connect(
            voicelive_url,
            additional_headers=headers,
            max_size=None,
        )
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
        if not service_ws.closed:
            await service_ws.close()
        logger.info("Signaling session ended")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("WEBRTCLIVE_PORT", "8090"))
    logger.info(f"Starting WebRTC Voice Live server on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
