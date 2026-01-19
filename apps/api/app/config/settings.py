from pydantic import BaseModel


class Settings(BaseModel):
    app_name: str = "data-analyst API"


settings = Settings()
