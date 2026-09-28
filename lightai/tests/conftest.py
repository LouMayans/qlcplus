import copy
import shutil
from pathlib import Path

import pytest

from lightai.config import REPO_ROOT, load_config
from lightai.rig.model import Rig

MAIN = REPO_ROOT / "SaveFile" / "Main Project.qxw"
BLANK = REPO_ROOT / "SaveFile" / "Blank Rig Template.qxw"


@pytest.fixture()
def cfg(tmp_path):
    c = copy.copy(load_config())
    c.data_dir = tmp_path / "data"
    c.data_dir.mkdir()
    c.learned_path = tmp_path / "learned.yaml"
    proj_dir = tmp_path / "SaveFile"
    proj_dir.mkdir()
    shutil.copy(MAIN, proj_dir / "Main Project.qxw")
    shutil.copy(BLANK, proj_dir / "Blank Rig Template.qxw")
    c.project_path = proj_dir / "Main Project.qxw"
    return c


@pytest.fixture()
def blank_cfg(cfg):
    c = copy.copy(cfg)
    c.project_path = cfg.project_path.parent / "Blank Rig Template.qxw"
    return c


@pytest.fixture()
def rig(cfg):
    return Rig.load(cfg)


@pytest.fixture()
def blank_rig(blank_cfg):
    return Rig.load(blank_cfg)
