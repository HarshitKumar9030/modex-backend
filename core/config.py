from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List
import os
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    # Gemini API
    GEMINI_API_KEY: str = ""

    # MongoDB
    MONGODB_URL: str = "mongodb://localhost:27017"
    MONGODB_DB_NAME: str = "modex"

    # File storage
    UPLOAD_DIR: str = "./storage/uploads"
    OUTPUT_DIR: str = "./storage/outputs"
    MAX_FILE_SIZE_MB: int = 50

    # Allowed file extensions (pdf, images, audio, documents)
    ALLOWED_EXTENSIONS: str = "pdf,png,jpg,jpeg,webp,bmp,tiff,gif,mp3,wav,ogg,flac,aac,m4a,txt,md,csv,json,html,xml,rtf,log"

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    # AI Model — Gemini 2.5 Flash (fast & cheap)
    AI_MODEL: str = "gemini-2.5-flash"
    AI_MAX_TOKENS: int = 2048

    # Data retention policy — strict, auto-delete after N hours
    DATA_RETENTION_HOURS: int = 24

    # Beta access control
    BETA_ENABLED: bool = True
    ADMIN_SECRET: str = "modex-admin-secret-2026"

    # Mailgun Email Configuration
    MAILGUN_API_KEY: str = ""
    MAILGUN_DOMAIN: str = ""
    MAILGUN_URL: str = "https://api.mailgun.net"
    SENDER_EMAIL: str = "noreply@agfe.tech"
    SENDER_NAME: str = "Modex Team"

    # ---------- computed helpers ----------

    @property
    def allowed_extensions_list(self) -> List[str]:
        return [ext.strip().lower() for ext in self.ALLOWED_EXTENSIONS.split(",")]

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]

    @property
    def max_file_size_bytes(self) -> int:
        return self.MAX_FILE_SIZE_MB * 1024 * 1024

    @property
    def image_extensions(self) -> List[str]:
        return [e for e in self.allowed_extensions_list if e in ("png", "jpg", "jpeg", "webp", "bmp", "tiff", "gif")]

    @property
    def audio_extensions(self) -> List[str]:
        return [e for e in self.allowed_extensions_list if e in ("mp3", "wav", "ogg", "flac", "aac", "m4a")]

    @property
    def pdf_extensions(self) -> List[str]:
        return ["pdf"]

    @property
    def document_extensions(self) -> List[str]:
        return [e for e in self.allowed_extensions_list if e in ("txt", "md", "csv", "json", "html", "xml", "rtf", "log")]

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()

# Ensure storage directories exist
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
os.makedirs(settings.OUTPUT_DIR, exist_ok=True)
