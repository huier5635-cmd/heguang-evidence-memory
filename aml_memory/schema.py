import base64
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class ImageURL(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def inline_image(cls, value):
        if not value.startswith(("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,")):
            raise ValueError("Images must be inline PNG/JPEG/WebP data URIs")
        try:
            raw = base64.b64decode(value.split(",", 1)[1], validate=True)
        except ValueError:
            raise ValueError("Invalid Base64 image") from None
        if not raw or len(raw) > 10 * 1024 * 1024:
            raise ValueError("Image must contain 1 byte to 10 MiB")
        return value


class TextPart(BaseModel):
    type: Literal["text"]
    text: str = Field(min_length=1)


class ImagePart(BaseModel):
    type: Literal["image_url"]
    image_url: ImageURL


Content = str | list[TextPart | ImagePart]


def content_text(content: Content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(part.text for part in content if isinstance(part, TextPart))


class Message(BaseModel):
    role: str = Field(min_length=1)
    content: Content
    timestamp: StrictInt | None = None

    @field_validator("content")
    @classmethod
    def nonempty(cls, value):
        if not value or (isinstance(value, str) and not value.strip()):
            raise ValueError("content must not be empty")
        return value


class AddRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    request_id: str = Field(min_length=1)
    messages: list[Message] = Field(min_length=1)
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)


class SearchRequest(BaseModel):
    query: Content
    options: list[str] = Field(default_factory=list)
    user_id: str = Field(min_length=1)
    top_k: StrictInt = Field(ge=1, le=100)

    @field_validator("query")
    @classmethod
    def nonempty(cls, value):
        if not value or (isinstance(value, str) and not value.strip()):
            raise ValueError("query must not be empty")
        return value

