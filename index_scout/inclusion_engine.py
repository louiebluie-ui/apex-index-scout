from index_scout.contracts import IndexCandidateFinancials, EarningsReleaseDelta, TOXIC_FILING_REGEX

def evaluate_index_inclusion(st: IndexCandidateFinancials, ed: EarningsReleaseDelta, raw_text: str, vacancy_active: bool = False, equity: float = 94596.0) -> dict | None:
    if TOXIC_FILING_REGEX.search(raw_text) or ed.is_toxic_or_restatement or ed.has_discontinued_ops_loss: return None
    if not (1.0e9 <= st.market_cap_usd <= 20.5e9 and st.iwf_float_pct >= 50.0 and st.falr_ratio >= 0.75 and st.live_price >= 10.0 and 4.0e6 <= st.addv_20d_usd < 20.0e6 and st.bars_count >= 60): return None
    if st.live_price < st.avwap_event or st.live_price > st.avwap_event * 1.08 or st.intraday_ofi < 0.15: return None
    q0_ni = ed.q0_gaap_ni_usd if ed.q0_gaap_ni_usd != 0.0 else (st.api_q0_gaap_ni_usd if st.is_api_synced_today else 0.0)
    ttm_ni = q0_ni + st.prior_3q_gaap_ni_usd
    owner_fcf = max(st.ocf_ttm_usd - st.capex_ttm_usd - st.sbc_ttm_usd, ed.q0_operating_cf_usd * 3.0)
    fcf_y = (owner_fcf / max(st.market_cap_usd, 1.0)) * 100.0
    net_debt = max(0.0, st.total_debt_usd - st.cash_usd)
    pf_lev = net_debt / max(owner_fcf, 1.0)
    is_flip = st.prior_4q_gaap_ni_usd <= 0.0
    is_vac = vacancy_active and st.prior_4q_gaap_ni_usd > 0.0 and (ttm_ni / st.market_cap_usd) >= 0.04
    is_ins = ed.insider_cluster_usd >= 250_000.0 and ed.insider_buyers_count >= 2
    is_del_washout = ed.is_index_deletion_washout and fcf_y >= 6.0 and pf_lev <= 2.50 and st.intraday_ofi >= 0.25
    if not ((q0_ni > 0 and ttm_ni > 0 and (is_flip or is_vac)) or is_ins or is_del_washout): return None
    if fcf_y < 4.5 or pf_lev > 3.00: return None
    tier = "SP_600_SMALLCAP" if st.market_cap_usd <= 6.8e9 else "SP_400_MIDCAP"
    track = f"{tier}_DELETION_WASHOUT" if is_del_washout else ("SECTION_16B_CLUSTER" if is_ins and not (is_flip or is_vac) else (f"{tier}_GAAP_FLIP" if is_flip else f"{tier}_VACANCY_LEADER"))
    idx_wt = 0.115 if tier == "SP_600_SMALLCAP" else 0.095
    passive_days = round((st.market_cap_usd * (st.iwf_float_pct / 100.0) * idx_wt) / max(st.addv_20d_usd, 1.0), 2)
    stop_dist = max(1.65 * st.atr_14, 0.025 * st.live_price)
    if stop_dist / st.live_price > 0.08: return None
    alloc_usd = min(0.085 * equity, (0.0045 * equity) / (stop_dist / st.live_price), 0.0020 * st.addv_20d_usd)
    limit_px = round(min(st.live_price, st.avwap_event * 1.035), 2)
    return {"symbol": st.symbol, "track": track, "index_tier": tier, "q0_gaap_ni_usd": round(q0_ni, 2), "ttm_gaap_ni_usd": round(ttm_ni, 2), "prior_4q_gaap_ni_usd": round(st.prior_4q_gaap_ni_usd, 2), "pf_fcf_yield_pct": round(fcf_y, 2), "pf_net_debt_y": round(pf_lev, 2), "falr_ratio": round(st.falr_ratio, 2), "passive_demand_days": passive_days, "live_px": round(st.live_price, 2), "limit_buy_px": limit_px, "wick_buy_px": round(st.live_price - 0.45 * st.atr_14, 2), "hard_stop_px": round(st.live_price - stop_dist, 2), "target_s1_px": round(st.live_price + 2.0 * stop_dist, 2), "alloc_usd": round(alloc_usd, 2), "shares": int(alloc_usd // limit_px)}

def has_index_inclusion_edge(st: IndexCandidateFinancials | None, ed: EarningsReleaseDelta, raw_text: str, vacancy_active: bool = False) -> bool:
    if not st or TOXIC_FILING_REGEX.search(raw_text) or ed.is_toxic_or_restatement: return False
    st_neutral = st.model_copy(update={"avwap_event": st.live_price, "intraday_ofi": 0.25, "addv_20d_usd": min(st.addv_20d_usd, 19.9e6), "atr_14": min(st.atr_14, 0.04 * st.live_price)})
    return evaluate_index_inclusion(st_neutral, ed, raw_text, vacancy_active=vacancy_active) is not None

def build_early_index_brief(st: IndexCandidateFinancials | None, ed: EarningsReleaseDelta, raw_text: str, vacancy_active: bool = False, sym: str = "") -> dict:
    if not st:
        return {"symbol": sym or "PENDING", "track": "INDEX_INCLUSION_WATCH", "form_type": ed.form_type, "file_date": ed.file_date, "q0_gaap_ni_usd": ed.q0_gaap_ni_usd, "status": "AWAITING_MARKET_DATA", "summary": ed.catalyst_summary}
    sim = evaluate_index_inclusion(st.model_copy(update={"avwap_event": st.live_price, "intraday_ofi": 0.25, "addv_20d_usd": min(st.addv_20d_usd, 19.9e6), "atr_14": min(st.atr_14, 0.04 * st.live_price)}), ed, raw_text, vacancy_active=vacancy_active) or {}
    min_ofi = 0.25 if ed.is_index_deletion_washout else 0.15
    reasons = []
    if st.live_price < st.avwap_event: reasons.append(f"BELOW_AVWAP ({st.live_price:.2f} < {st.avwap_event:.2f})")
    if st.live_price > st.avwap_event * 1.08: reasons.append(f"EXTENDED_ABOVE_AVWAP ({st.live_price:.2f} > {st.avwap_event * 1.08:.2f})")
    if st.intraday_ofi < min_ofi: reasons.append(f"WEAK_OFI ({st.intraday_ofi:.2f} < {min_ofi:.2f})")
    return {
        "symbol": st.symbol, "track": sim.get("track", "INDEX_INCLUSION_WATCH"), "index_tier": sim.get("index_tier", "SP_600_SMALLCAP"),
        "form_type": ed.form_type, "file_date": ed.file_date, "live_px": round(st.live_price, 2),
        "avwap_anchor_px": round(st.avwap_event, 2), "max_chase_px": round(st.avwap_event * 1.08, 2),
        "intraday_ofi": round(st.intraday_ofi, 2), "q0_gaap_ni_usd": sim.get("q0_gaap_ni_usd", ed.q0_gaap_ni_usd),
        "ttm_gaap_ni_usd": sim.get("ttm_gaap_ni_usd", 0.0), "pf_fcf_yield_pct": sim.get("pf_fcf_yield_pct", 0.0),
        "pf_net_debt_y": sim.get("pf_net_debt_y", 0.0), "passive_demand_days": sim.get("passive_demand_days", 0.0),
        "status": "READY_FOR_EXECUTION" if not reasons else " | ".join(reasons), "summary": ed.catalyst_summary
    }

def build_quant_bridge(st: IndexCandidateFinancials, ed: EarningsReleaseDelta) -> dict:
    owner_fcf = max(st.ocf_ttm_usd - st.capex_ttm_usd - st.sbc_ttm_usd, ed.q0_operating_cf_usd * 3.0)
    nd = max(0.0, st.total_debt_usd - st.cash_usd)
    return {"symbol": st.symbol, "file_date": ed.file_date, "form_type": ed.form_type, "graduated": st.addv_20d_usd >= 20_000_000 and st.live_price >= 12.0, "bars_count": st.bars_count, "addv_20d_usd": round(st.addv_20d_usd, 2), "avwap_event": round(st.avwap_event, 2), "pf_fcf_usd": round(owner_fcf, 2), "pf_net_debt_usd": round(nd, 2), "adj_mcap_usd": round(st.market_cap_usd, 2), "insider_cluster_usd": ed.insider_cluster_usd, "q0_gaap_ni_usd": ed.q0_gaap_ni_usd, "is_index_deletion_washout": ed.is_index_deletion_washout}
