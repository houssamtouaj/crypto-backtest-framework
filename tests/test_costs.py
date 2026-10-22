"""Fees, slippage, funding and R coefficients (spec §4.5, §4.7)."""
import pytest

from perpbt.config import ExecConfig
from perpbt.execution.costs import CostIdentityError, entry_fee, exit_costs, funding_amount, trade_costs

CFG = ExecConfig(fee_maker=0.0002, fee_taker=0.0005, slippage=0.0002)
cent = pytest.approx


def tc(**kw):
    args = dict(qty=100.0, entry_price=100.0, stop_dist=1.0, risk_usd=100.0, funding_usd=0.0, cfg=CFG)
    args.update(kw)
    return trade_costs(**args)


def test_target_trade_to_the_cent():
    t = tc(exit_ref=102.0, exit_role="maker")
    assert t.gross_pnl == cent(200.0) and t.fees == cent(2.00 + 2.04) and t.slippage_cost == 0.0
    assert t.net_pnl == cent(195.96) and t.net_r == cent(1.9596) and t.gross_r == cent(2.0)
    assert (t.c_maker_entry, t.c_maker_exit, t.c_taker_exit, t.c_slip) == (100.0, 102.0, 0.0, 0.0)
    assert t.cost_r == cent(0.0404)


def test_stop_trade_to_the_cent():
    t = tc(exit_ref=99.0, exit_role="taker")
    assert t.fees == cent(2.00 + 4.95) and t.slippage_cost == cent(1.98)
    assert t.net_pnl == cent(-108.93) and t.net_r == cent(-1.0893) and t.gross_r == cent(-1.0)
    assert (t.c_maker_entry, t.c_maker_exit, t.c_taker_exit, t.c_slip) == (100.0, 0.0, 99.0, 99.0)
    _, _, price = exit_costs(100.0, 99.0, "taker", CFG)
    assert price == cent(99.0 * (1 - 0.0002))


def test_funding_enters_net_r_and_the_identity_holds():
    t = tc(exit_ref=101.0, exit_role="taker", funding_usd=1.5)
    assert t.funding_r == cent(0.015) and t.funding == 1.5
    assert t.net_pnl == cent(t.gross_pnl - t.fees - t.slippage_cost - t.funding)
    assert t.net_r == cent(1.0 - 0.0002 * 100 - 0.0005 * 101 - 0.0002 * 101 - 0.015)


def test_repricing_with_the_coefficients_reproduces_net_r():
    t = tc(exit_ref=99.0, exit_role="taker", funding_usd=-0.7)
    for fm, ft, sl in ((0.0, 0.0005, 0.0), (0.0002, 0.0005, 0.001)):
        cfg = ExecConfig(fee_maker=fm, fee_taker=ft, slippage=sl)
        direct = tc(exit_ref=99.0, exit_role="taker", funding_usd=-0.7, cfg=cfg).net_r
        repriced = t.gross_r - fm * (t.c_maker_entry + t.c_maker_exit) - ft * t.c_taker_exit - sl * t.c_slip - t.funding_r
        assert repriced == cent(direct, abs=1e-12)


def test_helpers():
    assert entry_fee(2.0, 50.0, CFG) == cent(0.02)
    assert exit_costs(2.0, 50.0, "maker", CFG) == (cent(0.02), 0.0, 50.0)
    assert funding_amount(0.0001, 2.0, 50.0) == cent(0.01)
    assert funding_amount(-0.0001, 2.0, 50.0) == cent(-0.01)
    with pytest.raises(ValueError):
        exit_costs(1.0, 1.0, "market", CFG)


def test_identity_failure_raises(monkeypatch):
    import perpbt.execution.costs as costs

    monkeypatch.setattr(costs, "entry_fee", lambda qty, price, cfg: 1.0)  # a fee the coefficients do not know
    with pytest.raises(CostIdentityError):
        tc(exit_ref=102.0, exit_role="maker")
