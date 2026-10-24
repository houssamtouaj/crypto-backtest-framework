"""Hands-off guards (ETF spec overview §7, Phase 0 §0.5): the holdout flag's grep test and the carry code lock."""
import re
from pathlib import Path

from perpbt.carry.prereg import carry_code_version, lock_path, read_lock

ROOT = Path(__file__).resolve().parents[2]
ETF = ROOT / "perpbt" / "etf"
ALLOWED = {"holdout.py", "forward.py", "validate.py"}


def test_allow_holdout_true_only_in_the_holdout_forward_and_validate_modules():
    users = {p.name for p in ETF.rglob("*.py")
             if re.search(r"allow_holdout\s*=\s*True", p.read_text(encoding="utf-8"))}
    assert users <= ALLOWED, f"allow_holdout=True outside {sorted(ALLOWED)}: {sorted(users - ALLOWED)}"
    assert "validate.py" in users  # the grep finds what it is meant to find


def test_the_carry_code_version_is_still_the_locked_one():
    # the carry forward reviews need CARRY_CODE_PATHS unchanged (overview §7)
    locked = read_lock(lock_path(ROOT / "configs" / "carry" / "prereg.yaml"))["code_version"]
    assert locked.startswith("7ce43816")
    assert carry_code_version() == locked


def test_the_store_guard_date_is_a_constant_not_config():
    text = (ETF / "store.py").read_text(encoding="utf-8")
    assert 'ETF_HOLDOUT_START = "2018-01-01"' in text
    assert "EtfDataConfig" not in text and "holdout_start" not in text
