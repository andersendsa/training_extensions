# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
from io import BytesIO
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from cryptography.fernet import Fernet
from PIL import Image

from app.db.schema import ProjectDB
from app.models.remote_storage import RemoteStorageCreate
from app.repositories.remote_storage_repo import RemoteStorageRepository
from app.services.media_service import MediaService
from app.services.remote_storage_service import RemoteStorageError, RemoteStorageService
from app.settings import get_settings


def image_bytes(color="red", size=(16, 16)):
    data = BytesIO()
    Image.new("RGB", size, color).save(data, format="PNG")
    return data.getvalue()


@pytest.fixture
def remote_storage(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("REMOTE_STORAGE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    project_id = uuid4()
    db_session.add(ProjectDB(id=str(project_id), name="Remote", task_type="classification"))
    db_session.flush()
    service = RemoteStorageService(tmp_path, db_session)
    client = MagicMock()
    client.__enter__.return_value = client
    monkeypatch.setattr("app.services.remote_storage_service.boto3.client", lambda *args, **kwargs: client)
    yield service, project_id, client
    get_settings.cache_clear()


def configure(service, project_id, mode="remote_only", immutable=False):
    return service.configure(
        project_id,
        RemoteStorageCreate(
            endpoint="https://s3.example.test",
            bucket="images",
            region="us-east-1",
            access_mode=mode,
            immutable=immutable,
            access_key="test-access",
            secret_key="test-secret",
        ),
    )


def respond(client, data, etag="one"):
    client.get_object.side_effect = None
    client.get_object.return_value = {"Body": BytesIO(data), "ContentLength": len(data), "ETag": etag}


@pytest.mark.parametrize("mode", ["remote_only", "local_cache"])
def test_create_and_read_modes(remote_storage, mode):
    service, project_id, client = remote_storage
    config = configure(service, project_id, mode, immutable=True)
    assert "secret" not in config.model_dump_json()
    stored = RemoteStorageRepository(service.db_session).for_project(str(project_id))
    assert "test-secret" not in stored.credentials
    assert "test-access" not in stored.credentials
    respond(client, image_bytes())
    media = service.create_image(project_id, "folder/picture.png")
    loader = MediaService(service.data_dir, db_session=service.db_session)
    path = loader.get_media_binary_path(project_id, media)
    assert path.exists() == (mode == "local_cache")
    respond(client, image_bytes())
    assert loader.read_media_bytes(project_id, media) == image_bytes()
    assert client.get_object.call_count == (1 if mode == "local_cache" else 2)
    assert path.exists() == (mode == "local_cache")


def test_mutable_refresh_and_deletion(remote_storage):
    service, project_id, client = remote_storage
    configure(service, project_id, "local_cache")
    respond(client, image_bytes())
    media = service.create_image(project_id, "picture.png")
    loader = MediaService(service.data_dir, db_session=service.db_session)
    respond(client, image_bytes("blue"), "two")
    assert loader.read_media_bytes(project_id, media) == image_bytes("blue")
    media = loader.get_media_by_id(project_id, media.id)
    assert media.remote["etag"] == "two"
    client.get_object.side_effect = ClientError({"Error": {"Code": "304"}}, "GetObject")
    assert loader.read_media_bytes(project_id, media) == image_bytes("blue")
    client.get_object.side_effect = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
    with pytest.raises(RemoteStorageError, match="no longer exists"):
        loader.read_media_bytes(project_id, media)


def test_invalid_images_and_dimension_changes(remote_storage):
    service, project_id, client = remote_storage
    configure(service, project_id)
    respond(client, b"not an image")
    with pytest.raises(RemoteStorageError, match="supported image"):
        service.create_image(project_id, "invalid.png")
    respond(client, image_bytes())
    media = service.create_image(project_id, "picture.png")
    respond(client, image_bytes(size=(20, 20)), "two")
    with pytest.raises(RemoteStorageError, match="dimensions or format"):
        MediaService(service.data_dir, db_session=service.db_session).read_media_bytes(project_id, media)


def test_reconfiguration_and_credential_rotation(remote_storage):
    service, project_id, client = remote_storage
    configure(service, project_id)
    respond(client, image_bytes())
    service.create_image(project_id, "picture.png")
    configure(service, project_id)
    with pytest.raises(RemoteStorageError, match="Remove remote image references"):
        configure(service, project_id, "local_cache")
    with pytest.raises(RemoteStorageError, match="Remove remote image references"):
        service.delete_config(project_id)


def test_cache_mismatched_to_database_is_not_reused(remote_storage):
    service, project_id, client = remote_storage
    configure(service, project_id, "local_cache")
    respond(client, image_bytes())
    media = service.create_image(project_id, "picture.png")
    loader = MediaService(service.data_dir, db_session=service.db_session)
    loader.get_media_binary_path(project_id, media).write_bytes(image_bytes("green"))
    respond(client, image_bytes("blue"), "two")
    assert loader.read_media_bytes(project_id, media) == image_bytes("blue")
    assert "IfNoneMatch" not in client.get_object.call_args.kwargs
