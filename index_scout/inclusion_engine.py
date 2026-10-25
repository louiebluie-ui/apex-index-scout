from index_scout.contracts import IndexCandidateFinancials, EarningsReleaseDelta, TOXIC_FILING_REGEX

def evaluate_index_inclusion(st: IndexCandidateFinancials, ed: EarningsReleaseDelta, raw_text: str, vacancy_active: bool = False, equity: float = 94596.0) -> dict | None:
    if TOXIC_FILING_REGEX.search(raw_text) or ed.is_toxic_or_restatement or ed.has_discontinued_ops_loss: return None
    if not (1.0e9 <= st.market_cap_usd <= 20.5e9 and st.iwf_float_pct >= 50.0 and st.falr_ratio >= 0.75 and st.live_price >= 10.0 and st.addv_20d_usd >= 4.0e6 and st.bars_count >= 60): return None
    if st.live_price < st.avwap_event or st.intraday_ofi < 0.15: return None
    q0_ni = ed.q0_gaap_ni_usd if ed.q0_gaap_ni_usd != 0.0 else (st.api_q0_gaap_ni_usd if st.is_api_synced_today else 0.0)
    ttm_ni = q0_ni + st.prior_3q_gaap_ni_usd
    is_flip = st.prior_4q_gaap_ni_usd <= 0.0
    is_vac = vacancy_active and st.prior_4q_gaap_ni_usd > 0.0 and (ttm_ni / st.market_cap_usd) >= 0.04
    is_ins = ed.insider_cluster_usd >= 250_000.0 and ed.insider_buyers_count >= 2
    if not ((q0_ni > 0 and ttm_ni > 0 and (is_flip or is_vac)) or is_ins): return None
    owner_fcf = max(st.ocf_ttm_usd - st.capex_ttm_usd - st.sbc_ttm_usd, ed.q0_operating_cf_usd * 3.0)
    fcf_y = (owner_fcf / max(st.market_cap_usd, 1.0)) * 100.0
    net_debt = max(0.0, st.total_debt_usd - st.cash_usd)
    pf_lev = net_debt / max(owner_fcf, 1.0)
    if fcf_y < 4.5 or pf_lev > 3.00: return None
    tier = "SP_600_SMALLCAP" if st.market_cap_usd <= 6.8e9 else "SP_400_MIDCAP"
    track = "SECTION_16B_CLUSTER" if is_ins and not (is_flip or is_vac) else (f"{tier}_GAAP_FLIP" if is_flip else f"{tier}_VACANCY_LEADER")
    idx_wt = 0.115 if tier == "SP_600_SMALLCAP" else 0.095
    passive_days = round((st.market_cap_usd * (st.iwf_float_pct / 100.0) * idx_wt) / max(st.addv_20d_usd, 1.0), 2)
    stop_dist = max(1.65 * st.atr_14, 0.025 * st.live_price)
    alloc_usd = min(0.085 * equity, (0.0045 * equity) / (stop_dist / st.live_price), 0.0020 * st.addv_20d_usd)
    return {"symbol": st.symbol, "track": track, "index_tier": tier, "q0_gaap_ni_usd": round(q0_ni, 2), "ttm_gaap_ni_usd": round(ttm_ni, 2), "prior_4q_gaap_ni_usd": round(st.prior_4q_gaap_ni_usd, 2), "pf_fcf_yield_pct": round(fcf_y, 2), "pf_net_debt_y": round(pf_lev, 2), "falr_ratio": round(st.falr_ratio, 2), "passive_demand_days": passive_days, "limit_buy_px": round(st.live_price, 2), "wick_buy_px": round(st.live_price - 0.45 * st.atr_14, 2), "hard_stop_px": round(st.live_price - stop_dist, 2), "target_s1_px": round(st.live_price + 2.0 * stop_dist, 2), "alloc_usd": round(alloc_usd, 2), "shares": int(alloc_usd // st.live_price)}

def has_index_inclusion_edge(st: IndexCandidateFinancials | None, ed: EarningsReleaseDelta, raw_text: str, vacancy_active: bool = False) -> bool:
    if not st or TOXIC_FILING_REGEX.search(raw_text) or ed.is_toxic_or_restatement: return False
    st_neutral = st.model_copy(update={"avwap_event": st.live_price, "intraday_ofi": 0.25})
    return evaluate_index_inclusion(st_neutral, ed, raw_text, vacancy_active=vacancy_active) is not None

def build_quant_bridge(st: IndexCandidateFinancials, ed: EarningsReleaseDelta) -> dict:
    owner_fcf = max(st.ocf_ttm_usd - st.capex_ttm_usd - st.sbc_ttm_usd, ed.q0_operating_cf_usd * 3.0)
    nd = max(0.0, st.total_debt_usd - st.cash_usd)
    return {"symbol": st.symbol, "file_date": ed.file_date, "form_type": ed.form_type, "graduated": st.addv_20d_usd >= 20_000_000 and st.live_price >= 12.0, "bars_count": st.bars_count, "addv_20d_usd": round(st.addv_20d_usd, 2), "avwap_event": round(st.avwap_event, 2), "pf_fcf_usd": round(owner_fcf, 2), "pf_net_debt_usd": round(nd, 2), "adj_mcap_usd": round(st.market_cap_usd, 2)}
