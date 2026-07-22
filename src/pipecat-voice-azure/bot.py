#!/usr/bin/env python
# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
# --------------------------------------------------------------------------
"""CyclePro voice bike guide — Pipecat cascade bot (Azure STT + Azure OpenAI LLM + Azure TTS).

A minimal speech-to-speech agent that follows the Pipecat ``voice-azure`` example:

    mic → Azure Speech STT → Azure OpenAI LLM → Azure Speech TTS → speaker

It uses the built-in Pipecat development runner, which serves a WebRTC client in
the browser — no custom signaling server required.

USAGE:
    python src/pipecat-voice-azure/bot.py
    # then open the printed URL (default http://localhost:7860) in a browser

REQUIRED ENVIRONMENT VARIABLES (see .env):
    AZURE_SPEECH_API_KEY      Azure AI Speech key (used for both STT and TTS)
    AZURE_SPEECH_REGION       Azure AI Speech region, e.g. "swedencentral"
    AZURE_OPENAI_API_KEY      Azure OpenAI key
    AZURE_OPENAI_ENDPOINT     Azure OpenAI endpoint, e.g. "https://<res>.openai.azure.com"

OPTIONAL:
    AZURE_OPENAI_DEPLOYMENT   Chat model deployment name (default: gpt-4.1-mini)
    AZURE_OPENAI_API_VERSION  Azure OpenAI API version (default: 2024-09-01-preview)
    AZURE_TTS_VOICE           Neural TTS voice (default: en-US-AvaMultilingualNeural)
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.azure.llm import AzureLLMService
from pipecat.services.azure.stt import AzureSTTService
from pipecat.services.azure.tts import AzureTTSService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.workers.runner import WorkerRunner

# Load the .env that sits next to this bot first (it holds the Azure Speech /
# OpenAI credentials), then fall back to the workspace-root .env for anything
# not already set.
_BOT_DIR = Path(__file__).resolve().parent
_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_BOT_DIR / ".env", override=True)
load_dotenv(_WORKSPACE_ROOT / ".env", override=False)


SYSTEM_INSTRUCTION = """\
You are the CyclePro Bike Product Guide, a friendly voice assistant that helps
customers choose the right bike — city bikes, mountain bikes, and children's bikes.

Because your responses are spoken aloud, keep them short (2-3 sentences),
conversational, and free of emojis, bullet points, or any formatting that cannot
be read out loud. Ask a clarifying question when the customer's needs are unclear
(budget, terrain, rider height, age), quote prices in euros, and proactively
suggest helpful alternatives."""


# Transport parameters are created lazily so the transport type can be chosen at
# runtime by the Pipecat runner (defaults to WebRTC).
transport_params = {
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
}


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments) -> None:
    """Wire up the STT → LLM → TTS pipeline and run it on the given transport."""
    logger.info("Starting CyclePro Azure voice bot")

    # The Pipecat runner (pipecat.runner.run) calls ``load_dotenv(override=True)``
    # at import time, which picks up the workspace-root .env and clobbers the
    # credentials we set at module import. Re-apply this bot's own .env here, at
    # runtime, so the correct Azure endpoint/keys always win.
    load_dotenv(_BOT_DIR / ".env", override=True)

    stt = AzureSTTService(
        api_key=os.environ["AZURE_SPEECH_API_KEY"],
        region=os.environ["AZURE_SPEECH_REGION"],
    )

    tts = AzureTTSService(
        api_key=os.environ["AZURE_SPEECH_API_KEY"],
        region=os.environ["AZURE_SPEECH_REGION"],
        settings=AzureTTSService.Settings(
            voice=os.getenv("AZURE_TTS_VOICE", "en-US-AvaMultilingualNeural"),
        ),
    )

    llm = AzureLLMService(
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-09-01-preview"),
        settings=AzureLLMService.Settings(
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"),
            system_instruction=SYSTEM_INSTRUCTION,
        ),
    )

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline(
        [
            transport.input(),      # Transport user input (mic)
            stt,                    # Speech-to-text
            user_aggregator,        # User responses
            llm,                    # Azure OpenAI LLM
            tts,                    # Text-to-speech
            transport.output(),     # Transport bot output (speaker)
            assistant_aggregator,   # Assistant spoken responses
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected — greeting the user")
        context.add_message(
            {
                "role": "developer",
                "content": (
                    "Greet the user warmly as the CyclePro Bike Product Guide and "
                    "ask how you can help them find the perfect bike today."
                ),
            }
        )
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args: RunnerArguments) -> None:
    """Main bot entry point compatible with the Pipecat runner and Pipecat Cloud."""
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
