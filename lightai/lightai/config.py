"""Runtime configuration.

Defaults work on the booth laptop. Override with %LIGHTAI_DATA%\\config.yaml or environment
variables (LIGHTAI_DATA, LIGHTAI_PROJECT, LIGHTAI_QLC_URL, LIGHTAI_QLC_TLS_NAME).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

PACKAGE_DIR = Path(__file__).resolve().parent
LIGHTAI_DIR = PACKAGE_DIR.parent
REPO_ROOT = LIGHTAI_DIR.parent


def _default_data_dir() -> Path:
    return Path(os.environ.get("LIGHTAI_DATA", r"C:\lightai-data"))


@dataclass
class Config:
    data_dir: Path = field(default_factory=_default_data_dir)
    project_path: Path = REPO_ROOT / "SaveFile" / "Main Project.qxw"
    roles_template: Path = REPO_ROOT / "SaveFile" / "Blank Rig Template.qxw"
    overrides_path: Path = PACKAGE_DIR / "rig" / "overrides.yaml"
    learned_path: Path = PACKAGE_DIR / "rig" / "learned.yaml"
    colors_path: Path = LIGHTAI_DIR / "data" / "colors.yaml"
    fixture_dirs: list = field(
        default_factory=lambda: [
            Path.home() / "QLC+" / "Fixtures",
            Path(r"C:\qlcplus\Fixtures"),
            REPO_ROOT / "Fixtures",
            REPO_ROOT / "resources" / "fixtures",
        ]
    )
    qlc_url: str = "auto"
    qlc_host: str = "127.0.0.1"
    qlc_port: int = 9999
    tls_server_name: Optional[str] = None
    reload_strategy: str = "auto"
    encoder: str = "distilbert-base-uncased"
    retriever: str = "sentence-transformers/all-MiniLM-L6-v2"
    max_len: int = 64
    ort_threads: int = 2
    api_host: str = "127.0.0.1"
    api_port: int = 8765
    backups_keep: int = 50

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def backups_dir(self) -> Path:
        return self.project_path.parent / "backups"

    @property
    def sidecar_path(self) -> Path:
        return self.project_path.parent / "lightai-looks.json"

    def current_model_dir(self) -> Optional[Path]:
        marker = self.models_dir / "current.txt"
        if not marker.exists():
            return None
        name = marker.read_text(encoding="utf-8").strip()
        path = self.models_dir / name
        if name and (path / "model.int8.onnx").exists() and (path / "meta.json").exists():
            return path
        # current.txt is empty or names an incomplete folder: fall back to the newest complete model
        done = sorted((p for p in self.models_dir.glob("v*") if (p / "model.int8.onnx").exists() and (p / "meta.json").exists()),
                      key=lambda p: p.stat().st_mtime)
        if done:
            import sys

            print(f"[lightai] models/current.txt names '{name}', which is not a complete model; using {done[-1].name}", file=sys.stderr)
            return done[-1]
        return None


def load_config(path: Optional[Path] = None) -> Config:
    cfg = Config()
    cfg_path = path or cfg.data_dir / "config.yaml"
    if cfg_path.exists():
        doc = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        for key, value in doc.items():
            if not hasattr(cfg, key):
                continue
            current = getattr(cfg, key)
            if isinstance(current, Path):
                value = Path(value)
            elif key == "fixture_dirs":
                value = [Path(v) for v in value]
            setattr(cfg, key, value)
    env = os.environ
    if env.get("LIGHTAI_PROJECT"):
        cfg.project_path = Path(env["LIGHTAI_PROJECT"])
    if env.get("LIGHTAI_QLC_URL"):
        cfg.qlc_url = env["LIGHTAI_QLC_URL"]
    if env.get("LIGHTAI_QLC_TLS_NAME"):
        cfg.tls_server_name = env["LIGHTAI_QLC_TLS_NAME"]
    return cfg
