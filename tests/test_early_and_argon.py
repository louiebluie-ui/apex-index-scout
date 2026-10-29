from index_scout.contracts import IndexCandidateFinancials, EarningsReleaseDelta
from index_scout.inclusion_engine import evaluate_index_inclusion, has_index_inclusion_edge, build_early_index_brief
from index_scout.ingestor import redact_entity

def test_early_index_catalyst_brief_and_jurisdiction_redaction():
    clean = redact_entity("Mr. John Doe in Limassol, Cyprus reported Q3 results.", "SMCP", "SMALLCAP CORP")
    assert "[INSIDER_REDACTED]" in clean and "[JURISDICTION_REDACTED]" in clean
    st_dip = IndexCandidateFinancials(
        symbol="SMCP", market_cap_usd=2.0e9, iwf_float_pct=85.0, falr_ratio=1.2,
        ocf_ttm_usd=150e6, capex_ttm_usd=20e6, sbc_ttm_usd=10e6,
        total_debt_usd=100e6, cash_usd=50e6, prior_3q_gaap_ni_usd=5e6, prior_4q_gaap_ni_usd=-10e6,
        addv_20d_usd=10e6, bars_count=65, live_price=24.0, avwap_event=24.8, atr_14=0.75, intraday_ofi=-0.10
    )
    ed_flip = EarningsReleaseDelta(form_type="8-K", file_date="2026-10-28", q0_gaap_ni_usd=18.5e6, catalyst_summary="Q3 GAAP flip")
    assert evaluate_index_inclusion(st_dip, ed_flip, "Item 2.02") is None
    assert has_index_inclusion_edge(st_dip, ed_flip, "Item 2.02") is True
    brief = build_early_index_brief(st_dip, ed_flip, "Item 2.02", sym="SMCP")
    assert brief["symbol"] == "SMCP" and brief["track"] == "SP_600_SMALLCAP_GAAP_FLIP" and "BELOW_AVWAP" in brief["status"]
