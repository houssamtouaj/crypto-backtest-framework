"""The ETF store (ETF spec Phase 0 §0.3) and its holdout guard (§0.5)."""
import hashlib
import json

import pandas as pd
import pytest

from perpbt.data.store import date_ms
from perpbt.etf.store import (
    MANIFEST, EtfStore, FileSource, HoldoutAccessError, StoreRefused, actions_file, prices_file, xdividends_file,
)
from tests.etf.synth import make_data


def _provenance(data):
    return {rel: FileSource("test", f"test://{rel}", {"k": 1}, "2026-10-24T00:00:00Z", "a" * 64)
            for rel in data.files()}


def _write(store, data, date="2026-10-24", **kw):
    return store.write(data, _provenance(data), download_date=date, holdout_end="2026-10-23", **kw)


@pytest.fixture
def written(tmp_path):
    store = EtfStore(tmp_path / "etf")
    data = make_data()
    manifest = _write(store, data, extra={"cross_check_basis": {"SPY": "raw"}})
    return store, data, manifest


def test_write_records_hashes_rows_and_dates(written):
    store, data, manifest = written
    assert manifest == store.manifest()
    assert manifest["download_date"] == "2026-10-24" and manifest["holdout_end"] == "2026-10-23"
    assert manifest["tickers"] == ["SPY", "QQQ", "IWM", "GLD"] and manifest["cross_check_basis"] == {"SPY": "raw"}
    assert set(manifest["files"]) == set(data.files())
    assert xdividends_file("QQQ") in manifest["files"] and xdividends_file("SPY") not in manifest["files"]
    entry = manifest["files"][prices_file("SPY")]
    assert entry["rows"] == len(data.prices["SPY"]) and entry["first"] == "2004-11-18"
    assert entry["last"] == "2007-01-31" and entry["raw_sha256"] == "a" * 64
    for rel, e in manifest["files"].items():
        assert e["sha256"] == hashlib.sha256((store.root / rel).read_bytes()).hexdigest()
    assert not list(store.root.rglob("*.tmp"))
    assert store.manifest_sha256() == hashlib.sha256((store.root / MANIFEST).read_bytes()).hexdigest()


def test_load_round_trips_with_actions(written):
    store, data, _ = written
    got = store.load("SPY", "2005-01-01", "2005-12-31")
    assert got["day_ms"].iloc[0] == date_ms("2005-01-03") and got["day_ms"].iloc[-1] == date_ms("2005-12-30")
    assert (got["dividend"] > 0).sum() == 4 and got["split"].eq(0).all()
    whole = store.load_all(allow_holdout=True)
    pd.testing.assert_frame_equal(whole.prices["IWM"], data.prices["IWM"])
    pd.testing.assert_frame_equal(whole.xdividends["QQQ"], data.xdividends["QQQ"])
    assert whole.xdividends["SPY"] is None


def test_holdout_guard(written):
    store, _, _ = written
    assert len(store.load("SPY", "2004-11-18", "2017-12-31")) > 0
    for end in ("2018-01-01", "2026-10-23"):
        with pytest.raises(HoldoutAccessError, match="2018-01-01"):
            store.load("SPY", "2004-11-18", end)
        with pytest.raises(HoldoutAccessError):
            store.load_rates("2004-11-01", end)
    assert len(store.load("SPY", "2004-11-18", "2018-01-01", allow_holdout=True)) > 0
    with pytest.raises(HoldoutAccessError):
        store.load_all()


def test_the_guard_runs_before_anything_is_read(tmp_path):
    with pytest.raises(HoldoutAccessError):
        EtfStore(tmp_path).load("SPY", "2004-11-18", "2019-01-01")


def test_loads_need_a_manifest_and_listed_files(written, tmp_path):
    with pytest.raises(StoreRefused, match="etf fetch"):
        EtfStore(tmp_path / "empty").load("SPY", "2005-01-01", "2005-02-01")
    store, _, _ = written
    with pytest.raises(StoreRefused, match="not in"):
        store.load("XLK", "2005-01-01", "2005-02-01")
    with pytest.raises(ValueError, match="before start"):
        store.load("SPY", "2005-02-01", "2005-01-01")


def test_any_rewrite_needs_refetch(written):
    store, data, _ = written
    for date in ("2026-10-24", "2026-11-01"):  # the same download date too: the snapshot may be frozen
        with pytest.raises(StoreRefused, match="--refetch"):
            _write(store, data, date=date)
    assert store.manifest()["download_date"] == "2026-10-24"


def test_a_changed_parquet_is_refused(written):
    store, _, _ = written
    path = store.root / prices_file("SPY")
    path.write_bytes(path.read_bytes() + b"\0")
    with pytest.raises(StoreRefused, match="manifest hash"):
        store.load("SPY", "2005-01-01", "2005-02-01")
    assert len(store.load("QQQ", "2005-01-01", "2005-02-01")) > 0


def test_refetch_moves_the_old_store(written):
    store, data, _ = written
    _write(store, data, date="2026-11-01", refetch=True)
    old = store.root / "_old" / "2026-10-24"
    assert json.loads((old / MANIFEST).read_text(encoding="utf-8"))["download_date"] == "2026-10-24"
    assert (old / actions_file("SPY")).is_file()
    assert store.manifest()["download_date"] == "2026-11-01"
    store.manifest_path.write_text(json.dumps({**store.manifest(), "download_date": "2026-10-24"}),
                                   encoding="utf-8")
    with pytest.raises(StoreRefused, match="exists"):
        _write(store, data, date="2026-12-01", refetch=True)


def test_an_interrupted_retire_leaves_no_manifest(written, monkeypatch):
    store, data, _ = written
    import shutil as shutil_mod

    real_move = shutil_mod.move
    moved = []

    def flaky(src, dst):
        moved.append(src)
        if len(moved) == 2:
            raise PermissionError("locked")
        return real_move(src, dst)

    monkeypatch.setattr(shutil_mod, "move", flaky)
    with pytest.raises(PermissionError):
        _write(store, data, date="2026-11-01", refetch=True)
    assert moved[0].endswith(MANIFEST) and store.manifest() is None  # nothing half-moved loads


def test_write_needs_provenance_for_every_file(tmp_path):
    data = make_data()
    prov = _provenance(data)
    del prov[prices_file("GLD")]
    with pytest.raises(ValueError, match="no provenance"):
        EtfStore(tmp_path).write(data, prov, download_date="2026-10-24", holdout_end="2026-10-23")


def test_etfdata_checks_columns_order_and_tickers():
    data = make_data()
    with pytest.raises(ValueError, match="expected columns"):
        type(data)(prices={**data.prices, "SPY": data.prices["SPY"].drop(columns="adj_close")},
                   actions=data.actions, rates=data.rates, xprices=data.xprices, xdividends=data.xdividends)
    with pytest.raises(ValueError, match="strictly increasing"):
        type(data)(prices={**data.prices, "SPY": data.prices["SPY"].iloc[::-1]}, actions=data.actions,
                   rates=data.rates, xprices=data.xprices, xdividends=data.xdividends)
    with pytest.raises(ValueError, match="tickers"):
        type(data)(prices=data.prices, actions={k: v for k, v in data.actions.items() if k != "GLD"},
                   rates=data.rates, xprices=data.xprices, xdividends=data.xdividends)
