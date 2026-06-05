"""Local web server that bridges a browser WebRTC client with two backends:

1. **Azure Voice Live (WebRTC)** — for STT (microphone) and TTS (speaker)
   using a realtime model (e.g. ``gpt-realtime``). The browser opens a
   WebRTC peer connection directly to Voice Live; this server only proxies
   the SDP signaling + control WebSocket so the Authorization header can be
   attached (browsers cannot set custom headers on a ``WebSocket``).

2. **The local bike-renting agent's ``/invocations`` endpoint** (default
   ``http://localhost:8088/invocations``). When the user finishes speaking
   the browser POSTs the transcript here; this server forwards it to the
   bike-renting agent, parses the SSE stream, and returns the spoken reply
   text + custom ``ui.*`` events as JSON.

The browser then asks Voice Live to speak the reply (by injecting a text
item + ``response.create``) and renders the UI events as cards.

Voice Live is configured with ``turn_detection.create_response = false`` so
it never invents its own reply — it acts purely as a STT + TTS pipeline.

References:
    https://learn.microsoft.com/azure/ai-services/speech-service/voice-live-webrtc
    https://github.com/microsoft-foundry/foundry-samples/tree/main/samples/python/hosted-agents/bring-your-own/invocations_ws/hello-world
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import uvicorn
import websockets
from azure.identity.aio import DefaultAzureCredential
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Load .env from the workspace root if present
_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_WORKSPACE_ROOT / ".env", override=False)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Voice Live host — accepts a full ``https://<account>.services.ai.azure.com``
# URL or a bare host. The realtime call path is appended below.
_VOICE_LIVE_HOST = (
    os.environ.get("AZURE_VOICELIVE_ENDPOINT", "").strip().rstrip("/")
    or os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "").strip().rstrip("/")
)

_VOICE_LIVE_MODEL = os.environ.get("AZURE_VOICELIVE_MODEL", "gpt-realtime").strip()
_VOICE_LIVE_API_VERSION = os.environ.get(
    "AZURE_VOICELIVE_API_VERSION", "2026-01-01-preview"
).strip()
_VOICE_LIVE_VOICE = os.environ.get(
    "AZURE_VOICELIVE_VOICE", "en-US-Ava:DragonHDLatestNeural"
).strip()

# The local invocations endpoint to forward each user turn to.
_INVOCATION_URL = os.environ.get(
    "INVOCATION_URL", "http://localhost:8088/invocations"
).strip()

logger.info(
    "Config: voice_live_host=%r model=%r voice=%r invocation_url=%r",
    _VOICE_LIVE_HOST,
    _VOICE_LIVE_MODEL,
    _VOICE_LIVE_VOICE,
    _INVOCATION_URL,
)


def _build_voicelive_ws_url() -> str:
    """Build the Voice Live ``/voice-live/realtime/calls`` WebSocket URL."""
    if not _VOICE_LIVE_HOST:
        raise RuntimeError(
            "Set AZURE_VOICELIVE_ENDPOINT to your AI Services account URL, "
            "e.g. https://<account>.services.ai.azure.com"
        )
    host = (
        _VOICE_LIVE_HOST.replace("https://", "")
        .replace("http://", "")
        .split("/", 1)[0]
    )
    params = {
        "api-version": _VOICE_LIVE_API_VERSION,
        "model": _VOICE_LIVE_MODEL,
    }
    return f"wss://{host}/voice-live/realtime/calls?{urlencode(params)}"


async def _get_auth_token() -> str:
    """Acquire an Entra token for the Voice Live service."""
    credential = DefaultAzureCredential()
    try:
        token = await credential.get_token(
            "https://cognitiveservices.azure.com/.default"
        )
        return token.token
    finally:
        await credential.close()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="CyclePro Bike Renting — WebRTC voice bridge")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "voice_live_host": _VOICE_LIVE_HOST or None,
        "voice_live_model": _VOICE_LIVE_MODEL,
        "voice": _VOICE_LIVE_VOICE,
        "invocation_url": _INVOCATION_URL,
    }


@app.get("/config")
async def config() -> dict[str, Any]:
    """Frontend bootstrap — voice name, instructions, invocation URL."""
    return {
        "voice": _VOICE_LIVE_VOICE,
        "invocation_url_label": _INVOCATION_URL,
    }


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(Path(__file__).parent / "index.html")


# ---------------------------------------------------------------------------
# Invocation bridge — POST /invoke
# ---------------------------------------------------------------------------


class InvokeRequest(BaseModel):
    session_id: str
    # Exactly one of these must be set.
    transcript: str | None = None
    payload: dict[str, Any] | None = None


class InvokeResponse(BaseModel):
    reply: str
    ui_events: list[dict[str, Any]]


@app.post("/invoke", response_model=InvokeResponse)
async def invoke(req: InvokeRequest) -> InvokeResponse:
    """Forward a user turn (transcript or click action) to the bike-renting
    ``/invocations`` endpoint and collect the SSE response."""
    if req.payload is not None:
        payload = req.payload
    elif req.transcript and req.transcript.strip():
        payload = {"type": "input_audio.transcription", "input": req.transcript}
    else:
        raise HTTPException(
            status_code=400, detail="either `transcript` or `payload` is required"
        )

    url = f"{_INVOCATION_URL}?agent_session_id={req.session_id}"
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}

    reply_parts: list[str] = []
    reply_done: str | None = None
    ui_events: list[dict[str, Any]] = []

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=120.0)) as http:
            async with http.stream("POST", url, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                async for raw in resp.aiter_lines():
                    if not raw or not raw.startswith("data:"):
                        continue
                    data = raw[len("data:") :].strip()
                    if not data:
                        continue
                    try:
                        evt = json.loads(data)
                    except json.JSONDecodeError:
                        logger.warning("Could not parse SSE line: %r", data)
                        continue
                    etype = evt.get("type", "")
                    if etype == "output_audio_transcription.delta":
                        reply_parts.append(evt.get("delta", ""))
                    elif etype == "output_audio_transcription.done":
                        reply_done = evt.get("text", "")
                    elif etype == "done":
                        break
                    else:
                        ui_events.append(evt)
    except httpx.HTTPError as exc:
        logger.exception("Invocation call failed")
        raise HTTPException(status_code=502, detail=f"invocation failed: {exc}") from exc

    reply = reply_done if reply_done is not None else "".join(reply_parts)
    return InvokeResponse(reply=reply, ui_events=ui_events)


# ---------------------------------------------------------------------------
# Signaling proxy — /ws
# ---------------------------------------------------------------------------


@app.websocket("/ws")
async def signaling_ws(ws: WebSocket) -> None:
    """Two-way relay between the browser and Voice Live's control channel.

    Browser frames are forwarded verbatim to Voice Live, and vice versa. The
    server's only job is to attach the ``Authorization: Bearer <token>``
    header on the upstream connection.
    """
    await ws.accept()
    logger.info("Browser signaling WebSocket connected")

    try:
        token = await _get_auth_token()
        voicelive_url = _build_voicelive_ws_url()
    except Exception as exc:
        logger.exception("Failed to prepare Voice Live connection")
        try:
            await ws.send_json({"type": "error", "message": str(exc)})
        finally:
            await ws.close()
        return

    logger.info("Connecting to Voice Live: %s", voicelive_url)
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
            "Voice Live rejected connection: HTTP %s — %s",
            exc.response.status_code,
            body,
        )
        await ws.send_json(
            {
                "type": "error",
                "message": (
                    f"Voice Live HTTP {exc.response.status_code} — {body}"
                ),
            }
        )
        await ws.close()
        return
    except Exception as exc:
        logger.exception("Failed to connect to Voice Live")
        await ws.send_json({"type": "error", "message": f"upstream failed: {exc}"})
        await ws.close()
        return

    logger.info("Connected to Voice Live control channel")

    async def _service_to_browser() -> None:
        try:
            async for raw in service_ws:
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode("utf-8", errors="replace")
                await ws.send_text(raw)
        except websockets.exceptions.ConnectionClosed:
            logger.info("Voice Live closed the connection")
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("service→browser relay failed")

    async def _browser_to_service() -> None:
        try:
            while True:
                data = await ws.receive_text()
                await service_ws.send(data)
        except WebSocketDisconnect:
            logger.info("Browser signaling WebSocket disconnected")
        except Exception:
            logger.exception("browser→service relay failed")

    try:
        await asyncio.gather(
            _service_to_browser(),
            _browser_to_service(),
            return_exceptions=True,
        )
    finally:
        try:
            await service_ws.close()
        except Exception:
            pass
        logger.info("Signaling session ended")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("WEBRTC_BIKE_PORT", "8095"))
    logger.info(
        "Starting bike-rental WebRTC voice bridge on http://0.0.0.0:%d", port
    )
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
