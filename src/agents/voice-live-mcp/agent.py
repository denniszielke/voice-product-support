#!/usr/bin/env python
# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------
"""
FILE: agent.py

DESCRIPTION:
    Voice-Live agent that uses the bike-rental MCP server as its tool
    backend. The Azure VoiceLive service handles speech-in/speech-out and
    asks the MCP server (see `src/mcp-server-bike-rental/server.py`) to run
    tools for searching the rental fleet, reserving a bike, and confirming
    a booking.

USAGE:
    python agent.py \\
        --endpoint wss://api.voicelive.com/v1 \\
        --mcp-server-url https://<public-host>/mcp

    Command-line arguments:
      --endpoint           Azure VoiceLive endpoint (required)
      --mcp-server-url     Publicly-reachable URL of the bike-rental MCP server
                           (required; VoiceLive must be able to reach it)
      --mcp-server-label   Human-readable label sent in MCP events
                           (default: bike-rental)
      --voice              Azure neural voice (default:
                           en-US-Ava:DragonHDLatestNeural)
      --model              VoiceLive model (default: gpt-realtime)
      --require-approval   MCP approval policy: 'never' or 'always'
                           (default: never; the booking-confirm tool already
                           has explicit user-in-the-loop in conversation)

    Environment variables (fall back when CLI flag is omitted):
      AZURE_VOICELIVE_ENDPOINT
      AZURE_VOICELIVE_MODEL
      AZURE_VOICELIVE_USE_API_KEY  ("true" to use API key instead of Entra)
      AZURE_VOICELIVE_API_KEY
      AZURE_VOICELIVE_VOICE        (e.g. en-US-Ava:DragonHDLatestNeural)
      BIKE_RENTAL_MCP_URL          publicly-reachable MCP /mcp endpoint
      BIKE_RENTAL_MCP_LABEL        server label sent in MCP events
      BIKE_RENTAL_MCP_REQUIRE_APPROVAL  'never' | 'always' (default: never)

LOCAL DEVELOPMENT
    Azure VoiceLive runs in the cloud, so it cannot reach
    `http://localhost:8000/mcp` directly. For local testing, expose the MCP
    server with a tunnel, e.g.::

        # In one terminal -- run the MCP server:
        python src/mcp-server-bike-rental/server.py

        # In another terminal -- expose it publicly:
        devtunnel host -p 8000 --allow-anonymous
        # or: ngrok http 8000

        # Then run this agent with the tunnel URL:
        python src/agents/voice-live-mcp/agent.py \\
            --endpoint $AZURE_VOICELIVE_ENDPOINT \\
            --mcp-server-url https://<tunnel-host>/mcp

REQUIREMENTS:
    - azure-ai-voicelive (>=1.3.0b1, for MCP support)
    - azure-identity
    - python-dotenv
    - pyaudio (for microphone capture and speaker playback)
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import os
import queue
import signal
import sys
from typing import Literal, Optional, Union, cast

from dotenv import load_dotenv

load_dotenv()

# Audio processing imports
try:
    import pyaudio
except ImportError:
    print("This agent requires pyaudio. Install with:")
    print("  Linux: sudo apt-get install -y portaudio19-dev libasound2-dev && pip install pyaudio")
    print("  macOS: brew install portaudio && pip install pyaudio")
    print("  Windows: pip install pyaudio")
    sys.exit(1)

# Azure VoiceLive SDK imports
from azure.ai.voicelive.aio import VoiceLiveConnection, connect
from azure.ai.voicelive.models import (
    AudioEchoCancellation,
    AudioInputTranscriptionOptions,
    AudioNoiseReduction,
    AzureStandardVoice,
    InputAudioFormat,
    ItemType,
    LlmInterimResponseConfig,
    MCPApprovalResponseRequestItem,
    MCPServer,
    Modality,
    OutputAudioFormat,
    RequestSession,
    ResponseMCPApprovalRequestItem,
    ResponseMCPCallItem,
    ServerEventConversationItemCreated,
    ServerEventType,
    ServerVad,
    Tool,
    ToolChoiceLiteral,
)
from azure.core.credentials import AzureKeyCredential
from azure.core.credentials_async import AsyncTokenCredential
from azure.identity.aio import DefaultAzureCredential

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Instructions for the voice model
# ---------------------------------------------------------------------------

DEFAULT_INSTRUCTIONS = """\
You are the friendly voice concierge for CyclePro Rentals. Your goal is to help
customers pick the right rental bike, place a 30-minute reservation hold, and
confirm the final booking. You have access to a bike-rental MCP server with
these tools (call them by name when needed):

  - list_categories       — quick overview of available bike categories
  - search_bikes          — find bikes by query / category / max price
  - get_bike              — full details and a price quote for N days
  - reserve_bike          — place a 30-minute hold (returns RES-... code)
  - confirm_booking       — finalise a reservation (returns BK-... code)
  - cancel_reservation    — release a held reservation
  - get_reservation, get_booking — look up by code

Conversation rules:
  * Keep replies short and conversational — you are speaking aloud, not writing.
  * When you list bikes, name at most three and quote prices in euros per day.
  * Before reserving, confirm the bike, the number of rental days, and read
    back the total (incl. deposit) for the customer.
  * Before confirming a booking, ask explicitly: "Shall I confirm the
    booking?" Wait for a clear yes.
  * Always speak the reservation or confirmation code back so the customer
    can write it down.
  * If a tool returns an error, apologise briefly and offer an alternative
    (e.g. another bike or fewer rental days).
"""


# ---------------------------------------------------------------------------
# Audio processor (callback-based, mirrors src/voice/client.py)
# ---------------------------------------------------------------------------


class AudioProcessor:
    """Real-time microphone capture and speaker playback for the voice agent."""

    loop: asyncio.AbstractEventLoop

    class AudioPlaybackPacket:
        def __init__(self, seq_num: int, data: Optional[bytes]):
            self.seq_num = seq_num
            self.data = data

    def __init__(self, connection: VoiceLiveConnection):
        self.connection = connection
        self.audio = pyaudio.PyAudio()

        # Audio configuration -- PCM16, 24 kHz, mono (VoiceLive requirement)
        self.format = pyaudio.paInt16
        self.channels = 1
        self.rate = 24000
        self.chunk_size = 1200  # 50 ms chunks

        self.input_stream: Optional[pyaudio.Stream] = None

        # Playback uses sequence numbers so we can drop in-flight audio on barge-in.
        self.playback_queue: queue.Queue[AudioProcessor.AudioPlaybackPacket] = queue.Queue()
        self.playback_base = 0
        self.next_seq_num = 0
        self.output_stream: Optional[pyaudio.Stream] = None

        logger.info("AudioProcessor initialized (24kHz PCM16 mono)")

    # --- capture --------------------------------------------------------

    def start_capture(self):
        def _capture_callback(in_data, _frame_count, _time_info, _status_flags):
            audio_base64 = base64.b64encode(in_data).decode("utf-8")
            future = asyncio.run_coroutine_threadsafe(
                self.connection.input_audio_buffer.append(audio=audio_base64),
                self.loop,
            )
            future.add_done_callback(
                lambda f: logger.error("Audio buffer append error: %s", f.exception())
                if f.exception()
                else None
            )
            return (None, pyaudio.paContinue)

        if self.input_stream:
            return

        self.loop = asyncio.get_running_loop()
        self.input_stream = self.audio.open(
            format=self.format,
            channels=self.channels,
            rate=self.rate,
            input=True,
            frames_per_buffer=self.chunk_size,
            stream_callback=_capture_callback,
        )
        logger.info("Started audio capture")

    # --- playback -------------------------------------------------------

    def start_playback(self):
        if self.output_stream:
            return

        remaining = bytes()

        def _playback_callback(_in_data, frame_count, _time_info, _status_flags):
            nonlocal remaining
            frame_count *= pyaudio.get_sample_size(pyaudio.paInt16)

            out = remaining[:frame_count]
            remaining_local = remaining[frame_count:]

            while len(out) < frame_count:
                try:
                    packet = self.playback_queue.get_nowait()
                except queue.Empty:
                    out = out + bytes(frame_count - len(out))
                    continue

                if not packet or not packet.data:
                    break

                if packet.seq_num < self.playback_base:
                    # Caller asked for a flush -- drop everything we'd kept around.
                    if len(remaining_local) > 0:
                        remaining_local = bytes()
                    continue

                num_to_take = frame_count - len(out)
                out = out + packet.data[:num_to_take]
                remaining_local = packet.data[num_to_take:]

            remaining = remaining_local
            if len(out) >= frame_count:
                return (out, pyaudio.paContinue)
            return (out, pyaudio.paComplete)

        self.output_stream = self.audio.open(
            format=self.format,
            channels=self.channels,
            rate=self.rate,
            output=True,
            frames_per_buffer=self.chunk_size,
            stream_callback=_playback_callback,
        )
        logger.info("Audio playback ready")

    def _get_and_increase_seq_num(self) -> int:
        seq = self.next_seq_num
        self.next_seq_num += 1
        return seq

    def queue_audio(self, audio_data: Optional[bytes]) -> None:
        self.playback_queue.put(
            AudioProcessor.AudioPlaybackPacket(
                seq_num=self._get_and_increase_seq_num(),
                data=audio_data,
            )
        )

    def skip_pending_audio(self) -> None:
        """Drop everything currently queued for playback (used on barge-in)."""
        self.playback_base = self._get_and_increase_seq_num()

    def shutdown(self):
        if self.input_stream:
            self.input_stream.stop_stream()
            self.input_stream.close()
            self.input_stream = None
        if self.output_stream:
            self.skip_pending_audio()
            self.queue_audio(None)
            self.output_stream.stop_stream()
            self.output_stream.close()
            self.output_stream = None
        if self.audio:
            self.audio.terminate()
        logger.info("Audio processor cleaned up")


# ---------------------------------------------------------------------------
# Voice-Live MCP client
# ---------------------------------------------------------------------------


class BikeRentalVoiceLiveMCPAgent:
    """Voice-Live agent backed by the bike-rental MCP server."""

    def __init__(
        self,
        *,
        endpoint: str,
        credential: Union[AzureKeyCredential, AsyncTokenCredential],
        model: str,
        voice: str,
        mcp_server_url: str,
        mcp_server_label: str,
        require_approval: Literal["never", "always"],
        instructions: str,
    ) -> None:
        self.endpoint = endpoint
        self.credential = credential
        self.model = model
        self.voice = voice
        self.mcp_server_url = mcp_server_url
        self.mcp_server_label = mcp_server_label
        self.require_approval: Literal["never", "always"] = require_approval
        self.instructions = instructions

        self.connection: Optional[VoiceLiveConnection] = None
        self.audio_processor: Optional[AudioProcessor] = None
        self.session_id: Optional[str] = None

    # --- entry point ----------------------------------------------------

    async def start(self) -> None:
        try:
            logger.info(
                "Connecting to VoiceLive at %s using model %s", self.endpoint, self.model,
            )
            # API version 2026-04-10 is required for MCP tool support.
            async with connect(
                endpoint=self.endpoint,
                credential=self.credential,
                model=self.model,
                api_version="2026-04-10",
            ) as connection:
                self.connection = connection
                self.audio_processor = AudioProcessor(connection)

                await self._setup_session(connection)
                self.audio_processor.start_playback()

                print("\n" + "=" * 70)
                print("  CYCLEPRO VOICE CONCIERGE  (Voice Live + MCP)")
                print(f"  MCP server: {self.mcp_server_label} <{self.mcp_server_url}>")
                print(f"  Voice: {self.voice}")
                print("  Try: 'show me electric bikes', 'rent a mountain bike for 3 days'")
                print("  Press Ctrl+C to exit")
                print("=" * 70 + "\n")

                await self._process_events(connection)
        finally:
            if self.audio_processor:
                self.audio_processor.shutdown()

    # --- session setup --------------------------------------------------

    async def _setup_session(self, connection: VoiceLiveConnection) -> None:
        logger.info("Configuring VoiceLive session with bike-rental MCP tools...")

        voice_config = AzureStandardVoice(name=self.voice)
        turn_detection = ServerVad(
            threshold=0.5,
            prefix_padding_ms=300,
            silence_duration_ms=500,
        )
        interim_response = LlmInterimResponseConfig(latency_threshold_ms=500)

        mcp_tools: list[Tool] = [
            MCPServer(
                server_label=self.mcp_server_label,
                server_url=self.mcp_server_url,
                allowed_tools=[
                    "list_categories",
                    "search_bikes",
                    "get_bike",
                    "reserve_bike",
                    "confirm_booking",
                    "cancel_reservation",
                    "get_reservation",
                    "get_booking",
                ],
                require_approval=self.require_approval,
            ),
        ]

        session_config = RequestSession(
            modalities=[Modality.TEXT, Modality.AUDIO],
            instructions=self.instructions,
            voice=voice_config,
            input_audio_format=InputAudioFormat.PCM16,
            output_audio_format=OutputAudioFormat.PCM16,
            input_audio_echo_cancellation=AudioEchoCancellation(),
            input_audio_noise_reduction=AudioNoiseReduction(
                type="azure_deep_noise_suppression",
            ),
            turn_detection=turn_detection,
            tools=mcp_tools,
            tool_choice=ToolChoiceLiteral.AUTO,
            input_audio_transcription=AudioInputTranscriptionOptions(model="whisper-1"),
            interim_response=interim_response,
        )

        await connection.session.update(session=session_config)
        logger.info("Session configuration sent")

    # --- event loop -----------------------------------------------------

    async def _process_events(self, connection: VoiceLiveConnection) -> None:
        try:
            async for event in connection:
                await self._handle_event(event, connection)
        except KeyboardInterrupt:
            logger.info("Event processing interrupted")

    async def _handle_event(self, event, connection: VoiceLiveConnection) -> None:
        ap = self.audio_processor
        assert ap is not None

        et = event.type

        if et == ServerEventType.SESSION_UPDATED:
            self.session_id = event.session.id
            logger.info("Session ready: %s", self.session_id)
            ap.start_capture()
            print("[Ready -- start speaking]")

        elif et == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STARTED:
            print("[Listening...]")
            ap.skip_pending_audio()

        elif et == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STOPPED:
            print("[Processing...]")

        elif et == ServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_COMPLETED:
            transcript = getattr(event, "transcript", "") or ""
            if transcript:
                print(f"\n  You: {transcript}")

        elif et == ServerEventType.RESPONSE_AUDIO_DELTA:
            ap.queue_audio(event.delta)

        elif et == ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DELTA:
            # Stream agent text alongside audio.
            print(event.delta, end="", flush=True)

        elif et == ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DONE:
            transcript = getattr(event, "transcript", "") or ""
            if transcript:
                print(f"\n  Agent: {transcript}")

        elif et == ServerEventType.RESPONSE_AUDIO_DONE:
            logger.debug("Agent finished speaking")

        elif et == ServerEventType.RESPONSE_DONE:
            print("[Ready]")

        # --- MCP lifecycle ------------------------------------------------

        elif et == ServerEventType.MCP_LIST_TOOLS_IN_PROGRESS:
            logger.info("MCP list_tools in progress (%s)", event.item_id)
        elif et == ServerEventType.MCP_LIST_TOOLS_COMPLETED:
            logger.info("MCP list_tools completed (%s)", event.item_id)
        elif et == ServerEventType.MCP_LIST_TOOLS_FAILED:
            logger.error("MCP list_tools FAILED (%s)", event.item_id)

        elif et == ServerEventType.RESPONSE_MCP_CALL_IN_PROGRESS:
            logger.info("MCP call in progress (%s)", event.item_id)
        elif et == ServerEventType.RESPONSE_MCP_CALL_COMPLETED:
            logger.info("MCP call completed (%s)", event.item_id)
            await self._handle_mcp_call_completed(event, connection)
        elif et == ServerEventType.RESPONSE_MCP_CALL_FAILED:
            logger.error("MCP call FAILED (%s)", event.item_id)
            # Even on failure, ask the model to summarise / recover.
            await connection.response.create()

        elif et == ServerEventType.CONVERSATION_ITEM_CREATED:
            item_type = getattr(event.item, "type", None)
            if item_type == ItemType.MCP_LIST_TOOLS:
                label = getattr(event.item, "server_label", "?")
                logger.info("MCP tools advertised by server '%s'", label)
            elif item_type == ItemType.MCP_CALL:
                self._log_mcp_call(event)
            elif item_type == ItemType.MCP_APPROVAL_REQUEST:
                await self._handle_mcp_approval_request(event, connection)

        elif et == ServerEventType.ERROR:
            logger.error("VoiceLive ERROR: %s", event.error.message)
            print(f"  ! Error: {event.error.message}")

        elif et == ServerEventType.WARNING:
            logger.warning("VoiceLive warning: %s", event.warning.message)

        else:
            logger.debug("Unhandled event type: %s", et)

    # --- MCP helpers ----------------------------------------------------

    @staticmethod
    def _log_mcp_call(event: ServerEventConversationItemCreated) -> None:
        if not isinstance(event.item, ResponseMCPCallItem):
            return
        item = event.item
        print(
            f"\n  ⚙ MCP call -> {item.server_label}.{item.name}"
            + (f"  args={item.arguments}" if item.arguments else "")
        )

    async def _handle_mcp_call_completed(
        self,
        event,
        connection: VoiceLiveConnection,
    ) -> None:
        """Trigger an assistant follow-up turn after a tool call returns.

        VoiceLive emits ``RESPONSE_MCP_CALL_COMPLETED`` to signal that the
        MCP server returned tool output, but it does *not* automatically
        start the next assistant response -- the client has to ask for it
        with ``response.create()`` so the model can incorporate the output
        into a spoken reply.
        """
        try:
            await connection.response.create()
            logger.info(
                "Requested follow-up response for completed MCP call %s",
                getattr(event, "item_id", "?"),
            )
        except Exception:  # noqa: BLE001 -- log + continue so the loop stays alive
            logger.exception("Failed to request follow-up response after MCP call")

    async def _handle_mcp_approval_request(
        self,
        event: ServerEventConversationItemCreated,
        connection: VoiceLiveConnection,
    ) -> None:
        """Prompt the user (via the terminal) to approve / deny a tool call."""
        if not isinstance(event.item, ResponseMCPApprovalRequestItem):
            logger.error("Expected ResponseMCPApprovalRequestItem")
            return

        approval_item = event.item
        if not approval_item.id:
            logger.error("MCP approval item missing ID")
            return

        print(
            "\n  🔐 Approval requested:"
            f" {approval_item.server_label}.{approval_item.name}"
            f"  args={approval_item.arguments}"
        )

        # Read approval off-thread so we don't block the asyncio event loop.
        loop = asyncio.get_running_loop()
        approve = False
        while True:
            answer = (await loop.run_in_executor(
                None, lambda: input("    Approve MCP call? (y/n): ")
            )).strip().lower()
            if answer in ("y", "yes"):
                approve = True
                break
            if answer in ("n", "no"):
                approve = False
                break
            print("    Please answer 'y' or 'n'.")

        response_item = MCPApprovalResponseRequestItem(
            approval_request_id=approval_item.id,
            approve=approve,
        )
        await connection.conversation.item.create(item=response_item)
        logger.info("Sent MCP approval response: approve=%s", approve)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


async def _run(args: argparse.Namespace) -> None:
    use_api_key = (
        args.use_api_key
        or os.environ.get("AZURE_VOICELIVE_USE_API_KEY", "").strip().lower()
        in {"1", "true", "yes"}
    )
    credential: Union[AzureKeyCredential, AsyncTokenCredential]
    if use_api_key:
        api_key = args.api_key or os.environ.get("AZURE_VOICELIVE_API_KEY")
        if not api_key:
            print("ERROR: --api-key (or AZURE_VOICELIVE_API_KEY) is required when --use-api-key is set")
            sys.exit(1)
        credential = AzureKeyCredential(api_key)
        logger.info("Authenticating with API key")
    else:
        credential = DefaultAzureCredential()
        logger.info("Authenticating with DefaultAzureCredential (Entra ID)")

    agent = BikeRentalVoiceLiveMCPAgent(
        endpoint=args.endpoint,
        credential=credential,
        model=args.model,
        voice=args.voice,
        mcp_server_url=args.mcp_server_url,
        mcp_server_label=args.mcp_server_label,
        require_approval=args.require_approval,
        instructions=DEFAULT_INSTRUCTIONS,
    )

    try:
        await agent.start()
    finally:
        if isinstance(credential, AsyncTokenCredential):
            await credential.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Voice-Live agent that uses the bike-rental MCP server as its tool backend.",
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("AZURE_VOICELIVE_ENDPOINT", "wss://api.voicelive.com/v1"),
        help="Azure VoiceLive endpoint (or set AZURE_VOICELIVE_ENDPOINT).",
    )
    parser.add_argument(
        "--mcp-server-url",
        default=os.environ.get("BIKE_RENTAL_MCP_URL"),
        help=(
            "Publicly-reachable URL of the bike-rental MCP server "
            "(or set BIKE_RENTAL_MCP_URL). "
            "VoiceLive must be able to reach this URL -- use a tunnel "
            "(devtunnel / ngrok) for local servers."
        ),
    )
    parser.add_argument(
        "--mcp-server-label",
        default=os.environ.get("BIKE_RENTAL_MCP_LABEL", "bike-rental"),
        help="Server label sent in MCP events (default: bike-rental).",
    )
    parser.add_argument(
        "--voice",
        default=os.environ.get("AZURE_VOICELIVE_VOICE", "en-US-Ava:DragonHDLatestNeural"),
        help="Azure neural voice (or set AZURE_VOICELIVE_VOICE). Default: en-US-Ava:DragonHDLatestNeural.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("AZURE_VOICELIVE_MODEL", "gpt-realtime"),
        help="VoiceLive model (or set AZURE_VOICELIVE_MODEL). Default: gpt-realtime.",
    )
    parser.add_argument(
        "--require-approval",
        choices=("never", "always"),
        default=os.environ.get("BIKE_RENTAL_MCP_REQUIRE_APPROVAL", "never"),
        help="MCP approval policy (or set BIKE_RENTAL_MCP_REQUIRE_APPROVAL). Default: never.",
    )
    parser.add_argument(
        "--use-api-key",
        action="store_true",
        help="Authenticate with --api-key / AZURE_VOICELIVE_API_KEY instead of Entra ID.",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key used when --use-api-key is set (or set AZURE_VOICELIVE_API_KEY).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if not args.endpoint:
        print("ERROR: --endpoint is required (or set AZURE_VOICELIVE_ENDPOINT)")
        sys.exit(1)
    if not args.mcp_server_url:
        print("ERROR: --mcp-server-url is required (or set BIKE_RENTAL_MCP_URL)")
        sys.exit(1)
    if args.require_approval not in ("never", "always"):
        print(
            "ERROR: --require-approval / BIKE_RENTAL_MCP_REQUIRE_APPROVAL must be "
            f"'never' or 'always' (got: {args.require_approval!r})"
        )
        sys.exit(1)

    # Check audio devices up-front for a friendlier error.
    try:
        pa = pyaudio.PyAudio()
        input_devices = [
            i for i in range(pa.get_device_count())
            if cast(Union[int, float], pa.get_device_info_by_index(i).get("maxInputChannels", 0) or 0) > 0
        ]
        output_devices = [
            i for i in range(pa.get_device_count())
            if cast(Union[int, float], pa.get_device_info_by_index(i).get("maxOutputChannels", 0) or 0) > 0
        ]
        pa.terminate()
        if not input_devices:
            print("ERROR: no audio input devices found (microphone)")
            sys.exit(1)
        if not output_devices:
            print("ERROR: no audio output devices found (speakers)")
            sys.exit(1)
    except Exception as exc:  # noqa: BLE001 -- audio init failures vary by platform
        print(f"ERROR: audio system check failed: {exc}")
        sys.exit(1)

    def _on_signal(_sig, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\nVoice agent shut down. Goodbye!")


if __name__ == "__main__":
    main()
