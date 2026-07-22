"""Configuration settings for the Voice Summary application."""

import os
from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings

# The built-in JWT secret is intentionally obvious so a misconfigured deploy is
# caught (see the validator below), not silently shipped. It is public (it's in
# this file), so any environment using it has fully forgeable tokens.
_INSECURE_JWT_DEFAULT = "dev-only-insecure-secret-change-me"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Database
    database_url: str = "postgresql://username:password@localhost:5432/voicesummary"
    
    # S3 Configuration (optional — not needed when storage_mode=local)
    aws_access_key_id: str = "dummy"
    aws_secret_access_key: str = "dummy"
    aws_region: str = "us-east-1"
    s3_bucket_name: str = "dummy"
    
    # Sarvam AI — SOLE provider (STT + diarization + translation + analysis).
    # Comma-separated keys; rotated automatically on credit exhaustion / rate limits.
    sarvam_api_keys: Optional[str] = None
    sarvam_base_url: str = "https://api.sarvam.ai"
    sarvam_chat_model: str = "sarvam-105b"       # sarvam-105b (best Sarvam reasoner) | sarvam-30b (cheap worker)
    sarvam_stt_model: str = "saaras:v3"          # batch STT + diarization
    sarvam_stt_mode: str = "transcribe"          # 'transcribe' (original lang) | 'translate' (→ English)
    sarvam_translate_model: str = "mayura:v1"
    sarvam_output_language: str = "English"      # language for analysis text (notes/summaries/key_points/memory)

    # Reasoning provider — which LLM does post-call ANALYSIS (scoring / sentiment / digests).
    # STT + diarization ALWAYS stay on Sarvam (Saaras v3); this only swaps the reasoning brain.
    #   "sarvam" — Indic-tuned, default (no extra key needed)
    #   "gemini" — Gemini 3.1 Pro: stronger general reasoner (needs GEMINI_API_KEYS + billing)
    reasoning_provider: str = "sarvam"
    gemini_api_keys: Optional[str] = None            # comma-separated; rotated on 429/503
    gemini_model: str = "gemini-3.5-flash"
    gemini_thinking_level: str = "low"               # low | medium | high  (Gemini 3 thinking budget)
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    
    # Storage Configuration
    storage_mode: str = "local"  # "local", "s3", or "supabase"
    local_storage_path: str = "./local_storage"
    audio_source_path: str = "./Audio"

    # Supabase (hosted Postgres via database_url + optional Storage backend for call audio).
    # No Supabase Auth in this app — service_role key is server-side only, never sent to any client.
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_storage_bucket: str = "audio-calls"

    # Application
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    debug: bool = False
    cors_origins: str = "*"  # comma-separated allowed origins; "*" only for local dev

    # How many reverse-proxy hops in front of this app append their own
    # X-Forwarded-For entry (e.g. 1 for a single Render/Railway-style edge
    # proxy). Used to find the real client IP for rate-limiting without
    # trusting a caller-supplied hop further left in the chain.
    trusted_proxy_hops: int = 1

    # Auth — FastAPI is the sole identity provider for web + mobile (no separate NestJS auth).
    # MUST be overridden via .env in any environment beyond a throwaway local sandbox.
    jwt_secret_key: str = _INSECURE_JWT_DEFAULT
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 days
    # Explicit, narrow opt-out for the weak-secret check below — deliberately
    # NOT tied to `debug` (that flag is also reused for SQL echo and uvicorn
    # --reload, so a deploy that sets DEBUG=true for verbose logs would
    # otherwise silently disable the JWT check too).
    allow_insecure_jwt_secret: bool = False

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"

    @model_validator(mode="after")
    def _require_secure_jwt_secret(self):
        """Refuse to start with a weak/default JWT secret outside local dev.

        HS256 tokens are only as trustworthy as this secret — with the public
        default, anyone can forge a token for any user/org/role (full auth
        bypass + cross-tenant access). Fail loudly at boot instead of running
        forgeable. Local sandboxes set ALLOW_INSECURE_JWT_SECRET=true to keep
        the convenient default.
        """
        if self.allow_insecure_jwt_secret:
            return self
        if self.jwt_secret_key == _INSECURE_JWT_DEFAULT or len(self.jwt_secret_key) < 16:
            raise ValueError(
                "JWT_SECRET_KEY is unset/weak. Set a strong random JWT_SECRET_KEY "
                "(>=16 chars) in the environment for any non-local deployment, or "
                "set ALLOW_INSECURE_JWT_SECRET=true for a throwaway local sandbox."
            )
        return self


# Global settings instance
settings = Settings()
