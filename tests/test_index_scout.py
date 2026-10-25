import json, httpx
from index_scout.contracts import IndexCandidateFinancials, EarningsReleaseDelta
from index_scout.harvester import SYM_RE, strip_sec_html, fetch_filing_text
from index_scout.ingestor import fetch_index_candidate_financials, extract_earnings_delta, redact_entity, _norm_signed_usd
from index_scout.inclusion_engine import evaluate_index_inclusion, has_index_inclusion_edge, build_quant_bridge

def _base_ic(**kw) -> IndexCandidateFinancials:
    d = dict(symbol="SMCP", market_cap_usd=2.4e9, iwf_float_pct=88.0, falr_ratio=1.15, ocf_ttm_usd=180e6, capex_ttm_usd=30e6, sbc_ttm_usd=15e6, total_debt_usd=150e6, cash_usd=90e6, prior_3q_gaap_ni_usd=-12e6, prior_4q_gaap_ni_usd=-18e6, api_q0_gaap_ni_usd=0.0, is_api_synced_today=False, addv_20d_usd=14e6, bars_count=120, live_price=32.0, avwap_event=31.2, atr_14=0.95, intraday_ofi=0.28)
    d.update(kw); return IndexCandidateFinancials(**d)

def test_1_sp600_day0_gaap_flip_passes_and_computes_passive_demand():
    r = evaluate_index_inclusion(_base_ic(), EarningsReleaseDelta(form_type="8-K", q0_gaap_ni_usd=22e6), "Item 2.02 Q3 GAAP Net Income $22M")
    assert r is not None and r["track"] == "SP_600_SMALLCAP_GAAP_FLIP" and r["ttm_gaap_ni_usd"] == 10e6 and r["passive_demand_days"] > 10.0

def test_2_sp400_midcap_band_classification():
    r = evaluate_index_inclusion(_base_ic(market_cap_usd=9.5e9, ocf_ttm_usd=650e6, addv_20d_usd=45e6), EarningsReleaseDelta(form_type="8-K", q0_gaap_ni_usd=35e6), "Item 2.02")
    assert r is not None and r["track"] == "SP_400_MIDCAP_GAAP_FLIP" and r["index_tier"] == "SP_400_MIDCAP"

def test_3_blocks_when_q0_still_insufficient_to_flip_ttm_positive():
    assert evaluate_index_inclusion(_base_ic(prior_3q_gaap_ni_usd=-25e6), EarningsReleaseDelta(form_type="8-K", q0_gaap_ni_usd=18e6), "Item 2.02") is None

def test_4_blocks_already_positive_4q_unless_vacancy_active():
    st_pos = _base_ic(prior_3q_gaap_ni_usd=80e6, prior_4q_gaap_ni_usd=100e6)
    assert evaluate_index_inclusion(st_pos, EarningsReleaseDelta(form_type="10-Q", q0_gaap_ni_usd=35e6), "10-Q", vacancy_active=False) is None
    r_vac = evaluate_index_inclusion(st_pos, EarningsReleaseDelta(form_type="10-Q", q0_gaap_ni_usd=35e6), "10-Q", vacancy_active=True)
    assert r_vac is not None and r_vac["track"] == "SP_600_SMALLCAP_VACANCY_LEADER"

def test_5_blocks_low_iwf_float_and_low_falr_liquidity():
    assert evaluate_index_inclusion(_base_ic(iwf_float_pct=42.0), EarningsReleaseDelta(form_type="8-K", q0_gaap_ni_usd=25e6), "8-K") is None
    assert evaluate_index_inclusion(_base_ic(falr_ratio=0.60), EarningsReleaseDelta(form_type="8-K", q0_gaap_ni_usd=25e6), "8-K") is None

def test_6_coiled_spring_day0_dip_queues_then_triggers_on_avwap_reclaim():
    ed = EarningsReleaseDelta(form_type="8-K", file_date="2026-10-23", q0_gaap_ni_usd=24e6)
    dip = _base_ic(live_price=30.1, avwap_event=31.2, intraday_ofi=-0.10)
    assert evaluate_index_inclusion(dip, ed, "Item 2.02") is None and has_index_inclusion_edge(dip, ed, "Item 2.02") is True

def test_7_asc205_audit_footnote_safe_while_real_distress_blocked():
    benign = "Management evaluated whether there is substantial doubt about continuing as a going concern and found none."
    toxic = "There exists substantial doubt about the Company's ability to continue as a going concern."
    ed = EarningsReleaseDelta(form_type="10-K", q0_gaap_ni_usd=25e6)
    assert evaluate_index_inclusion(_base_ic(), ed, benign) is not None and evaluate_index_inclusion(_base_ic(), ed, toxic) is None

def test_8_bitemporal_fmp_unsynced_vs_synced_quarter_alignment():
    bars = [{"date": f"2026-{(i//28)+7:02d}-{(i%28)+1:02d}", "close": 35.0, "high": 36.0, "low": 34.0, "volume": 600000} for i in range(65, 0, -1)]
    inc_unsynced = [{"filingDate": "2026-08-05", "netIncome": -4e6}, {"filingDate": "2026-05-05", "netIncome": -4e6}, {"filingDate": "2026-02-05", "netIncome": -4e6}, {"filingDate": "2025-11-05", "netIncome": -6e6}]
    def mock_fmp(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if "profile" in u: return httpx.Response(200, json=[{"symbol": "SMCP", "price": 35.0, "marketCap": 2.2e9, "country": "US", "sector": "Technology", "industry": "Software"}])
        if "shares-float" in u: return httpx.Response(200, json=[{"freeFloat": 82.0}])
        if "historical-price-eod" in u: return httpx.Response(200, json=bars)
        if "income-statement" in u: return httpx.Response(200, json=inc_unsynced)
        if "cash-flow" in u: return httpx.Response(200, json=[{"operatingCashFlow": 45e6, "capitalExpenditure": -5e6, "stockBasedCompensation": 3e6}] * 4)
        return httpx.Response(200, json=[{"totalDebt": 80e6, "cashAndShortTermInvestments": 60e6}])
    with httpx.Client(transport=httpx.MockTransport(mock_fmp)) as c:
        st = fetch_index_candidate_financials(c, "SMCP", "2026-10-23", "k", {"AAPL"})
        assert st is not None and st.is_api_synced_today is False and st.prior_3q_gaap_ni_usd == -12e6 and st.prior_4q_gaap_ni_usd == -18e6

def test_9_signed_usd_norm_and_gemini_parenthetical_negative_coercion():
    assert _norm_signed_usd(22.5) == 22_500_000.0 and _norm_signed_usd(-8.4) == -8_400_000.0
    def mock_gem(req: httpx.Request) -> httpx.Response:
        p = {"q0_gaap_ni_usd": "$24.5", "q0_operating_cf_usd": "(3.2)", "catalyst_summary": "Q3 GAAP flip"}
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "```json\n" + json.dumps(p) + "\n```"}]}}]})
    with httpx.Client(transport=httpx.MockTransport(mock_gem)) as c:
        ed = extract_earnings_delta(c, "SMCP", "8-K", "Net income $24.5M", "k", "ACME CORP (DE) | TARGET INC (SMCP)")
        assert ed.q0_gaap_ni_usd == 24_500_000.0 and ed.q0_operating_cf_usd == -3_200_000.0

def test_10_table_density_slicer_and_uppercase_ex99_discovery():
    early = ("Narrative income mention $1M. " + "x" * 1500) * 6
    table = "Consolidated Statements of Operations | $240 | $180 | $95 | $42 | $24"
    assert "$240 | $180" in strip_sec_html(early + table, max_chars=5000)
    def mock_sec(req: httpx.Request) -> httpx.Response:
        if "index.json" in str(req.url): return httpx.Response(200, json={"directory": {"item": [{"name": "d8k.htm"}, {"name": "EX99-1_EARNINGS.HTM"}]}})
        if "EX99-1" in str(req.url): return httpx.Response(200, text="<html><body>GAAP Net Income $28 million</body></html>")
        return httpx.Response(200, text="<html><body>Item 2.02 Shell</body></html>")
    with httpx.Client(transport=httpx.MockTransport(mock_sec)) as c:
        assert "GAAP Net Income $28 million" in fetch_filing_text(c, ["0001234567"], "0001234567-26-000001", ["d8k.htm"])

def test_11_two_letter_tickers_and_multi_entity_redaction():
    ent = "HOLDING LP (DE) (CIK 0001) | FOOT LOCKER INC (FL) (CIK 0002)"
    assert SYM_RE.findall(ent) == ["FL"]
    red = redact_entity("Foot Locker Inc. reported Q3 results.", "FL", ent)
    assert "Foot Locker" not in red and "[ENTITY_OMEGA]" in red

def test_12_quant_bridge_export_matches_apex_quant_engine_schema():
    qb = build_quant_bridge(_base_ic(addv_20d_usd=28e6), EarningsReleaseDelta(form_type="8-K", file_date="2026-10-23"))
    assert qb["graduated"] is True and qb["pf_fcf_usd"] == 135_000_000.0 and qb["pf_net_debt_usd"] == 60_000_000.0
