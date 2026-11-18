"""探測公開資料端點的實際回應格式（在 GitHub Actions 上執行），把結構與前幾筆存到 data/probe/。"""
import json, time, urllib.parse, urllib.request, ssl
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "probe"
OUT.mkdir(parents=True, exist_ok=True)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE  # TWSE 憑證鏈在部分環境驗證失敗


def get(url, data=None):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode() if data else None, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40, context=CTX) as r:
        return r.status, r.read()


def summarize(raw):
    try:
        j = json.loads(raw)
    except Exception:  # noqa: BLE001
        for enc in ("utf-8", "cp950"):
            try:
                t = raw.decode(enc); break
            except Exception:  # noqa: BLE001
                t = raw.decode("latin1")
        return {"kind": "text", "len": len(raw), "head": t[:1500], "tail": t[-300:]}
    def cut(x, depth=0):
        if isinstance(x, list):
            return {"_len": len(x), "head": [cut(v, depth + 1) for v in x[:4]], "last": cut(x[-1], depth + 1) if x else None}
        if isinstance(x, dict):
            return {k: cut(v, depth + 1) for k, v in x.items()}
        return x
    return {"kind": "json", "len": len(raw), "body": cut(j)}


PROBES = {}
RAW = {
    "raw_twse_mi_index_all_20261117": "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&type=ALLBUT0999&date=20261117",
    "raw_twse_mi_index_all_20150824": "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&type=ALLBUT0999&date=20150824",
    "raw_tpex_otc_20261117": "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date=2026/11/17&type=EW&response=json",
    "raw_tpex_otc_20150824": "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date=2015/08/24&type=EW&response=json",
    "raw_tpex_highlight_20150824": "https://www.tpex.org.tw/www/zh-tw/afterTrading/highlight?date=2015/08/24&response=json",
    "raw_tpex_highlight_20261117": "https://www.tpex.org.tw/www/zh-tw/afterTrading/highlight?date=2026/11/17&response=json",
    "raw_tpex_inx_201401": "https://www.tpex.org.tw/www/zh-tw/indexInfo/inx?date=2014/01/01&response=json",
}
for name, url in RAW.items():
    try:
        st, raw = get(url)
        (OUT / f"{name}.json").write_bytes(raw)
        print(name, st, len(raw))
    except Exception as e:  # noqa: BLE001
        print(name, "ERR", repr(e)[:300])
    time.sleep(4)
for name, url in PROBES.items():
    try:
        st, raw = get(url)
        res = {"url": url, "status": st, **summarize(raw)}
    except Exception as e:  # noqa: BLE001
        res = {"url": url, "error": repr(e)[:400]}
    (OUT / f"{name}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(name, res.get("status"), res.get("kind"), res.get("len"), res.get("error", ""))
    time.sleep(4)

for name, (s, e) in {}.items():
    try:
        st, raw = get("https://www.taifex.com.tw/cht/3/pcRatioDown", {"queryStartDate": s, "queryEndDate": e})
        res = {"status": st, "kind": "csv", "len": len(raw), "head": raw.decode("cp950", errors="replace")[:800]}
    except Exception as ex:  # noqa: BLE001
        res = {"error": repr(ex)[:400]}
    (OUT / f"{name}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(name, res.get("status"), res.get("len"), res.get("error", ""))
    time.sleep(3)
