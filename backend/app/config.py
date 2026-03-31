from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    hf_home: str = "D:\\hf_cache"
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "FinMate API"
    database_url: str = "postgresql+psycopg2://finmate:finmate@localhost:5432/finmate"

    jwt_secret: str = Field(
        default="change-me-in-production-use-openssl-rand-hex-32",
        description="Set JWT_SECRET in .env for production",
    )
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7

    embedding_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    intent_embedding_weight: float = 0.25

    alpha_vantage_api_key: str | None = None

    # FinMate QLoRA: folder containing adapter_config.json + adapter_model.safetensors (or a checkpoint-* subfolder with weights)
    finmate_lora_path: str = "app/ml/finmate-lora"
    # If true, chat uses the local LLM in app/ml/finmate.py; otherwise rule-based agents only
    finmate_use_llm: bool = False
    finmate_max_new_tokens: int = 256


settings = Settings()
