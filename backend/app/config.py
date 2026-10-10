import pytesseract
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    hf_home: str = "D:\\hf_cache"
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "FinMate API"
    app_env: str = "development"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    database_url: str = "postgresql+psycopg2://finmate:finmate@localhost:5433/finmate"

    jwt_secret: str = Field(
        default="change-me-in-production-use-openssl-rand-hex-32",
        description="Set JWT_SECRET in .env for production",
    )
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    google_client_id: str | None = None
    google_client_secret: str | None = None
    auth_allow_mock_google: bool = False

    auth_rate_limit_max_attempts: int = 5
    auth_rate_limit_window_seconds: int = 60
    auth_rate_limit_redis_url: str | None = None

    # Email & SMTP Settings
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str = "noreply@finmate.com"
    email_provider: str = "smtp"
    resend_api_key: str | None = None
    resend_from_email: str = "onboarding@resend.dev"
    email_mock_mode: bool = False  # Enable explicitly only in isolated development/test environments
    verification_code_expire_minutes: int = 15
    password_reset_code_expire_minutes: int = 15

    embedding_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    finmate_use_embeddings: bool = True
    # Disable semantic memory retrieval for controlled research ablations.
    finmate_use_rag: bool = True
    intent_embedding_weight: float = 0.25

    alpha_vantage_api_key: str | None = None

    # FinMate QLoRA: folder containing adapter_config.json + adapter_model.safetensors (or a checkpoint-* subfolder with weights)
    finmate_lora_path: str = "app/ml/finmate-lora"
    # Use the bundled trained adapter by default. If weights or runtime dependencies are
    # unavailable, the orchestrator safely falls back to the specialist rules.
    finmate_use_llm: bool = True
    finmate_max_new_tokens: int = 256

    # Bounded agentic execution for requests spanning multiple specialists.
    finmate_agentic_mode: bool = True
    # Optional latency experiment: bypass only the final LLM synthesis after specialist execution.
    finmate_agentic_synthesis: bool = False
    agentic_max_steps: int = 3

    # Path to tesseract.exe when not on PATH (common on Windows after installer)
    tesseract_cmd: str | None = None

    @model_validator(mode="after")
    def validate_deployment_security(self) -> "Settings":
        environment = self.app_env.lower()
        if environment in {"production", "prod", "staging"}:
            target = "staging" if environment == "staging" else "production"
            if (
                self.jwt_secret == "change-me-in-production-use-openssl-rand-hex-32"
                or len(self.jwt_secret.strip()) < 32
            ):
                raise ValueError(f"JWT_SECRET must be at least 32 characters and non-placeholder in {target}")
            if self.email_mock_mode:
                raise ValueError(f"EMAIL_MOCK_MODE must be false in {target}")
            if self.email_provider.lower() == "resend":
                if not self.resend_api_key:
                    raise ValueError("RESEND_API_KEY is required when EMAIL_PROVIDER=resend")
            elif not self.smtp_user or not self.smtp_password:
                raise ValueError(f"SMTP_USER and SMTP_PASSWORD are required in {target}")
            if not self.auth_rate_limit_redis_url:
                raise ValueError(f"AUTH_RATE_LIMIT_REDIS_URL is required for shared {target} rate limiting")
            origins = [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
            if not origins or any(not origin.startswith("https://") for origin in origins):
                raise ValueError(f"CORS_ORIGINS must contain only explicit HTTPS origins in {target}")
            if self.auth_allow_mock_google:
                raise ValueError(f"AUTH_ALLOW_MOCK_GOOGLE must be false in {target}")
        return self


settings = Settings()

# pytesseract looks on system PATH by default; point it at tesseract_cmd
# from .env if provided, so OCR works even when tesseract isn't on PATH.
if settings.tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
