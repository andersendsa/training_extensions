# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, field_validator


class RemoteStorageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: HttpUrl
    bucket: str = Field(min_length=3, max_length=63, pattern=r"^[a-z0-9][a-z0-9.-]*[a-z0-9]$")
    region: str = Field(default="us-east-1", min_length=1, max_length=64)
    access_mode: Literal["remote_only", "local_cache"]
    immutable: bool

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: HttpUrl) -> HttpUrl:
        if value.username or value.password or value.query or value.fragment:
            raise ValueError("Endpoint must not contain credentials, query parameters or a fragment")
        return value


class RemoteStorageCreate(RemoteStorageConfig):
    access_key: SecretStr = Field(min_length=1)
    secret_key: SecretStr = Field(min_length=1)


class RemoteImagesCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keys: list[str] = Field(min_length=1, max_length=100)

    @field_validator("keys")
    @classmethod
    def validate_keys(cls, keys: list[str]) -> list[str]:
        if len(set(keys)) != len(keys):
            raise ValueError("Object keys must be unique within a request")
        if any(not key or len(key.encode()) > 1024 or any(ord(c) < 32 for c in key) for key in keys):
            raise ValueError("Invalid S3 object key")
        return keys
