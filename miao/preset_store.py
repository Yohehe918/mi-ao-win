"""按键预设的 JSON 存储、导入导出与隔离恢复。

移植自 mi-ao 的 ButtonPresetStore.swift。
Windows 无 POSIX 权限语义，0700/0600 收紧步骤省略。
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import ConfigurationError
from .buttons import (
    ButtonBinding,
    ButtonPreset,
    ButtonPresetCatalog,
    RemoteButton,
    default_pointer_preset,
)

MAXIMUM_IMPORT_BYTES = 1_048_576
# v2：新增 activePresetId，并允许「自由映射」预设（任意按键→任意动作）
# 仍能读取 v1 文件（结构相同，v1 走严格校验）
CURRENT_SCHEMA_VERSION = 3


class LoadState(enum.Enum):
    DEFAULTS = "defaults"
    LOADED = "loaded"
    RECOVERED_INVALID = "recovered_invalid"
    UNSUPPORTED_VERSION = "unsupported_version"


@dataclass(frozen=True)
class CatalogSnapshot:
    catalog: ButtonPresetCatalog
    state: LoadState
    detail: str | None = None  # 隔离文件路径或版本号说明
    active_preset_id: str | None = None


class PresetStoreError(ConfigurationError):
    pass


class ButtonPresetStore:
    def __init__(self, file_path: str | Path) -> None:
        self.file_path = Path(file_path)

    def load(self) -> CatalogSnapshot:
        if not self.file_path.exists():
            return CatalogSnapshot(catalog=ButtonPresetCatalog(), state=LoadState.DEFAULTS)
        try:
            document = json.loads(self.file_path.read_text(encoding="utf-8"))
            schema_version = int(document["schemaVersion"])
            if schema_version > CURRENT_SCHEMA_VERSION:
                return CatalogSnapshot(
                    catalog=ButtonPresetCatalog(),
                    state=LoadState.UNSUPPORTED_VERSION,
                    detail=str(schema_version),
                )
            catalog = self._catalog_from(document, allow_free_mapping=schema_version >= 2)
            return CatalogSnapshot(
                catalog=catalog,
                state=LoadState.LOADED,
                active_preset_id=document.get("activePresetId"),
            )
        except Exception:
            # 隔离损坏文件并回退到内置默认
            try:
                self.file_path.parent.mkdir(parents=True, exist_ok=True)
                quarantine = self.file_path.with_name(
                    f"button-presets.invalid-"
                    f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
                )
                if quarantine.exists():
                    quarantine.unlink()
                self.file_path.replace(quarantine)
                return CatalogSnapshot(
                    catalog=ButtonPresetCatalog(),
                    state=LoadState.RECOVERED_INVALID,
                    detail=str(quarantine),
                )
            except OSError:
                return CatalogSnapshot(catalog=ButtonPresetCatalog(), state=LoadState.DEFAULTS)

    def save(
        self,
        catalog: ButtonPresetCatalog,
        active_preset_id: str | None = None,
        *,
        allow_free_mapping: bool = False,
    ) -> None:
        catalog.validate(allow_free_mapping=allow_free_mapping)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_atomic(self.file_path, self._encode(catalog, active_preset_id))

    def export(
        self,
        catalog: ButtonPresetCatalog,
        destination: str | Path,
        *,
        allow_free_mapping: bool = False,
    ) -> None:
        catalog.validate(allow_free_mapping=allow_free_mapping)
        self._write_atomic(Path(destination), self._encode(catalog, None))

    def import_catalog(self, source: str | Path) -> ButtonPresetCatalog:
        source = Path(source)
        if not source.is_file():
            raise PresetStoreError("只能导入本机普通 JSON 文件")
        size = source.stat().st_size
        if size > MAXIMUM_IMPORT_BYTES:
            raise PresetStoreError(f"按键配置文件过大（{size} 字节），上限为 1 MB")
        data = source.read_bytes()
        if len(data) > MAXIMUM_IMPORT_BYTES:
            raise PresetStoreError(f"按键配置文件过大（{len(data)} 字节），上限为 1 MB")
        try:
            document = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as error:
            raise PresetStoreError(f"按键配置不是有效 JSON：{error}") from error
        schema_version = int(document.get("schemaVersion", 0))
        if schema_version > CURRENT_SCHEMA_VERSION:
            raise PresetStoreError(
                f"按键配置来自较新的 schema v{schema_version}，当前版本无法安全写入"
            )
        return self._catalog_from(document, allow_free_mapping=schema_version >= 2)

    # ------------------------------------------------------------------

    @staticmethod
    def _encode(catalog: ButtonPresetCatalog, active_preset_id: str | None = None) -> bytes:
        document = {
            "schemaVersion": CURRENT_SCHEMA_VERSION,
            "presets": [
                {
                    "id": preset.id,
                    "name": preset.name,
                    "bindings": {
                        button.value: binding.to_json()
                        for button, binding in preset.bindings.items()
                    },
                    "gestures": {
                        button.value: {trigger: binding.to_json() for trigger, binding in gestures.items()}
                        for button, gestures in preset.gestures.items()
                    },
                }
                for preset in catalog.user_presets
            ],
        }
        if active_preset_id:
            document["activePresetId"] = active_preset_id
        return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )

    @staticmethod
    def _write_atomic(path: Path, data: bytes) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    @staticmethod
    def _catalog_from(document: dict, allow_free_mapping: bool = False) -> ButtonPresetCatalog:
        pointer = default_pointer_preset()
        presets: list[ButtonPreset] = []
        for stored in document.get("presets", []):
            bindings = dict(pointer.bindings)
            for raw_button, binding_data in stored.get("bindings", {}).items():
                try:
                    button = RemoteButton(raw_button)
                except ValueError as error:
                    raise ConfigurationError(f"按键配置包含未知按钮：{raw_button}") from error
                bindings[button] = ButtonBinding.from_json(binding_data)
            presets.append(
                ButtonPreset(
                    id=stored["id"],
                    name=stored["name"],
                    bindings=bindings,
                    required_buttons=pointer.required_buttons,
                    is_built_in=False,
                    gestures={
                        RemoteButton(button): {
                            trigger: ButtonBinding.from_json(binding)
                            for trigger, binding in gestures.items()
                        }
                        for button, gestures in stored.get("gestures", {}).items()
                    },
                )
            )
        catalog = ButtonPresetCatalog(user_presets=presets)
        catalog.validate(allow_free_mapping=allow_free_mapping)
        return catalog
