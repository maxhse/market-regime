# 資料放置規格

把 TEJ / CMoney 匯出的資料整理成下列 CSV，放進 `data/raw/`。
- 期間：2014/01/01 至今，日資料
- 第一欄一律是 `date`，格式 `YYYY-MM-DD`
- 金額單位一律用「億元」
- 缺的檔案可以先不放，網站會標示「缺資料」，相關指標暫停計算

| # | 檔名 | 欄位 | 必要性 | 用途 |
|---|---|---|---|---|
| 1 | `taiex.csv` | date, open, high, low, close, turnover | 必備 | 加權指數 OHLC、成交金額 |
| 2 | `tpex.csv` | date, open, high, low, close, turnover | 必備 | 櫃買指數 OHLC、成交金額 |
| 3 | `taiex_tr.csv` | date, close | 必備 | 加權報酬指數（回測報酬用） |
| 3b | `tpex_tr.csv` | date, close | 選用 | 櫃買報酬指數 |
| 4 | `tsmc.csv` | date, close, shares | 必備 | 台積電收盤價、發行股數（股），做「加權扣台積電」 |
| 5 | `margin.csv` | date, twse_margin, twse_short, tpex_margin, tpex_short | 必備 | 上市/上櫃融資、融券餘額（金額） |
| 6 | `mktcap.csv` | date, twse_mktcap, tpex_mktcap | 必備 | 上市/上櫃總市值 |
| 7 | `inst.csv` | date, foreign_net, trust_net | 必備 | 上市外資、投信買賣超金額 |
| 8 | `futures.csv` | date, foreign_tx_net_oi, pc_ratio | 必備 | 外資台指期淨未平倉口數（日盤）、選擇權 P/C ratio（未平倉） |
| 9 | `breadth.csv` | date, twse_up, twse_down, twse_limit_up, twse_limit_down, tpex_up, tpex_down, tpex_limit_up, tpex_limit_down | 必備 | 漲跌家數、漲跌停家數 |
| 10 | `external.csv` | date, usdtwd, sox | 必備 | 台幣匯率、費半指數 |
| 11 | `stock_margin.csv` | date, stock_id, margin_shares, close | 建議 | 個股融資張數＋收盤價，推算大盤融資維持率 |
| 12 | `stock_close.csv` | date, stock_id, close | 次要 | 個股收盤價（含下市櫃），算站上 60 日線比例、新高新低家數 |

## TEJ / CMoney 對照提示
- 報酬指數：TEJ「發行量加權股價報酬指數」代碼 Y9997；CMoney 名稱類似「加權報酬指數」
- 期貨與 P/C ratio 如果資料庫沒有，可從期交所網站下載歷史檔
- 漲停、跌停家數如果找不到，可以先放漲跌家數，漲跌停欄位留空

## 更新方式
每天收盤後把新資料附加到同一份 CSV 推上 GitHub，網站會自動重算。之後改串券商 API 時，只要讓程式寫出同樣格式的 CSV 即可。
