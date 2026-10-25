import re
from pydantic import BaseModel

TOXIC_FILING_REGEX = re.compile(
    r"(at-the-market|convertible promissory|warrant inducement|equity line of credit|"
    r"registered direct offering|(?:raises|exists) substantial doubt.{0,80}going concern|"
    r"minimum bid price|non-reliance.{0,60}restatement|restatement of.{0,40}financial)", re.I | re.S
)

class IndexCandidateFinancials(BaseModel):
    symbol: str; market_cap_usd: float; iwf_float_pct: float = 85.0; falr_ratio: float = 1.0
    ocf_ttm_usd: float; capex_ttm_usd: float; sbc_ttm_usd: float; total_debt_usd: float; cash_usd: float
    prior_3q_gaap_ni_usd: float; prior_4q_gaap_ni_usd: float; api_q0_gaap_ni_usd: float = 0.0
    is_api_synced_today: bool = False; addv_20d_usd: float; bars_count: int; live_price: float
    avwap_event: float; atr_14: float; intraday_ofi: float

class EarningsReleaseDelta(BaseModel):
    form_type: str; file_date: str = ""; q0_gaap_ni_usd: float = 0.0; q0_operating_cf_usd: float = 0.0
    has_discontinued_ops_loss: bool = False; is_merger_closing_vacancy: bool = False
    is_toxic_or_restatement: bool = False; catalyst_summary: str = ""
