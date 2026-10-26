import html, json, os, re, smtplib, httpx
from email.message import EmailMessage
from pathlib import Path

SEC_HDR = {"User-Agent": "ApexIndexScout/1.0 (quant@research.local)"}
SYM_RE = re.compile(r"\((?!(?:DE|MD|NV|CYM|BVI)\)\s*\(CIK)([A-Z]{1,5})(?:[,\s][^)]*)?\)\s*(?:\(CIK|$)")
ANCHOR_RE = re.compile(r"(statements?\s+of\s+(?:operations|income|earnings)|gaap\s+net\s+income|net\s+income\s+\(loss\)|continuing\s+operations|diluted\s+(?:earnings|net\s+income)|operating\s+activities|item\s+2\.0[12])", re.I)
SEEN_P = Path("store/seen_filings.json")

def load_seen() -> set[str]:
    try: return set(json.loads(SEEN_P.read_text())) if SEEN_P.exists() else set()
    except Exception: return set()

def save_seen(seen: set[str]) -> None:
    SEEN_P.parent.mkdir(parents=True, exist_ok=True)
    SEEN_P.write_text(json.dumps(sorted(list(seen))[-3000:]))

def fetch_earnings_and_vacancy_filings(start_dt: str, end_dt: str, sp1500_ex: set[str] | None = None) -> list[dict]:
    by_adsh: dict[str, dict] = {}
    ex = sp1500_ex or set()
    qs = [("8-K,8-K/A", "%22Item+2.02%22+OR+%22Item+2.01%22"), ("10-Q,10-K", "%22Net+Income%22+AND+%22Operating+Activities%22")]
    with httpx.Client(timeout=15.0, headers=SEC_HDR) as c:
        for forms, q in qs:
            for pg in range(4):
                r = c.get(f"https://efts.sec.gov/LATEST/search-index?q={q}&forms={forms}&dateRange=custom&startdt={start_dt}&enddt={end_dt}&from={pg*100}")
                hits = r.json().get("hits", {}).get("hits", []) if r.status_code == 200 else []
                if not hits: break
                for h in hits:
                    s = h.get("_source", {}); raw_id = str(h.get("_id", "")); fm = s.get("form", "")
                    name = " | ".join(s.get("display_names") or ["UNKNOWN"])
                    syms = [x for x in SYM_RE.findall(name) if x not in ex or "2.01" in str(s.get("items"))]
                    adsh = s.get("adsh") or raw_id.split(":")[0]; doc = raw_id.split(":")[1] if ":" in raw_id else ""
                    ciks = [x for x in (s.get("ciks") or []) if x]
                    if not (adsh and doc and syms and ciks): continue
                    if adsh not in by_adsh:
                        by_adsh[adsh] = {"symbol": syms[0], "symbols": syms, "entity": name, "form": fm, "file_date": s.get("file_date", ""), "items": s.get("items", []), "adsh": adsh, "cik": ciks[0], "ciks": ciks, "docs": []}
                    if doc not in by_adsh[adsh]["docs"] and len(by_adsh[adsh]["docs"]) < 3:
                        by_adsh[adsh]["docs"].append(doc)
    return list(by_adsh.values())

def strip_sec_html(raw_html: str, max_chars: int = 12000) -> str:
    txt = re.sub(r"<(script|style|ix:header)[^>]*>.*?</\1>", " ", raw_html, flags=re.I | re.S)
    txt = re.sub(r"</t[dh]>", " | ", txt, flags=re.I)
    txt = re.sub(r"</(tr|p|div|li|h[1-6])>", "\n", txt, flags=re.I)
    txt = html.unescape(re.sub(r"<[^>]+>", " ", txt))
    txt = re.sub(r"[ \t\u00a0\u200b]+", " ", txt)
    txt = re.sub(r"\n\s*\n+", "\n", txt).strip()
    if len(txt) <= max_chars: return txt
    chunks, used = [txt[:1500]], [0]
    ms = sorted(ANCHOR_RE.finditer(txt), key=lambda m: (0 if re.search(r"operations|income|diluted", m.group(0), re.I) else 1, -(txt[m.start():m.start()+1400].count("$")*3 + txt[m.start():m.start()+1400].count("|")), m.start()))
    for m in ms:
        if any(abs(m.start() - u) < 900 for u in used): continue
        st, end = max(1500, m.start() - 200), min(len(txt), m.end() + 1350); win = txt[st:end]
        if st < end and re.search(r"(\$|%|million|thousand)", win, re.I) and sum(len(x) for x in chunks) < max_chars:
            chunks.append(win); used.append(m.start())
    return "\n...\n".join(chunks)[:max_chars]

def fetch_filing_text(client: httpx.Client, cik: str | list[str], adsh: str, docs: list[str] | str) -> str:
    doc_list = list(docs) if isinstance(docs, list) else ([docs] if docs else [])
    ciks = [cik] if isinstance(cik, str) and cik else (list(cik) if isinstance(cik, list) else [])
    if not (ciks and adsh and doc_list): return ""
    base = f"https://www.sec.gov/Archives/edgar/data/{int(ciks[0])}/{adsh.replace('-','')}"
    for ck in ciks:
        b_try = f"https://www.sec.gov/Archives/edgar/data/{int(ck)}/{adsh.replace('-','')}"
        try:
            idx = client.get(f"{b_try}/index.json", headers=SEC_HDR, timeout=10.0)
            if idx.status_code == 200:
                base = b_try; ex99 = []
                for it in idx.json().get("directory", {}).get("item", []):
                    nm = str(it.get("name", ""))
                    if nm.lower().endswith((".htm", ".html", ".txt")) and nm not in doc_list and re.search(r"(ex99|ex-99|press|earn|release)", nm, re.I):
                        ex99.append(nm)
                doc_list = ex99 + doc_list; break
        except Exception: pass
    parts = []
    for i, d in enumerate(doc_list[:3]):
        try:
            r = client.get(f"{base}/{d}", headers=SEC_HDR, timeout=15.0)
            if r.status_code == 200: parts.append(strip_sec_html(r.text, 10000 if i == 0 else 6000))
        except Exception: pass
    return "\n--- EXHIBIT/DOC ---\n".join(parts)[:16000]

def send_index_alert(subject: str, body: str) -> bool:
    u, pw, to = os.getenv("ALERT_EMAIL", ""), os.getenv("ALERT_EMAIL_PW", ""), (os.getenv("ALERT_RECIPIENT") or os.getenv("ALERT_EMAIL", ""))
    if not (u and pw and to): return False
    try:
        msg = EmailMessage(); msg["Subject"] = subject; msg["From"] = u; msg["To"] = to; msg.set_content(body)
        with smtplib.SMTP_SSL((os.getenv("SMTP_HOST") or "smtp.gmail.com").strip(), 465, timeout=15.0) as s:
            s.login(u, pw); s.send_message(msg)
        return True
    except Exception: return False
