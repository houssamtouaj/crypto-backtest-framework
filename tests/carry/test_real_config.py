"""The committed carry configs fit the Phase 5 runner (no market data is read, nothing is run or written)."""
from pathlib import Path

from perpbt.carry.prereg import carry_code_version, load_prereg, lock_path, read_lock
from perpbt.carry.runner import check_data_config, holdout_period, insample_period, variants
from perpbt.config import DataConfig, load_yaml

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "configs" / "carry" / "prereg.yaml"


def test_real_configs_fit_the_runner():
    prereg = load_prereg(PREREG)
    check_data_config(prereg, load_yaml(ROOT / "configs" / "carry" / "data.yaml", DataConfig))
    h = prereg.prereg_hash()
    assert h == read_lock(lock_path(PREREG))["prereg_hash"]
    ids = [v.variant_id(h, p) for p in (insample_period(prereg), holdout_period(prereg)) for v in variants(prereg)]
    assert len(set(ids)) == len(ids) == 2 * 77


def test_carry_code_version_is_hex64():
    cv = carry_code_version()
    assert len(cv) == 64 and int(cv, 16) >= 0
