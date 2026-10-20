# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
import json
import os
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from cryptography.fernet import Fernet, InvalidToken
from PIL import Image
from sqlalchemy.orm import Session

from app.db.schema import MediaDB, RemoteStorageDB
from app.models.media import ImageFormat, Media, MediaAdapter
from app.models.remote_storage import RemoteStorageConfig, RemoteStorageCreate
from app.repositories.media_repo import MediaRepository
from app.repositories.remote_storage_repo import RemoteStorageRepository
from app.services.base import BaseSessionManagedService
from app.settings import get_settings
from app.utils.images import convert_to_jpeg_compatible, crop_to_thumbnail

MAX_REMOTE_IMAGE_BYTES = 100 * 1024 * 1024


class RemoteStorageError(Exception):
    """A safe, credential-free message for a remote storage failure."""


class RemoteStorageService(BaseSessionManagedService):
    def __init__(self, data_dir: Path, db_session: Session | None = None):
        super().__init__(db_session)
        self.data_dir = data_dir

    @staticmethod
    def _cipher() -> Fernet:
        key = get_settings().remote_storage_encryption_key
        if key is None:
            raise RemoteStorageError("Configure REMOTE_STORAGE_ENCRYPTION_KEY before using remote storage")
        try:
            return Fernet(key.get_secret_value().encode())
        except ValueError:
            raise RemoteStorageError("REMOTE_STORAGE_ENCRYPTION_KEY is not a valid Fernet key") from None

    def _connection(self, project_id: UUID) -> RemoteStorageDB:
        connection = RemoteStorageRepository(self.db_session).for_project(str(project_id))
        if connection is None:
            raise RemoteStorageError("No remote storage configured for this project")
        return connection

    def get_config(self, project_id: UUID) -> RemoteStorageConfig:
        return RemoteStorageConfig.model_validate(self._connection(project_id).config)

    def configure(self, project_id: UUID, config: RemoteStorageCreate) -> RemoteStorageConfig:
        repo = RemoteStorageRepository(self.db_session)
        connection = repo.for_project(str(project_id))
        public_config = config.model_dump(mode="json", exclude={"access_key", "secret_key"})
        if connection and connection.config != public_config and repo.has_references(str(project_id)):
            raise RemoteStorageError("Remove remote image references before changing storage configuration")
        credentials = (
            self._cipher()
            .encrypt(
                json.dumps(
                    {
                        "aws_access_key_id": config.access_key.get_secret_value(),
                        "aws_secret_access_key": config.secret_key.get_secret_value(),
                    }
                ).encode()
            )
            .decode()
        )
        if connection is None:
            connection = RemoteStorageDB(project_id=str(project_id), config=public_config, credentials=credentials)
        else:
            connection.config = public_config
            connection.credentials = credentials
        repo.save(connection)
        return RemoteStorageConfig.model_validate(public_config)

    def delete_config(self, project_id: UUID) -> None:
        repo = RemoteStorageRepository(self.db_session)
        connection = self._connection(project_id)
        if repo.has_references(str(project_id)):
            raise RemoteStorageError("Remove remote image references before deleting storage configuration")
        repo.delete(connection.id)

    def _fetch(self, project_id: UUID, key: str, etag: str | None = None) -> tuple[bytes, str] | None:
        connection = self._connection(project_id)
        try:
            credentials = json.loads(self._cipher().decrypt(connection.credentials.encode()))
        except InvalidToken:
            raise RemoteStorageError("Cannot decrypt storage credentials; check the encryption key") from None
        config = RemoteStorageConfig.model_validate(connection.config)
        try:
            with boto3.client(
                "s3",
                endpoint_url=str(config.endpoint),
                region_name=config.region,
                **credentials,
                config=Config(
                    connect_timeout=10, read_timeout=30, retries={"max_attempts": 2}, s3={"addressing_style": "path"}
                ),
            ) as client:
                response = client.get_object(Bucket=config.bucket, Key=key, **({"IfNoneMatch": etag} if etag else {}))
                try:
                    if response["ContentLength"] > MAX_REMOTE_IMAGE_BYTES:
                        raise RemoteStorageError("Remote image exceeds the 100 MiB limit")
                    data = response["Body"].read(MAX_REMOTE_IMAGE_BYTES + 1)
                    if len(data) > MAX_REMOTE_IMAGE_BYTES:
                        raise RemoteStorageError("Remote image exceeds the 100 MiB limit")
                    return data, response["ETag"]
                finally:
                    response["Body"].close()
        except ClientError as error:
            code = str(error.response["Error"]["Code"])
            if code == "304" and etag:
                return None
            if code in {"NoSuchKey", "404"}:
                raise RemoteStorageError("Remote image no longer exists") from None
            raise RemoteStorageError("Cannot access remote image; check storage credentials and permissions") from None
        except BotoCoreError:
            raise RemoteStorageError("Remote storage request failed") from None

    @staticmethod
    def _inspect(data: bytes) -> tuple[ImageFormat, int, int]:
        try:
            with Image.open(BytesIO(data)) as image:
                image_format = ImageFormat((image.format or "").lower())
                image.load()
                return image_format, image.width, image.height
        except (OSError, ValueError, Image.DecompressionBombError):
            raise RemoteStorageError("Object is not a supported image") from None

    @staticmethod
    def _write_cache(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{uuid4()}.tmp")
        try:
            temporary.write_bytes(data)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def create_image(self, project_id: UUID, key: str) -> Media:
        config = self.get_config(project_id)
        result = self._fetch(project_id, key)
        if result is None:
            raise RemoteStorageError("Remote storage returned an unexpected empty response")
        data, etag = result
        image_format, width, height = self._inspect(data)
        media_id = uuid4()
        remote = {
            "key": key,
            "etag": etag,
            "access_mode": config.access_mode,
            "immutable": config.immutable,
            "sha256": sha256(data).hexdigest(),
        }
        media = MediaDB(
            id=str(media_id),
            project_id=str(project_id),
            type="image",
            name=PurePosixPath(key).stem[:255],
            format=image_format,
            width=width,
            height=height,
            size=len(data),
            source_id=None,
            remote=remote,
        )
        path = self.data_dir / "projects" / str(project_id) / "dataset" / f"{media_id}.{image_format}"
        try:
            if config.access_mode == "local_cache":
                self._write_cache(path, data)
            saved = MediaRepository(project_id=str(project_id), db=self.db_session).save(media)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return MediaAdapter.validate_python(saved)

    def read_image(self, project_id: UUID, media: Media, path: Path) -> bytes:
        remote = media.remote
        if remote is None:
            raise ValueError("Expected a remote image reference")
        cached = remote["access_mode"] == "local_cache" and path.is_file()
        cached_data = path.read_bytes() if cached else None
        # A concurrent reader may see a file from a different database transaction.
        if cached_data is not None and sha256(cached_data).hexdigest() != remote["sha256"]:
            cached_data = None
        if cached_data is not None and remote["immutable"]:
            return cached_data
        result = self._fetch(project_id, remote["key"], remote["etag"] if cached_data is not None else None)
        if result is None:
            if cached_data is None:
                raise RemoteStorageError("Remote storage returned an unexpected empty response")
            return cached_data
        data, etag = result
        image_format, width, height = self._inspect(data)
        if (image_format, width, height) != (media.format, media.width, media.height):
            raise RemoteStorageError(
                "Remote image dimensions or format changed; recreate its reference before annotating"
            )
        if remote["access_mode"] == "local_cache":
            self._write_cache(path, data)
        if etag != remote["etag"]:
            repo = MediaRepository(project_id=str(project_id), db=self.db_session)
            stored = repo.get_by_id(str(media.id))
            if stored is None:
                raise RemoteStorageError("Remote image reference was deleted")
            stored.remote = {**remote, "etag": etag, "sha256": sha256(data).hexdigest()}
            stored.size = len(data)
            repo.save(stored)
        return data
