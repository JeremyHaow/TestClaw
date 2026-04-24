from pydantic import BaseModel


class DiscoverModelsRequest(BaseModel):
    type: str
    api_key: str
    base_url: str | None = None


class ModelItem(BaseModel):
    id: str
    display_name: str | None = None
