from datetime import date
import json, re, httpx
from pathlib import Path
from index_scout.contracts import IndexCandidateFinancials, EarningsReleaseDelta

FMP_BASE = "https://financialmodelingprep.com/stable"
SP1500_P = Path("store/sp1500_exclusions.json")

def load_sp1500_exclusions(client: httpx.Client, fmp_key: str) -> set[str]:
    cached: set[str] = set()
    try:
        if SP1500_P.exists(): cached = set(json.loads(SP1500_P.read_text()))
    except Exception: pass
    if len(cached) >= 400 or not fmp_key: return cached
    try:
        r = client.get(f"{FMP_BASE}/sp500-constituent?apikey={fmp_key}")
        if r.status_code == 200 and isinstance(r.json(), list):
            for x in r.json():
                if x.get("symbol"): cached.add(str(x["symbol"]).upper())
        if cached: SP1500_P.write_text(json.dumps(sorted(list(cached))))
    except Exception: pass
    return cached

def fetch_index_candidate_financials(client: httpx.Client, sym: str, file_dt: str, fmp_key: str, sp1500_ex: set[str] | None = None) -> IndexCandidateFinancials | None:
    if not fmp_key or not sym or (sp1500_ex and sym in sp1500_ex): return None
    client.headers.pop("X-Fmp-Err", None)
    try: pr = client.get(f"{FMP_BASE}/profile?symbol={sym}&apikey={fmp_key}")
    except Exception: client.headers["X-Fmp-Err"] = "1"; return None
    if pr.status_code in (429, 500, 502, 503, 504): client.headers["X-Fmp-Err"] = "1"; return None
    pj = pr.json() if pr.status_code == 200 else None
    if isinstance(pj, dict) and ("Error" in str(pj) or "Limit" in str(pj)): client.headers["X-Fmp-Err"] = "1"; return None
    if not isinstance(pj, list) or not pj: return None
    p = pj[0]; mcap = float(p.get("marketCap") or p.get("mktCap") or 0.0); px = float(p.get("price") or 0.0)
    sec, ind, nm = str(p.get("sector") or ""), str(p.get("industry") or "").upper(), str(p.get("companyName") or "").upper()
    if px < 10.0 or not (1.0e9 <= mcap <= 20.5e9) or p.get("isEtf") or p.get("isFund") or p.get("isAdr"): return None
    if p.get("country") not in (None, "US") or sec == "Real Estate" or "REIT" in ind or " LP" in nm: return None
    iwf = 85.0
    try:
        fr = client.get(f"{FMP_BASE}/shares-float?symbol={sym}&apikey={fmp_key}")
        fj = fr.json() if fr.status_code == 200 else []
        if isinstance(fj, list) and fj and fj[0].get("freeFloat") is not None: iwf = float(fj[0]["freeFloat"])
    except Exception: pass
    if iwf < 50.0: return None
    return _hydrate_bars_and_statements(client, sym, file_dt, mcap, px, iwf, fmp_key)

def _hydrate_bars_and_statements(c: httpx.Client, sym: str, file_dt: str, mcap: float, px: float, iwf: float, key: str) -> IndexCandidateFinancials | None:
    br = c.get(f"{FMP_BASE}/historical-price-eod/full?symbol={sym}&apikey={key}")
    bj = br.json() if br.status_code == 200 else None
    if not isinstance(bj, list) or len(bj) < 60: return None
    raw_b = sorted(bj, key=lambda x: str(x.get("date", "")), reverse=True)[:95]; contig = [raw_b[0]]
    for i in range(1, len(raw_b)):
        try:
            if abs((date.fromisoformat(str(raw_b[i-1]["date"])[:10]) - date.fromisoformat(str(raw_b[i]["date"])[:10])).days) > 20: break
        except Exception: pass
        contig.append(raw_b[i])
    bars = list(reversed(contig)); n = len(bars)
    if n < 60: return None
    rec = bars[-20:]
    addv = sum(float(b.get("close", 0)) * float(b.get("volume", 0)) for b in rec) / 20.0
    falr = round((addv * 252.0) / max(mcap * (iwf / 100.0), 1.0), 3)
    if addv < 4.0e6 or falr < 0.75: return None
    b0 = bars[-1]; live_px = float(b0.get("close") or px)
    hi, lo = float(b0.get("high") or live_px), float(b0.get("low") or live_px)
    ofi = ((live_px - lo) - (hi - live_px)) / max(hi - lo, 0.01)
    trs = [max(float(bars[i].get("high", 0)) - float(bars[i].get("low", 0)), abs(float(bars[i].get("high", 0)) - float(bars[i-1].get("close", 0))), abs(float(bars[i].get("low", 0)) - float(bars[i-1].get("close", 0))), 0.01) for i in range(n - 14, n)]
    atr = sum(trs) / 14.0
    ev = [b for b in bars if str(b.get("date", "")) >= file_dt] or bars[-1:]
    ev_v = sum(float(b.get("volume") or 0) for b in ev)
    avw = (sum(((float(b.get("high",0))+float(b.get("low",0))+float(b.get("close",0)))/3.0)*float(b.get("volume",0)) for b in ev) / ev_v) if ev_v > 0 else live_px
    def _j(ep: str) -> list:
        try: r = c.get(f"{FMP_BASE}/{ep}&apikey={key}"); return r.json() if r.status_code == 200 and isinstance(r.json(), list) else []
        except Exception: return []
    inc, cf, bs = _j(f"income-statement?symbol={sym}&period=quarter&limit=5"), _j(f"cash-flow-statement?symbol={sym}&period=quarter&limit=4"), _j(f"balance-sheet-statement?symbol={sym}&period=quarter&limit=1")
    if len(inc) < 4 or not cf or not bs: return None
    nis = [float(q.get("netIncome") or 0.0) for q in inc[:5]]
    f_dt0 = str(inc[0].get("filingDate") or inc[0].get("date") or "")[:10]
    synced = bool(file_dt and len(f_dt0) == 10 and f_dt0 >= file_dt and len(nis) >= 5)
    api_q0, p3, p4 = (nis[0], sum(nis[1:4]), sum(nis[1:5])) if synced else (0.0, sum(nis[:3]), sum(nis[:4]))
    scale = 4.0 / len(cf[:4])
    ocf = sum(float(q.get("operatingCashFlow") or 0) for q in cf[:4]) * scale
    capex = sum(abs(float(q.get("capitalExpenditure") or 0)) for q in cf[:4]) * scale
    sbc = sum(max(0.0, float(q.get("stockBasedCompensation") or 0)) for q in cf[:4]) * scale
    debt, cash = float(bs[0].get("totalDebt") or 0.0), float(bs[0].get("cashAndShortTermInvestments") or bs[0].get("cashAndCashEquivalents") or 0.0)
    return IndexCandidateFinancials(symbol=sym, market_cap_usd=mcap, iwf_float_pct=iwf, falr_ratio=falr, ocf_ttm_usd=ocf, capex_ttm_usd=capex, sbc_ttm_usd=sbc, total_debt_usd=debt, cash_usd=cash, prior_3q_gaap_ni_usd=p3, prior_4q_gaap_ni_usd=p4, api_q0_gaap_ni_usd=api_q0, is_api_synced_today=synced, addv_20d_usd=addv, bars_count=n, live_price=live_px, avwap_event=avw, atr_14=atr, intraday_ofi=ofi)

def redact_entity(text: str, sym: str, entity_name: str = "") -> str:
    out = re.sub(rf"\b{re.escape(sym)}\b", "[ENTITY_OMEGA]", text, flags=re.I) if sym else text
    for part in entity_name.split("|"):
        core = re.sub(r"(\s*\([^)]*\)|\s*/[A-Za-z]{2,4}/)", "", part)
        core = re.sub(r"[,\.]?\s*\b(INC|CORP|CORPORATION|LLC|LTD|PLC|LP|L\.P\.|HOLDINGS|BRANDS|GROUP|CO|COMPANY|TRUST)\b[\.,]*", "", core, flags=re.I).strip()
        if len(core) > 2: out = re.sub(re.escape(core), "[ENTITY_OMEGA]", out, flags=re.I)
    return out

def _norm_signed_usd(val: float) -> float:
    return val * 1e6 if 0.0 < abs(val) < 5000.0 else val

def extract_earnings_delta(c: httpx.Client, sym: str, form: str, raw_text: str, gem_key: str, entity_name: str = "", file_dt: str = "") -> EarningsReleaseDelta:
    if not gem_key or not raw_text: return EarningsReleaseDelta(form_type=form, file_date=file_dt)
    blind_txt = redact_entity(raw_text, sym, entity_name)
    prompt = (
        f"Audit this blinded SEC {form} filing for [ENTITY_OMEGA]. Convert dollar totals to full raw USD ($18.5M = 18500000.0, -$4.2M = -4200000.0). "
        "CRITICAL RULES: (1) Extract q0_gaap_ni_usd: most recent single quarter (Three Months Ended) GAAP Net Income attributable to common stockholders from continuing operations (NEVER use Non-GAAP/Adjusted Net Income or Adjusted EBITDA, and NEVER use 6-month/9-month/12-month YTD totals). "
        "(2) Extract q0_operating_cf_usd: single-quarter GAAP Operating Cash Flow if disclosed. "
        "(3) Set has_discontinued_ops_loss=true if continuing operations was only positive due to a one-time tax benefit or discontinued ops. "
        "(4) Set is_merger_closing_vacancy=true if Item 2.01 announces completion of an acquisition/merger of a public company. "
        "Return strict JSON: q0_gaap_ni_usd, q0_operating_cf_usd, has_discontinued_ops_loss (bool), is_merger_closing_vacancy (bool), is_toxic_or_restatement (bool), catalyst_summary (1-sentence str).\n\nTEXT:\n"
        + blind_txt[:14500]
    )
    u = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gem_key}"
    pld = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"}}
    try:
        r = c.post(u, json=pld, timeout=20.0)
        if r.status_code != 200: raise RuntimeError(f"Gemini HTTP {r.status_code}")
        raw_s = re.sub(r"^```(?:json)?|```$", "", r.json()["candidates"][0]["content"]["parts"][0]["text"].strip(), flags=re.I).strip()
        raw_d = json.loads(raw_s)
        if isinstance(raw_d, list): raw_d = raw_d[0] if raw_d else {}
        d = {k: v for k, v in raw_d.items() if v is not None}
        for k in ("q0_gaap_ni_usd", "q0_operating_cf_usd"):
            if k in d:
                if isinstance(d[k], str): d[k] = re.sub(r"[,$\s]", "", d[k].replace("(", "-").replace(")", "")) or 0.0
                d[k] = _norm_signed_usd(float(d[k] or 0.0))
        return EarningsReleaseDelta(form_type=form, file_date=file_dt, **d)
    except Exception:
        return EarningsReleaseDelta(form_type="ERROR", file_date=file_dt)
