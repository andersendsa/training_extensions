# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.api.dependencies import get_dataset_service, get_media_service, get_project
from app.api.schemas.media import MediaView, MediaViewAdapter
from app.api.schemas.remote_storage import ConfigureRemoteStorage, CreateRemoteImages, RemoteStorageView
from app.models import Project
from app.services.dataset_service import DatasetService
from app.services.media_service import MediaService
from app.services.remote_storage_service import RemoteStorageService

router = APIRouter(prefix="/api/projects/{project_id}", tags=["Remote storage"])


def get_remote_storage_service(
    media_service: Annotated[MediaService, Depends(get_media_service)],
) -> RemoteStorageService:
    """Provide project storage using the request database session."""
    return RemoteStorageService(media_service.projects_dir.parent, media_service.db_session)


@router.put("/remote-storage", response_model=RemoteStorageView)
def configure_storage(
    project: Annotated[Project, Depends(get_project)],
    service: Annotated[RemoteStorageService, Depends(get_remote_storage_service)],
    body: ConfigureRemoteStorage,
) -> RemoteStorageView:
    """Configure one S3-compatible bucket per project. Credentials are write-only."""
    return RemoteStorageView.model_validate(service.configure(project.id, body).model_dump())


@router.get("/remote-storage", response_model=RemoteStorageView)
def get_storage(
    project: Annotated[Project, Depends(get_project)],
    service: Annotated[RemoteStorageService, Depends(get_remote_storage_service)],
) -> RemoteStorageView:
    """Return connection details without credentials."""
    return RemoteStorageView.model_validate(service.get_config(project.id).model_dump())


@router.delete("/remote-storage", status_code=204)
def delete_storage(
    project: Annotated[Project, Depends(get_project)],
    service: Annotated[RemoteStorageService, Depends(get_remote_storage_service)],
) -> Response:
    """Delete an unused connection without touching its bucket."""
    service.delete_config(project.id)
    return Response(status_code=204)


@router.post("/dataset/media:from-remote", response_model=list[MediaView], status_code=201)
def add_remote_images(
    project: Annotated[Project, Depends(get_project)],
    service: Annotated[RemoteStorageService, Depends(get_remote_storage_service)],
    media_service: Annotated[MediaService, Depends(get_media_service)],
    dataset_service: Annotated[DatasetService, Depends(get_dataset_service)],
    body: CreateRemoteImages,
) -> list[MediaView]:
    """Validate and create up to 100 remote image references atomically."""
    created = []
    try:
        for key in body.keys:
            media = service.create_image(project.id, key)
            created.append(media)
            dataset_service.create_dataset_item(project.id, project.task, media, user_reviewed=False)
        service.db_session.commit()
    except Exception:
        service.db_session.rollback()
        for media in created:
            media_service.get_media_binary_path(project.id, media).unlink(missing_ok=True)
        raise
    return [MediaViewAdapter.validate_python(media, from_attributes=True) for media in created]
