"""Pipecat bot using SmallWebRTCTransport for the CyclePro Bike Product Guide.

Architecture:
  Main agent (no LLM/TTS):
      webrtc.in -> STT -> context_agg.user -> BusBridge ->
      webrtc.out -> context_agg.assistant

  LLM agent (with TTS):
      BusInput -> LLM -> TTS -> BusOutput

The ProductGuideAgent handles bike product questions using Azure OpenAI
and Azure Speech Services for STT/TTS.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env from workspace root
_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_WORKSPACE_ROOT / ".env", override=True)

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.observers.turn_tracking_observer import TurnTrackingObserver
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame
from pipecat.services.azure.stt import AzureSTTService
from pipecat.services.azure.tts import AzureTTSService
from pipecat.services.azure.llm import AzureLLMService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat_subagents.agents import BaseAgent, LLMAgentActivationArgs, agent_ready
from pipecat_subagents.bus import BusBridgeProcessor
from pipecat_subagents.runner import AgentRunner
from pipecat_subagents.types import AgentReadyData

# Add src root for data imports
_SRC_ROOT = Path(__file__).resolve().parent.parent
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))


# ---------------------------------------------------------------------------
# Azure service builders
# ---------------------------------------------------------------------------


def _build_stt() -> AzureSTTService:
    """Build Azure Speech-to-Text service."""
    return AzureSTTService(
        api_key=os.environ["AZURE_SPEECH_API_KEY"],
        region=os.environ["AZURE_SPEECH_REGION"],
        language=os.getenv("AZURE_SPEECH_LANGUAGE", "en-US"),
    )


def _build_tts() -> AzureTTSService:
    """Build Azure Text-to-Speech service."""
    return AzureTTSService(
        api_key=os.environ["AZURE_SPEECH_API_KEY"],
        region=os.environ["AZURE_SPEECH_REGION"],
        voice=os.getenv("AZURE_TTS_VOICE", "en-US-AvaMultilingualNeural"),
    )


def _build_llm() -> AzureLLMService:
    """Build Azure OpenAI LLM service."""
    return AzureLLMService(
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        model=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"),
    )


# ---------------------------------------------------------------------------
# Product Guide LLM Agent
# ---------------------------------------------------------------------------

PRODUCT_GUIDE_SYSTEM_PROMPT = """\
You are the Bike Product Guide for CyclePro Support, a knowledgeable and friendly
voice assistant that helps customers choose the right bike.

You have access to a catalogue of city bikes, mountain bikes, and children's bikes.

Guidelines:
- Be conversational and concise — this is a voice interface.
- When comparing bikes, mention name, price, and key differentiators.
- Ask clarifying questions if the customer's needs are unclear (budget, terrain, rider height).
- Quote prices in EUR.
- Keep responses short (2-3 sentences) for voice clarity.
- If you don't know something, say so and offer to help with what you can.
"""


class ProductGuideAgent(BaseAgent):
    """LLM agent for bike product guidance with TTS output."""

    def __init__(self, name: str, *, bus):
        super().__init__(name, bus=bus)

    async def build_pipeline(self) -> Pipeline:
        llm = _build_llm()
        tts = _build_tts()

        return Pipeline([llm, tts])

    def build_pipeline_task(self, pipeline: Pipeline) -> PipelineTask:
        return PipelineTask(
            pipeline,
            params=PipelineParams(
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
        )


# ---------------------------------------------------------------------------
# Main transport-owning agent (WebRTC version)
# ---------------------------------------------------------------------------


class BikeGuideMainAgent(BaseAgent):
    """Owns the SmallWebRTC transport and bridges frames to/from the bus."""

    def __init__(self, name: str, *, bus, transport: SmallWebRTCTransport):
        super().__init__(name, bus=bus)
        self._transport = transport

    @agent_ready(name="product_guide")
    async def on_product_guide_ready(self, data: AgentReadyData) -> None:
        await self.activate_agent(
            "product_guide",
            args=LLMAgentActivationArgs(
                messages=[
                    {
                        "role": "developer",
                        "content": (
                            "Greet the user warmly as the CyclePro Bike Product Guide. "
                            "Ask how you can help them find the perfect bike today."
                        ),
                    },
                ],
            ),
        )

    def build_pipeline_task(self, pipeline: Pipeline) -> PipelineTask:
        turn_observer = TurnTrackingObserver(turn_end_timeout_secs=2.5)
        latency_observer = UserBotLatencyObserver()

        task = PipelineTask(
            pipeline,
            enable_rtvi=True,
            idle_timeout_secs=None,
            params=PipelineParams(
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
            observers=[turn_observer, latency_observer],
        )

        @turn_observer.event_handler("on_turn_started")
        async def on_turn_started(observer, turn_count):
            logger.info(f"Turn {turn_count} started")
            await task.queue_frame(
                RTVIServerMessageFrame({"type": "turn-started", "turn_count": turn_count})
            )

        @turn_observer.event_handler("on_turn_ended")
        async def on_turn_ended(observer, turn_count, duration, was_interrupted):
            status = "interrupted" if was_interrupted else "completed"
            logger.info(f"Turn {turn_count} {status} after {duration:.2f}s")
            await task.queue_frame(
                RTVIServerMessageFrame(
                    {
                        "type": "turn-ended",
                        "turn_count": turn_count,
                        "duration": round(duration, 3),
                        "was_interrupted": was_interrupted,
                    }
                )
            )

        @latency_observer.event_handler("on_latency_measured")
        async def on_latency_measured(observer, latency_seconds):
            logger.info(f"Latency: {latency_seconds:.3f}s")
            await task.queue_frame(
                RTVIServerMessageFrame(
                    {"type": "latency", "latency_seconds": round(latency_seconds, 3)}
                )
            )

        return task

    async def build_pipeline(self) -> Pipeline:
        stt = _build_stt()

        context = LLMContext()
        context_aggregator = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(
                vad_analyzer=SileroVADAnalyzer(),
                user_turn_strategies=UserTurnStrategies(
                    stop=[
                        TurnAnalyzerUserTurnStopStrategy(
                            enable_user_speaking_frames=False,
                        )
                    ],
                ),
            ),
        )

        bridge = BusBridgeProcessor(
            bus=self.bus,
            agent_name=self.name,
            name=f"{self.name}::BusBridge",
        )

        @self._transport.event_handler("on_client_connected")
        async def on_client_connected(transport, client):
            logger.info("WebRTC client connected")
            product_guide = ProductGuideAgent("product_guide", bus=self.bus)
            await self.add_agent(product_guide)

        @self._transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            logger.info("WebRTC client disconnected")
            await self.cancel()

        return Pipeline(
            [
                self._transport.input(),
                stt,
                context_aggregator.user(),
                bridge,
                self._transport.output(),
                context_aggregator.assistant(),
            ]
        )


# ---------------------------------------------------------------------------
# Entry point used by server.py
# ---------------------------------------------------------------------------


async def run_bot(webrtc_connection: SmallWebRTCConnection) -> None:
    """Run the product guide bot bound to a SmallWebRTCConnection."""
    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        ),
    )

    runner = AgentRunner(handle_sigint=False)
    main = BikeGuideMainAgent("bike_guide", bus=runner.bus, transport=transport)
    await runner.add_agent(main)
    await runner.run()
