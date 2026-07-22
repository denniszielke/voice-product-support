# Realtime Async-Tool Demo

A Pipecat voice agent built on the **Azure OpenAI Realtime** LLM service that
demonstrates **asynchronous tool calling**. Adapted from the Pipecat
[realtime-azure-async-tool](https://github.com/pipecat-ai/pipecat/blob/main/examples/realtime/realtime-azure-async-tool.py)
example for the CyclePro bike domain.

## What it shows

The `check_bike_availability` tool is registered with
`cancel_on_interruption=False` and simulates a slow inventory lookup (an
8-second sleep). While the lookup runs, the conversation keeps flowing — the
model keeps chatting with the user. When the result is ready it arrives via the
async-tool mechanism and is forwarded to Azure Realtime as a
`function_call_output`, so the model weaves the stock and delivery details into
its next turn.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.sample ../../.env   # or set the vars in the workspace-root .env
```

Set `AZURE_REALTIME_API_KEY` and `AZURE_REALTIME_BASE_URL` (see `.env.sample`).
The `AZURE_REALTIME_BASE_URL` must be the full `wss://` realtime endpoint
including `api-version` and `deployment`.

## Run

```bash
python bot.py
```

This starts the built-in Pipecat runner, which serves a SmallWebRTC client you
can open in the browser to talk to the agent.
