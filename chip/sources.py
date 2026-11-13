"""五個資料來源的抓取邏輯。

設計原則：欄位一律用「欄位名稱」對應，不用固定索引。交易所偶爾會插入或調動欄位，
名稱比位置穩定得多；名稱找不到時直接拋錯，讓上層記錄為失敗並保留上次成功的資料，
而不是安靜地讀到錯的數字。
"""
from __future__ import annotations

import csv
import io
from datetime import date

from common import get_json, num, post_bytes

# ---- 單位換算 --------------------------------------------------------------
# 交易所原始欄位的單位，換算成「元」。首次上線請照 README 的核對步驟確認一次。
TWSE_MARGIN_VALUE_UNIT = 1_000  # 融資金額(仟元)
TPEX_MARGIN_VALUE_UNIT = 1_000  # 融資金額(仟元)


def _cols(fields: list[str]) -> list[str]:
    return [str(f).replace(" ", "").replace("\u3000", "") for f in fields]


def _find(fields: list[str], *must, exclude=()) -> int:
    """找出同時包含所有關鍵字、且不含排除字的欄位索引。"""
    for i, f in enumerate(_cols(fields)):
        if all(k in f for k in must) and not any(x in f for x in exclude):
            return i
    raise KeyError(f"找不到欄位 {must!r} (exclude={exclude!r})；現有欄位：{fields}")


def _find_any(fields: list[str], *candidates):
    """candidates 是多組關鍵字 tuple，依序嘗試，回傳第一組找得到的。"""
    last = None
    for c in candidates:
        try:
            return _find(fields, *c)
        except KeyError as e:  # noqa: PERF203
            last = e
    raise last


def _pick_table(payload: dict, *must_fields):
    """從 tables[] 裡挑出含有指定欄位的那張表。"""
    for t in payload.get("tables") or []:
        fields = t.get("fields") or []
        joined = "".join(_cols(fields))
        if all(k in joined for k in must_fields) and t.get("data"):
            return t
    raise KeyError(f"找不到含有 {must_fields!r} 的表格")


def _label_rows(rows, label_idx=0):
    return {str(r[label_idx]).replace(" ", "").strip(): r for r in rows}


def _sum_by_label(table_rows, wanted, value_idx):
    """把符合任一關鍵字的列加總起來。"""
    total, hit = 0, False
    for label, row in table_rows.items():
        if any(w in label for w in wanted):
            v = num(row[value_idx], 0)
            total += v or 0
            hit = True
    if not hit:
        raise KeyError(f"三大法人表格找不到 {wanted!r}，現有：{list(table_rows)}")
    return total


def _cat_amount(rows, value_idx, exact_labels, part_specs):
    """取一個投資人分類的買賣超金額。

    官方 API 的分類列名是「外資及陸資」「自營商」這種不帶「合計」字樣的名稱
    （網頁上顯示的「XX合計」是網頁自己加的文字，不是 API 原始標籤），
    且跟子項「外資及陸資(不含外資自營商)」用包含關鍵字比對容易互相重疊誤判，
    所以優先用「完全比對」列名——不會有任何重疊問題。
    exact_labels：可能的完整列名（依序嘗試，第一個對到的就用）。
    只有完全比對都找不到時，才退回用關鍵字加總子項（並排除掉會誤判的字串）。
    """
    for label, row in rows.items():
        if label in exact_labels:
            return num(row[value_idx], 0) or 0
    total, hit = 0, False
    for must, exclude in part_specs:
        for label, row in rows.items():
            if all(k in label for k in must) and not any(x in label for x in exclude):
                total += num(row[value_idx], 0) or 0
                hit = True
                break
    if not hit:
        raise KeyError(f"找不到 {exact_labels!r} 或其子項，現有列：{list(rows)}")
    return total


# ---------------------------------------------------------------- 1. 上市法人
def twse_institutional(s, d: date) -> dict:
    """證交所三大法人買賣金額統計表 (BFI82U)。單位：元。"""
    j = get_json(
        s,
        "https://www.twse.com.tw/rwd/zh/fund/BFI82U",
        {"dayDate": d.strftime("%Y%m%d"), "type": "day", "response": "json"},
    )
    if j.get("stat") != "OK" or not j.get("data"):
        raise ValueError("BFI82U 無資料（可能非交易日或尚未產製）")

    fields = j.get("fields") or ["單位名稱", "買進金額", "賣出金額", "買賣超"]
    net_i = _find_any(fields, ("買賣超",), ("買賣差額",))
    rows = _label_rows(j["data"])

    # 外資 = 外資及陸資(不含外資自營商) + 外資自營商；官方若有現成合計列則直接用它，
    # 避免跟子項重複加總。自營商同理。
    foreign = _cat_amount(
        rows, net_i, ("外資及陸資", "外資及陸資合計"),
        [
            (("外資及陸資", "不含"), ()),
            (("外資自營商",), ("不含",)),
        ],
    )
    trust = _cat_amount(rows, net_i, ("投信",), [(("投信",), ())])
    dealer = _cat_amount(
        rows, net_i, ("自營商", "自營商合計"),
        [
            (("自營商", "自行買賣"), ()),
            (("自營商", "避險"), ()),
        ],
    )
    return {"foreign": foreign, "trust": trust, "dealer": dealer,
            "total": foreign + trust + dealer}


# ---------------------------------------------------------------- 2. 上櫃法人
def tpex_institutional(s, d: date) -> dict:
    """櫃買中心三大法人買賣超彙總。單位：元。"""
    j = get_json(
        s,
        "https://www.tpex.org.tw/www/zh-tw/insti/summary",
        {"type": "Daily", "date": d.strftime("%Y/%m/%d"), "response": "json"},
    )
    t = _pick_table(j, "買賣超")
    net_i = _find_any(t["fields"], ("買賣超",), ("買賣差額",))
    rows = _label_rows(t["data"])

    foreign = _cat_amount(
        rows, net_i, ("外資及陸資", "外資及陸資合計"),
        [
            (("外資及陸資", "不含"), ()),
            (("外資自營商",), ("不含",)),
        ],
    )
    trust = _cat_amount(rows, net_i, ("投信",), [(("投信",), ())])
    dealer = _cat_amount(
        rows, net_i, ("自營商", "自營商合計"),
        [
            (("自營商", "自行買賣"), ()),
            (("自營商", "避險"), ()),
        ],
    )
    return {"foreign": foreign, "trust": trust, "dealer": dealer,
            "total": foreign + trust + dealer}


# ---------------------------------------------------------------- 3. 上市融資
def twse_margin(s, d: date) -> dict:
    """證交所融資融券彙總 (MI_MARGN, selectType=MS)。回傳元。

    注意：這張表的欄位名稱是通用的「項目/買進/賣出/前日餘額/今日餘額」，
    「融資金額」是某一列的內容（項目欄的值），不是欄位名稱，所以要找列而不是找欄。
    """
    j = get_json(
        s,
        "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN",
        {"date": d.strftime("%Y%m%d"), "selectType": "MS", "response": "json"},
    )
    if j.get("stat") != "OK":
        raise ValueError("MI_MARGN 無資料（可能非交易日或尚未產製）")

    for t in j.get("tables") or []:
        fields = t.get("fields") or []
        for row in t.get("data") or []:
            if not row:
                continue
            label = str(row[0]).replace(" ", "").strip()
            if "融資金額" in label:
                bal_i = _find(fields, "今日餘額")
                prev_i = _find(fields, "前日餘額")
                bal = (num(row[bal_i], 0) or 0) * TWSE_MARGIN_VALUE_UNIT
                prev = (num(row[prev_i], 0) or 0) * TWSE_MARGIN_VALUE_UNIT
                return {"balance": bal, "prev": prev, "change": bal - prev}
    raise KeyError("MI_MARGN 找不到融資金額列")


# ---------------------------------------------------------------- 4. 上櫃融資
def tpex_margin(s, d: date) -> dict:
    """櫃買中心融資融券餘額。回傳元。"""
    j = get_json(
        s,
        "https://www.tpex.org.tw/www/zh-tw/margin/balance",
        {"date": d.strftime("%Y/%m/%d"), "response": "json"},
    )
    t = j["tables"][0]
    if not t.get("summary"):
        raise ValueError("TPEx margin 無合計資料（可能非交易日）")

    # summary 各列首欄是項目名稱，例如「融資金額(仟元)」
    bal = prev = None
    for row in t["summary"]:
        label = "".join(str(c) for c in row[:2]).replace(" ", "")
        if "融資" in label and ("金額" in label or "仟元" in label):
            vals = [num(c) for c in row if num(c) is not None]
            if len(vals) >= 5:
                prev, bal = vals[0], vals[4]
            break
    if bal is None:
        raise KeyError(f"TPEx 融資金額列找不到，summary={t['summary']}")

    bal *= TPEX_MARGIN_VALUE_UNIT
    prev *= TPEX_MARGIN_VALUE_UNIT
    return {"balance": bal, "prev": prev, "change": bal - prev}


# ---------------------------------------------------------------- 5. 台指期
def taifex_tx_foreign(s, d: date) -> dict:
    """期交所三大法人台股期貨(TXF)未平倉。回傳外資多空淨額口數與契約金額(元)。"""
    q = d.strftime("%Y/%m/%d")
    raw = post_bytes(
        s,
        "https://www.taifex.com.tw/cht/3/futContractsDateDown",
        {"queryStartDate": q, "queryEndDate": q, "commodityId": "TXF"},
    )
    text = raw.decode("big5", errors="replace")
    if "查無資料" in text or "日期時間錯誤" in text:
        raise ValueError("TAIFEX 查無資料（可能非交易日或尚未產製）")

    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if len(r) >= 15]
    if len(rows) < 2:
        raise ValueError("TAIFEX 回傳格式異常")

    header, body = rows[0], rows[1:]
    hj = "".join(header)
    if "未平倉" not in hj:
        raise KeyError(f"TAIFEX 表頭異常：{header}")

    for r in body:
        who = str(r[2]).replace(" ", "")
        if "外資" in who:
            return {
                "net_oi_lots": num(r[13], 0) or 0,
                "net_oi_value": (num(r[14], 0) or 0) * 1_000,  # 契約金額(千元)
            }
    raise KeyError("TAIFEX 找不到外資列")


# ------------------------------------------------- 6. 個股買賣超金額（排行用）
def _twse_avg_price(s, d: date) -> dict[str, float]:
    j = get_json(
        s,
        "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
        {"date": d.strftime("%Y%m%d"), "type": "ALLBUT0999", "response": "json"},
    )
    if j.get("stat") != "OK":
        raise ValueError("MI_INDEX 無資料")
    t = _pick_table(j, "證券代號", "成交股數", "成交金額")
    f = t["fields"]
    si, vi, ai = _find(f, "證券代號"), _find(f, "成交股數"), _find(f, "成交金額")
    ci = _find(f, "收盤價")
    out = {}
    for r in t["data"]:
        vol, amt = num(r[vi], 0) or 0, num(r[ai], 0) or 0
        close = num(r[ci])
        out[str(r[si]).strip()] = (amt / vol) if vol > 0 else (close or 0)
    return out


def _tpex_avg_price(s, d: date) -> dict[str, float]:
    j = get_json(
        s,
        "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes",
        {"date": d.strftime("%Y/%m/%d"), "response": "json"},
    )
    t = _pick_table(j, "成交股數", "成交金額")
    f = t["fields"]
    si = _find(f, "代號")
    vi, ai = _find(f, "成交股數"), _find(f, "成交金額")
    ci = _find(f, "收盤")
    out = {}
    for r in t["data"]:
        vol, amt = num(r[vi], 0) or 0, num(r[ai], 0) or 0
        close = num(r[ci])
        out[str(r[si]).strip()] = (amt / vol) if vol > 0 else (close or 0)
    return out


def _twse_stock_net(s, d: date):
    j = get_json(
        s,
        "https://www.twse.com.tw/rwd/zh/fund/T86",
        {"date": d.strftime("%Y%m%d"), "selectType": "ALLBUT0999", "response": "json"},
    )
    if j.get("stat") != "OK" or not j.get("data"):
        raise ValueError("T86 無資料")
    f = j["fields"]
    si, ni = _find(f, "證券代號"), _find(f, "證券名稱")
    ti = _find(f, "投信", "買賣超股數")
    try:
        fi = _find(f, "外陸資買賣超股數", exclude=("外資自營商",))
        fdi = _find(f, "外資自營商買賣超股數")
    except KeyError:
        fi, fdi = _find(f, "外資", "買賣超股數"), None
    for r in j["data"]:
        fnet = num(r[fi], 0) or 0
        if fdi is not None:
            fnet += num(r[fdi], 0) or 0
        yield str(r[si]).strip(), str(r[ni]).strip(), fnet, num(r[ti], 0) or 0


def _tpex_stock_net(s, d: date):
    """櫃買中心個股三大法人買賣超。

    這張表的『欄位名稱』是「買進股數/賣出股數/買賣超股數」重複七組，
    沒有把投資人別放進欄位名稱裡（跟證交所 T86 不一樣），所以沒辦法用名稱去分辨
    哪一組是外資、哪一組是投信，只能按照官方固定的欄位順序取值：
      0-2  外資及陸資(不含外資自營商)   3-5  外資自營商        6-8  外資及陸資(合計)
      9-11 投信                        12-14 自營商(自行買賣)  15-17 自營商(避險)
      18-20 自營商(合計)                21   三大法人買賣超股數合計
    """
    j = get_json(
        s,
        "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade",
        {"type": "Daily", "sect": "EW", "date": d.strftime("%Y/%m/%d"),
         "response": "json"},
    )
    t = _pick_table(j, "買賣超")
    for r in t["data"]:
        code = str(r[0]).strip()
        if not code or len(code) > 6:
            continue
        name = str(r[1]).strip()
        values = r[2:]
        if len(values) < 12:
            continue
        fnet = num(values[8], 0) or 0   # 外資及陸資(合計) 買賣超股數
        tnet = num(values[11], 0) or 0  # 投信 買賣超股數
        yield code, name, fnet, tnet


def stock_flows(s, d: date) -> list[dict]:
    """上市+上櫃個股外資/投信買賣超金額。

    官方只公布股數，金額 = 買賣超股數 × 當日成交均價（成交金額÷成交股數）估算。
    與券商看盤軟體的數字會有小數點級距的差異，方向與量級一致。
    """
    out = []
    for mkt, netfn, pricefn in (
        ("TWSE", _twse_stock_net, _twse_avg_price),
        ("TPEX", _tpex_stock_net, _tpex_avg_price),
    ):
        prices = pricefn(s, d)
        for code, name, fnet, tnet in netfn(s, d):
            p = prices.get(code)
            if not p:
                continue
            out.append({
                "c": code, "n": name, "m": mkt,
                "f": round(fnet * p),   # 外資買賣超金額(元)
                "t": round(tnet * p),   # 投信買賣超金額(元)
            })
    if not out:
        raise ValueError("個股買賣超無資料")
    return out
