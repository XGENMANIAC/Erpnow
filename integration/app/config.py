"""
Application settings and feature flags.

All configuration is pulled from environment variables (or a .env file).
Pydantic-settings validates and coerces types at startup — a missing
required variable causes an immediate, descriptive error.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── App ──────────────────────────────────────────────────
    app_env: str = "dev"

    # ── ERPNext ──────────────────────────────────────────────
    erpnext_base_url: str = "http://localhost:8080"
    erpnext_api_key: str = ""
    erpnext_api_secret: str = ""
    erpnext_company: str = "DEWMIX Hardware"
    erpnext_default_warehouse: str = "Stores - DX"
    erpnext_price_list: str = "Standard Selling"
    # Name of an existing Sales Taxes and Charges Template in ERPNext.
    # We reference it by name; ERPNext applies it and computes the tax.
    erpnext_sales_taxes_template: str = ""
    erpnext_webhook_secret: str = ""

    # ── Database ─────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./dev.db"

    # ── Redis ────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── NIM / LLM ────────────────────────────────────────────
    nim_api_key: str = ""
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    nim_model: str = "meta/llama-3.1-70b-instruct"

    # ── WhatsApp / Channels ──────────────────────────────────
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_webhook_secret: str = ""

    # ── M-Pesa (Phase 2) ─────────────────────────────────────
    mpesa_consumer_key: str = ""
    mpesa_consumer_secret: str = ""
    mpesa_shortcode: str = ""
    mpesa_passkey: str = ""
    mpesa_callback_url: str = ""

    # ── Sentry ───────────────────────────────────────────────
    sentry_dsn: str = ""

    # ── Feature flags ────────────────────────────────────────
    feature_website_buy: bool = False
    feature_extra_channels: bool = False

    # ── Derived helpers ──────────────────────────────────────
    @property
    def is_production(self) -> bool:
        return self.app_env == "prod"

    @property
    def debug(self) -> bool:
        return self.app_env == "dev"


# Module-level singleton — import this everywhere
settings = Settings()
