"""按键预设存储测试（移植自 ButtonPresetStoreTests.swift）。"""

import pytest

from miao.buttons import (
    ButtonAction,
    ButtonBinding,
    ButtonPreset,
    ButtonPresetCatalog,
    RemoteButton,
    default_pointer_preset,
)
from miao.preset_store import (
    MAXIMUM_IMPORT_BYTES,
    ButtonPresetStore,
    LoadState,
    PresetStoreError,
)


def _two_presets() -> ButtonPresetCatalog:
    pointer = default_pointer_preset()
    writing_bindings = dict(pointer.bindings)
    writing_bindings[RemoteButton.TV] = ButtonBinding.of_preset_switch("user.review")
    writing = ButtonPreset(
        id="user.writing",
        name="写作",
        bindings=writing_bindings,
        required_buttons=pointer.required_buttons,
        is_built_in=False,
    )
    review_bindings = dict(pointer.bindings)
    review_bindings[RemoteButton.TV] = ButtonBinding.of_preset_switch("user.writing")
    review_bindings[RemoteButton.VOLUME_UP] = ButtonBinding.of_action(ButtonAction.KEYBOARD_PAGE_UP)
    review = ButtonPreset(
        id="user.review",
        name="审阅",
        bindings=review_bindings,
        required_buttons=pointer.required_buttons,
        is_built_in=False,
    )
    return ButtonPresetCatalog(user_presets=[writing, review])


def test_save_load_round_trip(tmp_path):
    store = ButtonPresetStore(tmp_path / "button-presets.json")
    catalog = _two_presets()
    store.save(catalog)

    snapshot = store.load()
    assert snapshot.state is LoadState.LOADED
    loaded = snapshot.catalog.preset("user.review")
    assert loaded.binding_for(RemoteButton.VOLUME_UP).resolved_action is ButtonAction.KEYBOARD_PAGE_UP
    assert snapshot.catalog.preset("user.writing").binding_for(RemoteButton.TV).target_preset_id == "user.review"


def test_missing_file_loads_defaults(tmp_path):
    store = ButtonPresetStore(tmp_path / "nope.json")
    snapshot = store.load()
    assert snapshot.state is LoadState.DEFAULTS
    assert snapshot.catalog.user_presets == []


def test_export_import_round_trip_and_rejects_oversized(tmp_path):
    store = ButtonPresetStore(tmp_path / "live.json")
    catalog = _two_presets()
    export_path = tmp_path / "export.json"

    store.export(catalog, export_path)
    imported = store.import_catalog(export_path)
    assert store._encode(imported) == store._encode(catalog)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (MAXIMUM_IMPORT_BYTES + 1))
    with pytest.raises(PresetStoreError, match="过大"):
        store.import_catalog(oversized)


def test_corrupted_file_is_quarantined(tmp_path):
    file_path = tmp_path / "button-presets.json"
    file_path.write_text("这不是 JSON", encoding="utf-8")
    store = ButtonPresetStore(file_path)

    snapshot = store.load()
    assert snapshot.state is LoadState.RECOVERED_INVALID
    assert snapshot.catalog.user_presets == []
    assert not file_path.exists()
    quarantined = list(tmp_path.glob("button-presets.invalid-*.json"))
    assert len(quarantined) == 1
