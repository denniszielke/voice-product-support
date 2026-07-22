#
# Copyright (c) 2024-2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Async function call with the Azure Realtime LLM service (bike domain demo).

Adapted from the Pipecat reference example:
https://github.com/pipecat-ai/pipecat/blob/main/examples/realtime/realtime-azure-async-tool.py

The ``check_bike_availability`` tool is registered with
``cancel_on_interruption=False`` and simulates a slow inventory lookup (an
8-second sleep). While the lookup is in flight the conversation keeps going;
the result arrives later via the async-tool mechanism and is forwarded to
Azure Realtime as a ``function_call_output`` so the model can integrate it
naturally into its next turn.

Run locally with the built-in Pipecat runner (serves a SmallWebRTC client):

    python bot.py

Required environment variables (see .env.sample):

    AZURE_REALTIME_API_KEY   Azure OpenAI API key
    AZURE_REALTIME_BASE_URL  Full wss:// realtime endpoint, e.g.
        wss://<resource>.openai.azure.com/openai/realtime\
?api-version=2025-04-01-preview&deployment=<realtime-deployment>
"""

import asyncio
import os
import random
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from pipecat.adapters.schemas.direct_function import tool_options
from pipecat.evals.transport import EvalTransportParams
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.services.azure.realtime.llm import AzureRealtimeLLMService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.openai.realtime.events import (
    AudioConfiguration,
    AudioInput,
    InputAudioTranscription,
    SessionProperties,
)
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams
from pipecat.workers.runner import WorkerRunner

# Load the workspace-root .env first (shared repo config), then this demo's
# local .env so its values take precedence when both are present.
_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_WORKSPACE_ROOT / ".env", override=True)
load_dotenv(Path(__file__).with_name(".env"), override=True)

# Make the shared bike catalogue importable.
_SRC_ROOT = Path(__file__).resolve().parent.parent
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from data.bikes import BIKES  # noqa: E402


def _find_bike(query: str) -> dict | None:
    """Look up a bike by (partial) name or id, case-insensitively."""
    q = query.strip().lower()
    for bike in BIKES:
        if q == bike["id"].lower() or q == bike["name"].lower():
            return bike
    for bike in BIKES:
        if q in bike["name"].lower():
            return bike
    return None


@tool_options(cancel_on_interruption=False)
async def check_bike_availability(params: FunctionCallParams, bike_name: str):
    """Check live stock availability and delivery estimate for a bike.

    Args:
        bike_name: The name (or catalogue id) of the bike to check, e.g.
            "CityRider Pro" or "CB-001".
    """
    # Simulate a slow inventory-system call so we can demonstrate that the
    # conversation continues while the tool is in flight.
    await asyncio.sleep(8)

    bike = _find_bike(bike_name)
    if bike is None:
        await params.result_callback(
            {
                "found": False,
                "query": bike_name,
                "message": "No matching bike found in the catalogue.",
                "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
            }
        )
        return

    in_stock = random.random() > 0.25
    await params.result_callback(
        {
            "found": True,
            "name": bike["name"],
            "id": bike["id"],
            "price_eur": bike["price_eur"],
            "in_stock": in_stock,
            "units_available": random.randint(1, 12) if in_stock else 0,
            "delivery_estimate_days": random.randint(2, 5)
            if in_stock
            else random.randint(14, 28),
            "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
        }
    )


system_instruction = (
    "You are the CyclePro Bike Product Guide, a friendly voice assistant that "
    "helps customers choose the right bike. The user and you will engage in a "
    "spoken dialog exchanging the transcripts of a natural real-time "
    "conversation. Keep your responses short, generally two or three sentences. "
    "Quote prices in EUR. When the user asks whether a bike is in stock or how "
    "quickly it can be delivered, call check_bike_availability. While you wait "
    "for the result, keep chatting with the user about the bike. When the result "
    "arrives, share the stock and delivery details with them naturally."
)


transport_params = {
    "eval": lambda: EvalTransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
    "websocket": lambda: FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        add_wav_header=False,
        serializer=ProtobufFrameSerializer(),
    ),
}


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    logger.info("Starting bot")

    llm = AzureRealtimeLLMService(
        api_key=os.environ["AZURE_REALTIME_API_KEY"],
        base_url=os.environ["AZURE_REALTIME_BASE_URL"],
        settings=AzureRealtimeLLMService.Settings(
            system_instruction=system_instruction,
            session_properties=SessionProperties(
                audio=AudioConfiguration(
                    input=AudioInput(
                        transcription=InputAudioTranscription(model="whisper-1"),
                    )
                ),
            ),
        ),
    )

    context = LLMContext(tools=[check_bike_availability])
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
    )

    pipeline = Pipeline(
        [
            transport.input(),
            user_aggregator,
            llm,
            transport.output(),
            assistant_aggregator,
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
        logger.info("Client connected")
        context.add_message(
            {"role": "developer", "content": "Please introduce yourself to the user."}
        )
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args: RunnerArguments):
    """Main bot entry point compatible with Pipecat Cloud."""
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
