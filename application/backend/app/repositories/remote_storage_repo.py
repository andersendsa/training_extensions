# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.schema import MediaDB, RemoteStorageDB
from app.repositories.base import BaseRepository


class RemoteStorageRepository(BaseRepository[RemoteStorageDB]):
    def __init__(self, db: Session):
        super().__init__(db, RemoteStorageDB)

    def for_project(self, project_id: str) -> RemoteStorageDB | None:
        return self.db.scalar(select(RemoteStorageDB).where(RemoteStorageDB.project_id == project_id))

    def has_references(self, project_id: str) -> bool:
        return any(
            item.remote is not None for item in self.db.scalars(select(MediaDB).where(MediaDB.project_id == project_id))
        )

    def has_mutable_references(self, project_id: str) -> bool:
        return any(
            item.remote is not None and not item.remote["immutable"]
            for item in self.db.scalars(select(MediaDB).where(MediaDB.project_id == project_id))
        )
