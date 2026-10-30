import re

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


SURROGATE = re.compile(r"[\ud800-\udfff]")


def valid_unicode(value):
    # Reject unpaired surrogate code points without allocating a source-sized
    # encoded copy. Valid non-ASCII source remains byte/character exact.
    if not value.isascii() and SURROGATE.search(value):
        raise ValueError("Invalid Unicode source")
    return value


class Message(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str = Field(min_length=1)
    content: str | None = ""
    timestamp: StrictInt | None = None
    tool_calls: list[dict] = Field(default_factory=list)
    function_call: dict | None = None
    extra: dict = Field(default_factory=dict)

    @field_validator("content")
    @classmethod
    def nonblank(cls, value):
        return valid_unicode(value) if value is not None else value

    @model_validator(mode="after")
    def contains_source(self):
        if not (self.content and self.content.strip()) and not self.tool_calls and not self.function_call and not self.extra:
            raise ValueError("Empty source")
        return self


class AddRequest(BaseModel):
    request_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    messages: list[Message] = Field(min_length=1)


class SearchRequest(BaseModel):
    user_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    top_k: StrictInt = Field(ge=1, le=100)

    @field_validator("query")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Empty query")
        return valid_unicode(value)

