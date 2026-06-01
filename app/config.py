import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    # Set SECRET_KEY in .env for non-local environments.
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-this-secret-key")
    # Override DATABASE_URL in .env only when using a database other than local SQLite.
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        f"sqlite:///{BASE_DIR / 'instance' / 'app.db'}",
    ) or f"sqlite:///{BASE_DIR / 'instance' / 'app.db'}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SCHEDULER_API_ENABLED = False
    # Set GEMINI_API_KEY in .env with your real Google Gemini API key.
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    # Set OPENAI_API_KEY in .env with your real OpenAI API key for image generation.
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    # Switch IMAGE_PROVIDER in .env to "openai" or "gemini" as the default image provider.
    IMAGE_PROVIDER = os.environ.get("IMAGE_PROVIDER", "openai")
    OPENAI_IMAGE_MODEL = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")
    GEMINI_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "imagen-4.0-generate-001")


class DevelopmentConfig(Config):
    DEBUG = True
