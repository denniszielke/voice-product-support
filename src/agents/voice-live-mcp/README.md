# Voice-Live MCP Agent

A voice agent built on the [Azure VoiceLive SDK](https://learn.microsoft.com/azure/ai-services/voicelive/)
that uses the **bike-rental MCP server** ([src/mcp-server-bike-rental/server.py](../../mcp-server-bike-rental/server.py))
as its tool backend. VoiceLive handles speech-in/speech-out; the MCP server
handles search, reservation, and booking logic.

```
microphone --> VoiceLive (cloud)
                 |  STT, LLM, TTS
                 v
              MCP tools  --HTTPS-->  bike-rental MCP server
                                       (search, reserve, confirm, ...)
                 v
              speakers
```

---

## Prerequisites

* Python 3.10+ with the workspace `.venv`.
* Microphone and speakers.
* An Azure AI Foundry / VoiceLive endpoint you can reach with Entra ID
  (or an API key).
* A way to expose the local MCP server to the public internet
  (devtunnel or ngrok), because VoiceLive runs in Azure and cannot
  reach `http://localhost:8000`.

Install dependencies (one-time):

```bash
pip install -r src/agents/voice-live-mcp/requirements.txt
```

On macOS PortAudio is needed for `pyaudio`:

```bash
brew install portaudio
```

---

## 1. Start the bike-rental MCP server

In one terminal:

```bash
python src/mcp-server-bike-rental/server.py
# -> listens on http://0.0.0.0:8000/mcp
```

Override the port with `MCP_PORT=8765 python ...` if `:8000` is taken.

---

## 2. Expose the MCP server publicly

Pick **one** of the options below. Both produce an HTTPS URL you'll plug
into `BIKE_RENTAL_MCP_URL` in the next step.

### Option A — Microsoft devtunnel (recommended)

One-time setup:

```bash
brew install --cask devtunnel        # macOS; see https://aka.ms/devtunnels for others
devtunnel user login                  # Microsoft, GitHub, or Entra ID
```

#### Quick anonymous tunnel (URL changes each run)

```bash
devtunnel host -p 8000 --allow-anonymous --protocol http
```

The CLI prints a URL like
`https://abc123-8000.usw2.devtunnels.ms/`. Append `/mcp` for the MCP
endpoint and use that as `BIKE_RENTAL_MCP_URL`.

#### Persistent tunnel (stable URL across restarts)

```bash
devtunnel create bike-rental-mcp --allow-anonymous
devtunnel port create bike-rental-mcp -p 8000 --protocol http
devtunnel host bike-rental-mcp
```

Look up the URL anytime:

```bash
devtunnel show bike-rental-mcp
```

Tear it down when finished:

```bash
devtunnel delete bike-rental-mcp
```

> `--allow-anonymous` is required because VoiceLive calls the URL without
> your Entra token. Don't put anything sensitive behind it.
> `--protocol http` tells devtunnel the upstream speaks plain HTTP;
> devtunnel terminates TLS on the public side.

### Option B — ngrok

```bash
ngrok http 8000
# use the printed https://*.ngrok-free.app/mcp URL
```

---

## 3. Run the voice agent

In a third terminal, point the agent at the tunneled MCP URL and your
VoiceLive endpoint:

```bash
export AZURE_VOICELIVE_ENDPOINT=wss://<your-account>.services.ai.azure.com
export BIKE_RENTAL_MCP_URL=https://abc123-8000.usw2.devtunnels.ms/mcp

python src/agents/voice-live-mcp/agent.py
```

Or pass them as CLI flags:

```bash
python src/agents/voice-live-mcp/agent.py \
  --mcp-server-url https://wvcx834m-8000.euw.devtunnels.ms/mcp
```

When the agent prints `[Ready -- start speaking]`, try:

* "Show me electric commuter bikes under 50 euros."
* "Rent the EnduroX for three days."
* "Reserve it under the name Dennis."
* "Yes, confirm the booking."

Press `Ctrl+C` to exit.

---

## Configuration

All flags fall back to environment variables (see
[`.env.example`](../../../.env.example)):

| Variable                            | Flag                  | Default                              |
| ----------------------------------- | --------------------- | ------------------------------------ |
| `AZURE_VOICELIVE_ENDPOINT`          | `--endpoint`          | `wss://api.voicelive.com/v1`         |
| `AZURE_VOICELIVE_MODEL`             | `--model`             | `gpt-realtime`                       |
| `AZURE_VOICELIVE_VOICE`             | `--voice`             | `en-US-Ava:DragonHDLatestNeural`     |
| `AZURE_VOICELIVE_USE_API_KEY`       | `--use-api-key`       | unset (uses `DefaultAzureCredential`) |
| `AZURE_VOICELIVE_API_KEY`           | `--api-key`           | —                                    |
| `BIKE_RENTAL_MCP_URL`               | `--mcp-server-url`    | — *(required)*                       |
| `BIKE_RENTAL_MCP_LABEL`             | `--mcp-server-label`  | `bike-rental`                        |
| `BIKE_RENTAL_MCP_REQUIRE_APPROVAL`  | `--require-approval`  | `never`                              |

Setting `BIKE_RENTAL_MCP_REQUIRE_APPROVAL=always` makes the agent prompt
in the terminal before every MCP tool call — useful when developing or
debugging tool calls.

---

## Troubleshooting

* **`MCP list_tools FAILED`** — VoiceLive can't reach
  `BIKE_RENTAL_MCP_URL`. Open the URL in a browser; you should get a
  `307` redirect from `/mcp` to `/mcp/`. If you get nothing, the tunnel
  isn't up or anonymous access isn't enabled.
* **`no audio input devices found`** — grant the terminal microphone
  permission (macOS: System Settings → Privacy & Security → Microphone).
* **`Authentication failed`** — run `az login` for Entra-based auth, or
  set `AZURE_VOICELIVE_USE_API_KEY=true` and `AZURE_VOICELIVE_API_KEY`.
* **MCP server port already in use** — pick another port:
  `MCP_PORT=8765 python src/mcp-server-bike-rental/server.py` and
  tunnel that port (`devtunnel host -p 8765 ...`).
