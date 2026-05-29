"""Product Guide Agent.

Prompt-based agent that uses Azure AI Search (vector database) to answer
questions about bikes and help customers compare city, mountain, and
children's bikes from the catalogue.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

logging.basicConfig(level=logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)

# Allow imports from src root when running standalone
_src_root = Path(__file__).resolve().parents[2] if len(Path(__file__).resolve().parents) > 2 else Path(__file__).resolve().parent
sys.path.insert(0, str(_src_root))

_env_path = Path(__file__).resolve().parents[3] / ".env" if len(Path(__file__).resolve().parents) > 3 else None
load_dotenv(dotenv_path=_env_path if _env_path and _env_path.exists() else None)

from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizableTextQuery

# ---------------------------------------------------------------------------
# Fallback in-memory product catalogue (used when AI Search is unavailable)
# ---------------------------------------------------------------------------
from data.bikes import BIKES

_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "")
_SEARCH_INDEX = os.getenv("AZURE_SEARCH_INDEX_NAME", "bike-products")
_SEARCH_API_KEY = os.getenv("AZURE_SEARCH_API_KEY", "")

_search_client: SearchClient | None = None

if _SEARCH_ENDPOINT:
    _credential = (
        AzureKeyCredential(_SEARCH_API_KEY)
        if _SEARCH_API_KEY
        else DefaultAzureCredential()
    )
    _search_client = SearchClient(
        endpoint=_SEARCH_ENDPOINT,
        index_name=_SEARCH_INDEX,
        credential=_credential,
    )


def search_bikes(query: str, top: int = 5) -> list[dict]:
    """Search the bike catalogue using Azure AI Search or fall back to in-memory."""
    if _search_client:
        try:
            results = _search_client.search(
                search_text=query,
                vector_queries=[VectorizableTextQuery(text=query, k_nearest_neighbors=top, fields="descriptionVector")],
                top=top,
            )
            return [dict(r) for r in results]
        except Exception as exc:
            print(f"[product-guide] AI Search error ({exc}), falling back to in-memory data", flush=True)

    # Keyword fallback over in-memory catalogue
    query_lower = query.lower()
    matches = [
        b for b in BIKES
        if any(
            token in b["name"].lower()
            or token in b["description"].lower()
            or token in b["category"].lower()
            or token in b.get("suitable_for", "").lower()
            for token in query_lower.split()
        )
    ]
    return matches[:top] if matches else BIKES[:top]


SYSTEM_PROMPT = """\
You are the Bike Product Guide for CyclePro Support, a knowledgeable and friendly
assistant that helps customers choose the right bike.

You have access to a bike catalogue containing city bikes, mountain bikes, and
children's bikes. Use the search_bikes tool to retrieve relevant products before
answering any product question.

Guidelines:
- Always search the catalogue before making recommendations.
- When comparing bikes, use a structured format (name, price, key features).
- Highlight the most important differences when comparing two or more bikes.
- Ask clarifying questions if the customer's need is unclear (budget, terrain, rider height, age).
- Quote prices in EUR and mention if accessories are included.
- Be concise, friendly, and proactive in suggesting alternatives.
- For availability and stock questions, direct the customer to the nearest store.
"""

# ---------------------------------------------------------------------------
# Stand-alone test (python agent.py --query "...")
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--query", type=str, default="What city bikes do you have?")
    args = parser.parse_args()

    results = search_bikes(args.query)
    print(f"Query: {args.query}")
    print(f"Found {len(results)} bikes:")
    for b in results:
        print(f"  [{b.get('id', '?')}] {b.get('name', '?')} — €{b.get('price_eur', '?')}")
        print(f"    {b.get('description', '')[:120]}...")
