"""
Application configuration — loaded from environment variables.

Production safety: required credentials are validated at startup when
debug=False. Missing credentials raise immediately rather than falling
through to localhost defaults.
"""

import logging
from pydantic_settings import BaseSettings
from pydantic import model_validator
from functools import lru_cache

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # App
    app_name: str = "STRØM Import API"
    debug: bool = False
    api_version: str = "v1"
    cors_origins: list[str] = ["http://localhost:3000"]

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/strom_import"
    database_url_sync: str = "postgresql://postgres:postgres@localhost:5432/strom_import"

    # Auth (Clerk)
    clerk_secret_key: str = ""
    clerk_publishable_key: str = ""
    clerk_jwks_url: str = "https://api.clerk.com/v1/jwks"

    # Anthropic (Claude)
    anthropic_api_key: str = ""

    # SerpAPI (Google Images fallback)
    serpapi_key: str = ""

    # Encryption key for Shopify tokens
    encryption_key: str = ""

    # Shopify OAuth
    shopify_api_key: str = ""
    shopify_api_secret: str = ""
    shopify_scopes: str = "write_products,read_products,write_inventory,read_inventory,read_locations,write_translations,read_translations,read_publications,write_publications"
    shopify_app_url: str = "http://localhost:3000"
    shopify_redirect_uri: str = "http://localhost:8000/api/v1/shopify/callback"

    # Google OAuth (Search Console)
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/v1/seo/callback"

    # DataForSEO (keyword volume + difficulty)
    dataforseo_login: str = ""
    dataforseo_password: str = ""

    # File storage
    upload_dir: str = "./data/uploads"
    image_upload_dir: str = "./data/images"
    max_upload_size_mb: int = 25
    max_image_size_mb: int = 10

    # Public base URL for serving uploaded images (used by Shopify to fetch)
    public_base_url: str = "http://localhost:8000"

    # Shopify defaults
    default_eur_rate: float = 7.46
    default_markup: float = 2.5

    # Rate limiting
    rate_limit_analyse: str = "3/minute"   # expensive Claude Vision calls
    rate_limit_push: str = "5/minute"      # Shopify push
    rate_limit_upload: str = "10/minute"   # file uploads
    rate_limit_default: str = "60/minute"  # general API

    @model_validator(mode="after")
    def validate_production_config(self):
        """Ensure critical credentials are set in production mode."""
        if not self.debug:
            missing = []
            if not self.encryption_key:
                missing.append("ENCRYPTION_KEY")
            if not self.clerk_secret_key:
                missing.append("CLERK_SECRET_KEY")
            if not self.clerk_publishable_key:
                missing.append("CLERK_PUBLISHABLE_KEY")
            if not self.anthropic_api_key:
                missing.append("ANTHROPIC_API_KEY")
            if "localhost" in self.database_url:
                missing.append("DATABASE_URL (still using localhost)")
            if missing:
                raise ValueError(
                    f"Production config error — missing required settings: {', '.join(missing)}. "
                    f"Set DEBUG=true for development mode."
                )
        return self

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


@lru_cache()
def get_settings() -> Settings:
    return Settings()
