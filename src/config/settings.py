"""Configuration settings for bike support agent applications."""
import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from workspace root
workspace_root = Path(__file__).parent.parent.parent
env_path = workspace_root / ".env"
load_dotenv(dotenv_path=env_path)


class Settings:
    """Application settings loaded from environment variables."""

    # Azure AI Foundry
    PROJECT_ENDPOINT: str = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
    MODEL_DEPLOYMENT_NAME: str = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini")

    # Azure AI Search
    SEARCH_ENDPOINT: str = os.getenv("AZURE_SEARCH_ENDPOINT", "")
    SEARCH_INDEX_NAME: str = os.getenv("AZURE_SEARCH_INDEX_NAME", "bike-products")
    SEARCH_API_KEY: Optional[str] = os.getenv("AZURE_SEARCH_API_KEY")

    # Application Insights
    APPLICATIONINSIGHTS_CONNECTION_STRING: Optional[str] = os.getenv(
        "APPLICATIONINSIGHTS_CONNECTION_STRING"
    )

    @classmethod
    def validate(cls) -> None:
        """Validate required settings are present."""
        missing = []
        if not cls.PROJECT_ENDPOINT:
            missing.append("AZURE_AI_PROJECT_ENDPOINT")
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")


settings = Settings()
