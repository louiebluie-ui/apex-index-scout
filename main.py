import json, os, sys, httpx
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from dotenv import load_dotenv
from index_scout.contracts import EarningsReleaseDelta, IndexCandidateFinancials
from index_scout.harvester import fetch_earnings_and_vacancy_filings, fetch_filing_text, load_seen, save_seen, send_index_alert
from index_scout.ingestor import load_sp1500_exclusions, fetch_index_candidate_financials, fetch_insider_cluster, extract_earnings_delta
from index_scout.inclusion_engine import evaluate_index_inclusion, has_index_inclusion_edge, build_early_index_brief, build_quant_bridge

CAT_P = Path("store/active_index_candidates.json")

if __name__ == "__main__":
    load_dotenv(".env", override=True)
    fmp_k, gem_k = os.getenv("FMP_API_KEY", "").strip(), os.getenv("GEMINI_API_KEY", "").strip()
    if not (fmp_k and gem_k): sys.exit("[INDEX SCOUT ABORT] Missing API keys.")
    now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
    is_rth = (390 <= (now_pt.hour * 60 + now_pt.minute) <= 785)
    end = date.today(); start = end - timedelta(days=5); seen = load_seen()
    active: dict[str, dict] = json.loads(CAT_P.read_text()) if CAT_P.exists() else {}
    with httpx.Client(timeout=15.0) as c:
        sp1500 = load_sp1500_exclusions(c, fmp_k)
        hits = fetch_earnings_and_vacancy_filings(str(start), str(end), sp1500)
        new_hits = [h for h in hits if h["adsh"] and h["adsh"] not in seen]
        if not new_hits and not is_rth:
            print(f"[INDEX SCOUT FAST-EXIT] New=0 outside RTH | ActiveQueue={len(active)}")
            sys.exit(0)
        vacancy_active = any("2.01" in str(h.get("items")) and any(s in sp1500 for s in h.get("symbols", [])) for h in hits)
        st_cache: dict[str, IndexCandidateFinancials | None] = {}
        for h in new_hits:
            sym, fm, st, fmp_err = h["symbol"], h["form"], None, False
            for cand in h.get("symbols", [sym]):
                if cand in sp1500: continue
                if cand not in st_cache:
                    st_cache[cand] = fetch_index_candidate_financials(c, cand, h["file_date"], fmp_k, sp1500)
                    if c.headers.get("X-Fmp-Err"): fmp_err = True; st_cache.pop(cand, None); break
                if st_cache.get(cand): sym, st = cand, st_cache[cand]; break
            if fmp_err: continue
            if not st: seen.add(h["adsh"]); continue
            txt = fetch_filing_text(c, h.get("ciks") or h["cik"], h["adsh"], h.get("docs") or "")
            if not txt: continue
            ed = extract_earnings_delta(c, sym, fm, txt, gem_k, h["entity"], h["file_date"])
            if ed.form_type == "ERROR": continue
            ins_usd, ins_cnt = fetch_insider_cluster(c, sym, str(end - timedelta(days=30)), fmp_k)
            if ins_usd > ed.insider_cluster_usd:
                ed.insider_cluster_usd, ed.insider_buyers_count = ins_usd, max(ed.insider_buyers_count, ins_cnt)
            seen.add(h["adsh"])
            if has_index_inclusion_edge(st, ed, txt, vacancy_active):
                prev = active.get(sym, {})
                if not prev.get("alerted"):
                    active[sym] = {"ed": ed.model_dump(), "raw_snippet": txt[:2000], "early_alerted": prev.get("early_alerted", False), "alerted": False}
        passed, early_notified = [], []
        for sym, item in list(active.items()):
            ed = EarningsReleaseDelta(**item["ed"])
            if ed.file_date and (end - date.fromisoformat(ed.file_date)).days > 60:
                del active[sym]; continue
            st = st_cache.get(sym) or fetch_index_candidate_financials(c, sym, ed.file_date or str(start), fmp_k, sp1500)
            if not st: continue
            item["quant_bridge"] = build_quant_bridge(st, ed)
            if item.get("alerted"): continue
            res = evaluate_index_inclusion(st, ed, item.get("raw_snippet", ""), vacancy_active)
            if res:
                res["summary"] = ed.catalyst_summary; item["alerted"] = True; item["early_alerted"] = True; passed.append(res)
                send_index_alert(f"[INDEX SCOUT BUY] {sym} ({res['track']})", json.dumps(res, indent=2))
            elif not item.get("early_alerted"):
                brief = build_early_index_brief(st, ed, item.get("raw_snippet", ""), vacancy_active=vacancy_active, sym=sym)
                send_index_alert(f"[INDEX EARLY CATALYST] {sym} ({brief['track']})", json.dumps(brief, indent=2))
                item["early_alerted"] = True; early_notified.append(sym)
    save_seen(seen); CAT_P.write_text(json.dumps(active, indent=2))
    print(f"[INDEX SCOUT] New={len(new_hits)} | ActiveQueue={len(active)} | EarlyWatch={len(early_notified)} | Triggered={len(passed)}")
