"""共用工具：HTTP、數字解析、日期處理、檔案讀寫。"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

TPE = timezone(timedelta(hours=8))

ROOT = Path(__file__).resolve().parent.parent
DAILY_DIR = ROOT / "data" / "daily"
STATUS_PATH = ROOT / "data" / "status.json"
OUT_DIR = ROOT / "docs" / "chip" / "data"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*"})
    return s


def get_json(s: requests.Session, url: str, params: dict, tries: int = 3):
    """GET 並回傳 JSON；失敗時重試，全部失敗則拋出。"""
    last = None
    for i in range(tries):
        try:
            r = s.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"GET failed: {url} :: {last}")


def post_bytes(s: requests.Session, url: str, data: dict, tries: int = 3) -> bytes:
    last = None
    for i in range(tries):
        try:
            r = s.post(url, data=data, timeout=30)
            r.raise_for_status()
            return r.content
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"POST failed: {url} :: {last}")


_NUM = re.compile(r"-?[\d.]+")


def num(v, default=None):
    """把 '1,234,567'、'--'、'' 之類的字串轉成數字。"""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return v
    t = str(v).replace(",", "").replace("+", "").strip()
    if t in ("", "-", "--", "---", "N/A", "無"):
        return default
    m = _NUM.search(t)
    if not m:
        return default
    try:
        f = float(m.group())
    except ValueError:
        return default
    if t.startswith("-") and f > 0:
        f = -f
    return int(f) if f.is_integer() else f


def now_tpe() -> datetime:
    return datetime.now(TPE)


def today_tpe() -> date:
    return now_tpe().date()


def iso(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def candidate_dates(back: int = 10):
    """由今天往回列出可能的交易日（排除週末），最近的排前面。"""
    out, d = [], today_tpe()
    while len(out) < back:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def save_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )


def daily_path(d: str) -> Path:
    return DAILY_DIR / f"{d}.json"


def existing_days() -> list[str]:
    """已存檔的交易日，由舊到新。"""
    if not DAILY_DIR.exists():
        return []
    return sorted(p.stem for p in DAILY_DIR.glob("*.json"))
