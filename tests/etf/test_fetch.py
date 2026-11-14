"""``perpbt etf fetch`` and ``perpbt etf validate`` (ETF spec Phase 0 §0.6) with fake sources; no network."""
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from perpbt.cli import main
from perpbt.data.store import date_ms
from perpbt.etf import fetch as fetch_mod
from perpbt.etf.config import load_data_config
from perpbt.etf.corrections import corrections_hash, load_corrections
from perpbt.etf.fetch import run_fetch
from perpbt.etf.sources import Fetched, SourceError
from perpbt.etf.store import EtfStore, FileSource, StoreRefused, day_iso
from tests.etf.synth import TICKERS, make_data

ROOT = Path(__file__).resolve().parents[2]
END = date(2018, 1, 31)  # reaches the holdout, so holdout_end is valid


class FakeSources:
    """The synthetic snapshot served as raw bytes in each source's own format; records every call."""

    def __init__(self, data=None):
        self.data = data or make_data(end=END)
        self.calls = []

    def _got(self, raw, source):
        return Fetched(raw, FileSource(source, f"fake://{source}", {}, "2026-10-24T00:00:00Z",
                                       hashlib.sha256(raw).hexdigest()))

    def yahoo(self, ticker, start, end):
        self.calls.append(("yahoo", ticker, start, end))
        p = self.data.prices[ticker].merge(self.data.actions[ticker], on="day_ms", how="left").fillna(0.0)
        p = p[p["day_ms"] < date_ms(end)]
        lines = ["Date,Open,High,Low,Close,Adj Close,Volume,Dividends,Stock Splits,Capital Gains"]
        for r in p.itertuples():
            lines.append(f"{day_iso(r.day_ms)},{r.open!r},{r.high!r},{r.low!r},{r.close!r},{r.adj_close!r},"
                         f"{r.volume!r},{r.dividend!r},{r.split!r},{r.capital_gain!r}")
        return self._got(("\n".join(lines) + "\n").encode(), "yahoo")

    def fred(self, series, start):
        self.calls.append(("fred", series, start))
        rows = [f"{day_iso(d)},{v}" for d, v in zip(self.data.rates["day_ms"], self.data.rates["rate_pct"])]
        return self._got(("observation_date,DTB3\n" + "\n".join(rows) + "\n").encode(), "fred")

    @staticmethod
    def _us(ms):
        y, m, d = day_iso(ms).split("-")
        return f"{m}/{d}/{y}"

    def nasdaq_history(self, ticker, start, end):
        self.calls.append(("nasdaq_history", ticker, start, end))
        x = self.data.xprices[ticker]
        rows = [{"date": self._us(r.day_ms), "open": f"{r.open!r}", "close": f"${r.close!r}"}
                for r in x.iloc[::-1].itertuples()]
        return self._got(json.dumps({"data": {"tradesTable": {"rows": rows}}}).encode(), "nasdaq")

    def nasdaq_dividends(self, ticker):
        self.calls.append(("nasdaq_dividends", ticker))
        xd = self.data.xdividends[ticker]
        rows = None if xd is None else [{"exOrEffDate": self._us(r.day_ms), "type": "Cash",
                                         "amount": f"${r.dividend!r}"} for r in xd.itertuples()]
        message = "Dividend History for Non-Nasdaq symbols is not available" if xd is None else None
        return self._got(json.dumps({"data": {"dividends": {"rows": rows}}, "message": message}).encode(), "nasdaq")

    def kwargs(self):
        return {"yahoo": self.yahoo, "fred": self.fred, "nasdaq_history": self.nasdaq_history,
                "nasdaq_dividends": self.nasdaq_dividends}


EMPTY_CORRECTIONS = "prices: []\ndividends: []\naccepted: []\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A working directory with configs/etf/{data,corrections}.yaml whose data_dir is data/etf."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs" / "etf").mkdir(parents=True)
    (tmp_path / "configs" / "etf" / "data.yaml").write_text(
        (ROOT / "configs" / "etf" / "data.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "configs" / "etf" / "corrections.yaml").write_text(EMPTY_CORRECTIONS, encoding="utf-8")
    return tmp_path


CFG = "configs/etf/data.yaml"
TODAY = date(2018, 2, 1)


def test_fetch_writes_the_store_and_the_dates(env):
    src = FakeSources()
    out = run_fetch(CFG, today=TODAY, **src.kwargs())
    assert out.ok and out.holdout_end == "2018-01-31" and out.download_date == "2018-02-01"
    cfg = load_data_config(CFG)
    assert (cfg.holdout_end, cfg.download_date) == ("2018-01-31", "2018-02-01")
    assert ("yahoo", "SPY", "2004-11-18", "2018-02-01") in src.calls
    assert ("fred", "DTB3", "2004-11-01") in src.calls
    assert ("nasdaq_history", "GLD", "2004-11-18", "2018-01-31") in src.calls
    m = EtfStore("data/etf").manifest()
    assert m["cross_check_basis"] == {t: "raw" for t in TICKERS} and m["cross_check_source"] == "nasdaq"
    assert m["corrections_hash"] == corrections_hash(load_corrections("configs/etf/corrections.yaml"))
    crlf = Path("configs/etf/corrections.yaml")
    crlf.write_bytes(crlf.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n") + b"# a comment\r\n")
    assert main(["etf", "validate"]) == 0  # line endings and comments are not content
    assert [v["rule"] for v in m["validation"]][0] == "1 calendar" and all(v["ok"] for v in m["validation"])
    assert m["files"]["SPY/prices.parquet"]["source"] == "yahoo"
    assert main(["etf", "validate"]) == 0


def test_fetch_applies_corrections_and_records_them(env):
    data = make_data(end=END)
    x = data.xprices["SPY"].copy()
    x.loc[10, "open"] *= 1.02  # the cross-check open is right, Yahoo's is wrong
    data = type(data)(prices=data.prices, actions=data.actions, rates=data.rates,
                      xprices={**data.xprices, "SPY": x}, xdividends=data.xdividends)
    day = day_iso(x["day_ms"].iloc[10])
    new_open = float(x.loc[10, "open"])
    yahoo = data.prices["SPY"].set_index("day_ms").loc[x["day_ms"].iloc[10]]
    old_open, old_high = float(yahoo["open"]), float(yahoo["high"])
    high = max(new_open, old_high)
    assert not run_fetch(CFG, today=TODAY, **FakeSources(data).kwargs()).ok

    def fix(was_open):
        Path("configs/etf/corrections.yaml").write_text(
            f'prices:\n  - {{ticker: SPY, date: "{day}", open: {new_open!r}, high: {high!r}, '
            f"was: {{open: {was_open!r}, high: {old_high!r}}}, reason: stale}}\n"
            "dividends: []\naccepted: []\n", encoding="utf-8")

    fix(old_open * 1.01)  # reviewed against another Yahoo value: the fix is stale and refused
    with pytest.raises(ValueError, match="stale price fix"):
        run_fetch(CFG, today=TODAY, **FakeSources(data).kwargs())
    fix(old_open)
    out = run_fetch(CFG, today=TODAY, **FakeSources(data).kwargs())
    assert out.ok
    assert out.manifest["corrections"]["prices"][0]["date"] == day
    stored = EtfStore("data/etf").load("SPY", day, day)
    assert stored["open"].item() == x.loc[10, "open"]
    Path("configs/etf/corrections.yaml").write_text(EMPTY_CORRECTIONS, encoding="utf-8")
    assert main(["etf", "validate"]) == 1  # the corrections changed since the fetch


def test_failed_validation_writes_nothing(env, capsys):
    data = make_data(end=END)
    r = data.rates.copy()
    r.loc[5, "rate_pct"] = 50.0
    data = type(data)(prices=data.prices, actions=data.actions, rates=r, xprices=data.xprices,
                      xdividends=data.xdividends)
    before = Path(CFG).read_text(encoding="utf-8")
    out = run_fetch(CFG, today=TODAY, **FakeSources(data).kwargs())
    assert not out.ok and out.manifest is None
    assert not Path("data/etf").exists()
    assert Path(CFG).read_text(encoding="utf-8") == before


def test_fetch_refuses_an_existing_store_before_downloading(env):
    run_fetch(CFG, today=TODAY, **FakeSources().kwargs())
    src = FakeSources()
    for day in (TODAY, date(2018, 2, 2)):  # the same download day too: the stored snapshot may be frozen
        with pytest.raises(StoreRefused, match="--refetch"):
            run_fetch(CFG, today=day, **src.kwargs())
    assert src.calls == []
    out = run_fetch(CFG, today=date(2018, 2, 2), refetch=True, **src.kwargs())
    assert out.ok and Path("data/etf/_old/2018-02-01/manifest.json").is_file()
    assert load_data_config(CFG).download_date == "2018-02-02"


def test_cli_fetch_exit_codes(env, monkeypatch, capsys):
    src = FakeSources()
    # the CLI calls run_fetch with its keyword defaults (the real sources, today): swap the defaults
    defaults = list(fetch_mod.run_fetch.__kwdefaults__.items())
    monkeypatch.setattr(fetch_mod.run_fetch, "__kwdefaults__",
                        {**dict(defaults), "today": TODAY, **src.kwargs()})
    assert main(["etf", "fetch"]) == 0
    assert "holdout_end 2018-01-31" in capsys.readouterr().out
    monkeypatch.setattr(fetch_mod.run_fetch, "__kwdefaults__",
                        {**dict(defaults), "today": date(2018, 2, 2), **src.kwargs()})
    assert main(["etf", "fetch"]) == 1
    assert "refused" in capsys.readouterr().err

    def down(*a, **k):
        raise SourceError("yahoo SPY: failed after 4 attempts")

    monkeypatch.setattr(fetch_mod.run_fetch, "__kwdefaults__",
                        {**dict(defaults), "today": date(2018, 2, 2), **src.kwargs(), "yahoo": down})
    assert main(["etf", "fetch", "--refetch"]) == 1
    assert "failed after 4 attempts" in capsys.readouterr().err
    assert main(["etf", "fetch", "--config", "missing.yaml"]) == 2


def test_cli_validate_without_a_store(env, capsys):
    assert main(["etf", "validate"]) == 1
    assert "etf fetch" in capsys.readouterr().err
    assert main(["etf", "validate", "--config", "missing.yaml"]) == 2


def test_validate_refuses_a_config_that_does_not_match_the_store(env, capsys):
    run_fetch(CFG, today=TODAY, **FakeSources().kwargs())
    from perpbt.etf.fetch import set_fetch_dates
    set_fetch_dates(CFG, download_date="2018-02-03", holdout_end="2018-01-31")
    assert main(["etf", "validate"]) == 1
    assert "download_date" in capsys.readouterr().err


def test_validate_refuses_a_tampered_parquet(env, capsys):
    run_fetch(CFG, today=TODAY, **FakeSources().kwargs())
    path = Path("data/etf/SPY/prices.parquet")
    path.write_bytes(path.read_bytes() + b"\0")
    assert main(["etf", "validate"]) == 1
    assert "differs from its manifest hash" in capsys.readouterr().err


def test_a_nasdaq_error_reply_fails_the_fetch(env):
    src = FakeSources()
    src.nasdaq_dividends = lambda t: src._got(b'{"data": null, "status": {"rCode": 400}}', "nasdaq")
    with pytest.raises(SourceError, match="no data"):
        run_fetch(CFG, today=TODAY, **src.kwargs())


def test_a_missing_required_dividend_history_fails_validation(env):
    data = make_data(end=END)
    data = type(data)(prices=data.prices, actions=data.actions, rates=data.rates, xprices=data.xprices,
                      xdividends={**data.xdividends, "QQQ": None})
    out = run_fetch(CFG, today=TODAY, **FakeSources(data).kwargs())
    assert not out.ok
    assert "QQQ: no cross-check dividend history, but QQQ is in cross_check.dividend_tickers" in out.results[5].failures
