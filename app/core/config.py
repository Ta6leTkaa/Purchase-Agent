from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Purchase Agent API"
    environment: str = "local"
    debug: bool = False


settings = Settings()
