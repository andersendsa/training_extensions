# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
from app.models.remote_storage import RemoteImagesCreate, RemoteStorageConfig, RemoteStorageCreate


class RemoteStorageView(RemoteStorageConfig):
    pass


class ConfigureRemoteStorage(RemoteStorageCreate):
    pass


class CreateRemoteImages(RemoteImagesCreate):
    pass
