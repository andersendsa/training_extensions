# Copyright (C) 2025-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""System API Endpoints"""

import io
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from loguru import logger

from app.api.dependencies import get_license_service, get_log_dir, get_system_service
from app.api.schemas.system import CameraInfoView, DeviceInfoView, PlatformType, SystemInfoView
from app.services import SystemService
from app.services.license_service import LicenseService
from app.settings import get_settings

router = APIRouter(prefix="/api/system", tags=["System"])


def _get_platform() -> PlatformType:
    """Return the current operating system platform."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


@router.get("/info")
async def get_system_info(
    license_service: Annotated[LicenseService, Depends(get_license_service)],
) -> SystemInfoView:
    """Returns system information including license status and platform."""
    return SystemInfoView(license_accepted=license_service.is_accepted(), platform=_get_platform())


@router.get("/devices/inference")
async def get_inference_devices(
    system_service: Annotated[SystemService, Depends(get_system_service)],
) -> list[DeviceInfoView]:
    """Returns the list of available compute devices (CPU, Intel XPU)."""
    inference_devices = system_service.inference_devices()
    return [DeviceInfoView.model_validate(device, from_attributes=True) for device in inference_devices]


@router.get("/devices/training")
async def get_training_devices(
    system_service: Annotated[SystemService, Depends(get_system_service)],
) -> list[DeviceInfoView]:
    """Returns the list of available training devices (CPU, Intel XPU, NVIDIA CUDA)."""
    training_devices = system_service.training_devices()
    return [DeviceInfoView.model_validate(device, from_attributes=True) for device in training_devices]


@router.get("/devices/camera")
async def get_camera_devices(
    system_service: Annotated[SystemService, Depends(get_system_service)],
) -> list[CameraInfoView]:
    """Returns the list of available camera devices."""
    camera_devices = system_service.list_cameras()
    return [CameraInfoView.model_validate(device, from_attributes=True) for device in camera_devices]


@router.get("/metrics/memory")
async def get_memory(
    system_service: Annotated[SystemService, Depends(get_system_service)],
) -> dict:
    """Returns the used memory in MB and total available memory in MB."""
    used, total = system_service.get_memory_usage()
    return {"used": int(used), "total": int(total)}


def _collect_log_files(base_dir: Path, archive_prefix: str, *, recursive: bool = False) -> list[tuple[Path, str]]:
    """Collect log files from a directory, returning (file_path, archive_name) pairs.

    Only files within ``base_dir`` are included; symlinks that escape the
    directory are silently skipped to prevent path-traversal attacks.

    Args:
        base_dir: Root directory to scan for log files.
        archive_prefix: Prefix for the file paths inside the ZIP archive.
        recursive: If True, scan subdirectories recursively.

    Returns:
        List of (absolute_path, archive_name) tuples.
    """
    if not base_dir.is_dir():
        return []

    resolved_base = base_dir.resolve()
    pattern = "**/*.log*" if recursive else "*.log*"
    entries: list[tuple[Path, str]] = []

    for file_path in base_dir.glob(pattern):
        if not file_path.is_file():
            continue
        # Guard against symlinks escaping the log directory.
        if not file_path.resolve().is_relative_to(resolved_base):
            logger.warning("Skipping log file outside base directory: {}", file_path)
            continue
        arcname = f"{archive_prefix}/{file_path.relative_to(base_dir)}"
        entries.append((file_path, arcname))

    return entries


@router.get(
    "/diagnostics",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "ZIP archive containing application logs and system information",
            "content": {"application/zip": {}},
        },
    },
)
async def download_diagnostics(
    log_dir: Annotated[Path, Depends(get_log_dir)],
    system_service: Annotated[SystemService, Depends(get_system_service)],
) -> StreamingResponse:
    """Download a ZIP archive containing all application logs and system information.

    Bundles the main application logs, worker logs, and job logs together with
    a ``system_info.json`` manifest into a single ZIP file for easy sharing
    with the development team.
    """
    settings = get_settings()
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")

    # Collect log files from the three log directories.
    files: list[tuple[Path, str]] = []
    files.extend(_collect_log_files(log_dir, "app", recursive=False))

    workers_dir = log_dir / "workers"
    files.extend(_collect_log_files(workers_dir, "workers", recursive=True))

    jobs_dir = log_dir / "jobs"
    files.extend(_collect_log_files(jobs_dir, "jobs", recursive=True))

    logger.info("Bundling {} log file(s) into diagnostics archive", len(files))

    # Build the ZIP archive in memory.
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path, arcname in files:
            zf.write(file_path, arcname=arcname)

        # Generate and include a system information manifest.
        used, total = system_service.get_memory_usage()
        system_info = {
            "version": settings.version,
            "platform": _get_platform(),
            "timestamp": timestamp,
            "memory": {"used_mb": int(used), "total_mb": int(total)},
            "log_dir": str(log_dir),
        }
        zf.writestr("system_info.json", json.dumps(system_info, indent=2))

    zip_buffer.seek(0)
    filename = f"geti-diagnostics-{timestamp}.zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
