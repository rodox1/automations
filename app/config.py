import os
from dataclasses import dataclass
from dotenv import load_dotenv
load_dotenv()

@dataclass(frozen=True)
class Settings:
    avaya_port: int = int(os.getenv("AVAYA_PORT", "7070"))
    avaya_username: str = os.getenv("AVAYA_USERNAME", "")
    avaya_password: str = os.getenv("AVAYA_PASSWORD", "")
    avaya_verify_ssl: bool = os.getenv("AVAYA_VERIFY_SSL", "false").lower() in ("1","true","yes")
    avaya_timeout: int = int(os.getenv("AVAYA_TIMEOUT", "30"))
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./avaya.db")
    secret_key: str = os.getenv("SECRET_KEY", "CHANGE-ME")
    access_token_minutes: int = int(os.getenv("ACCESS_TOKEN_MINUTES", "60"))
    bootstrap_admin_user: str = os.getenv("BOOTSTRAP_ADMIN_USER", "")
    bootstrap_admin_password: str = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
settings = Settings()
