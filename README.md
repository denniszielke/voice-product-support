# Voice Product Support — CyclePro AI Bike Hotline

A voice-enabled product support hotline for bike recommendations, troubleshooting, and repair management, built on **Azure AI Foundry** hosted agents.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    bike-support (Workflow)                    │
│         Routes conversations to the right specialist         │
└──────────┬──────────────┬──────────────────┬────────────────┘
           │              │                  │
  ┌────────▼────────┐     │                  │
  │  bike-concierge │     │                  │
  │  (Prompt Agent) │     │                  │
  └────────┬────────┘     │                  │
           │              │                  │
  ┌────────▼──────┐ ┌─────▼──────┐ ┌────────▼──────┐
  │ product-guide │ │  support-  │ │ repair-status │
  │ (Hosted Agent │ │  hotline   │ │ (Hosted Agent │
  │  AI Search)   │ │ (Hosted    │ │  LangGraph)   │
  └───────────────┘ │  Agent     │ └───────────────┘
                    │  Bing Web) │
                    └────────────┘
```

## Agents

### 1. `bike-concierge` — Prompt Agent
Intent classifier and router. Uses structured JSON output to route requests to the right specialist agent.

### 2. `product-guide` — Hosted Agent (Agent Framework)
Answers questions about bike models and helps customers compare city, mountain, and children's bikes.
Uses **Azure AI Search** (vector database) to search the bike catalogue.

### 3. `support-hotline` — Hosted Agent (Agent Framework + Bing)
Troubleshoots bike problems with internet-grounded answers. Uses the **Foundry Toolbox MCP** with
Bing Custom Web Search to find repair guides and maintenance tips.

### 4. `repair-status` — Hosted Agent (LangGraph)
Handles repair scheduling and status queries. Built with **LangGraph** and backed by in-memory
repair job data. Tools:
- `get_repair_status` — look up a job by ID
- `list_repair_jobs_for_customer` — search jobs by customer name
- `schedule_repair` — book a new appointment
- `cancel_repair` — cancel an existing booking
- `get_available_slots` — find open appointment slots

### 5. `bike-support` — Workflow Agent
Top-level entry point that orchestrates the concierge and specialist agents.

## Folder Structure

```
/src
  /agents
    /product-guide      — Hosted agent: bike catalogue search
    /support-hotline    — Hosted agent: Bing web search troubleshooting
    /repair-status      — Hosted agent: LangGraph repair scheduling
  /config               — Shared settings
  /data                 — Bike sample data (catalogue, repairs, FAQs)
  /workflows            — Workflow YAML definition
  /a2a                  — A2A test clients
/tools
  deploy_agents.py      — Deploy all agents (orchestrator)
  deploy_prompt_agents.py
  deploy_toolbox.py
  deploy_hosted_agents.py
  deploy_workflow_agents.py
  delete_agents.py
  deploy_helpers.py
/infra
  main.bicep            — Azure infrastructure
  main.parameters.json
  /core                 — Bicep modules
```

## Sample Data

The `/src/data/bikes.py` module contains:
- **9 bike models**: 3 city bikes (including e-bike), 3 mountain bikes, 3 children's bikes
- **Common support questions** per category
- **6 pre-loaded repair jobs** in various states

### Example Questions

**Product Guide:**
- "What mountain bikes do you have for a beginner?"
- "Compare the TrailBlaster 29 and EnduroX Full Suspension"
- "I have a 7-year-old, which bike would you recommend?"

**Support Hotline:**
- "My hydraulic disc brakes are squealing — how do I fix it?"
- "The suspension fork on my TrailBlaster is leaking oil"
- "How do I set up tubeless tyres?"

**Repair Status:**
- "What is the status of repair REP-1002?"
- "I need to book a service for my SpeedCommute E5"
- "What appointment slots are available next week?"

## Prerequisites

- Azure subscription with Azure AI Foundry access
- Azure Developer CLI (`azd`)
- Python 3.13+
- Docker (for building hosted agent images)
- Azure CLI (`az`)

## Deployment

### 1. Infrastructure
```bash
azd up
```

This provisions: Azure AI Foundry project (gpt-4.1-mini), Azure AI Search, Azure Container Registry,
Bing Custom Search, Storage, Log Analytics, and Application Insights.

### 2. Agents (manual)
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r src/requirements.txt
cd tools && python deploy_agents.py
```

### 3. Test with A2A clients
```bash
cd src
python a2a/bike-concierge-agent-client.py
python a2a/repair-status-agent-client.py
python a2a/support-hotline-agent-client.py
```

### 4. Delete all agents
```bash
cd tools && python delete_agents.py
```

## Environment Variables

Copy `.env.example` to `.env` and fill in your values.
