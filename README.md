# 持股水位建議

每日收盤後依大盤量價、籌碼、廣度、恐慌與外部指標，給出整體持股水位建議（0–100%）與明日劇本。

- 網站：`docs/`（GitHub Pages，來源設為 main 分支的 /docs）
  - `docs/index.html`：持股水位、盤勢狀態、劇本
  - `docs/chip/`：法人籌碼儀錶板（由原 chip-dashboard 併入）
- 籌碼資料：`chip/` 抓證交所、櫃買、期交所 → `data/daily/` → `docs/chip/data/dashboard.json`
- 每日更新：`.github/workflows/daily.yml`（籌碼 → 指數 → 水位），可用 workflow_dispatch 觸發
- 資料：把 CSV 放進 `data/raw/`，規格見 [data/README.md](data/README.md)
- 引擎：`engine/build.py` 讀資料 → 輸出 `docs/latest.json`
- 自動化：推送 `data/raw/` 後，GitHub Actions 會重算並更新網站

本機執行：
```
pip install -r requirements.txt
python engine/build.py
```

## 模型狀態
目前為 **v0 初版規則**，權重與門檻尚未經過回測驗證。下一階段：
1. 資料檢查與清理
2. 基準：持有 100%、季線規則、隨機水位
3. 2015/6–2024/12 擴張窗口滾動訓練（每 6 個月、embargo 20 日）
4. 2025/1 起最終驗收，只跑一次
