import os
from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8003


class AuthConfig(BaseModel):
    jwt_secret: str = "your_jwt_secret"


class GitHubConfig(BaseModel):
    webhook_secret: str = ""


class GatewayConfig(BaseModel):
    base_url: str = "http://localhost:8002"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WEBHOOK_", env_nested_delimiter="__")

    env: str = "local"
    server: ServerConfig = ServerConfig()
    auth: AuthConfig = AuthConfig()
    github: GitHubConfig = GitHubConfig()
    gateway: GatewayConfig = GatewayConfig()


def load_settings() -> Settings:
    config_path = os.environ.get("CONFIG_PATH")
    if config_path and Path(config_path).exists():
        with open(config_path) as f:
            data = yaml.safe_load(f)
        return Settings(**data)
    return Settings()
