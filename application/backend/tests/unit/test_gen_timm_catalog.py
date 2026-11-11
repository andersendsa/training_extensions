# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

from getitune.backend.lightning.models.classification.optimizers import TRANSFORMER_PREFIXES

from app.gen_timm_catalog import _ADAMW_PREFIXES, _defaults_for


def test_optimizer_defaults_use_shared_prefixes() -> None:
    assert _ADAMW_PREFIXES is TRANSFORMER_PREFIXES
    for prefix in TRANSFORMER_PREFIXES:
        assert _defaults_for(f"{prefix}_model") == {"learning_rate": 1e-4, "weight_decay": 5e-2}
    assert _defaults_for("resnet50") == {"learning_rate": 7e-3, "weight_decay": 1e-3}
