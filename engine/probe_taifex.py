"""一次性：列出期交所 OpenAPI 的所有端點，存成 data/probe/taifex_openapi.json。"""
import json, re, ssl, urllib.request
from pathlib import Path
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
out = Path("data/probe"); out.mkdir(parents=True, exist_ok=True)
def get(u):
    return urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=40, context=CTX).read().decode("utf-8", "replace")
res = {}
home = get("https://openapi.taifex.com.tw/")
res["home_refs"] = re.findall(r'(?:src|href|url)\s*[:=]\s*["\']([^"\']+)', home)
for u in ["https://openapi.taifex.com.tw/swagger.json", "https://openapi.taifex.com.tw/v1/swagger.json",
          "https://openapi.taifex.com.tw/swagger/v1/swagger.json", "https://openapi.taifex.com.tw/swagger-initializer.js"]:
    try:
        t = get(u); res[u] = t[:200]
        if t.lstrip().startswith("{"):
            spec = json.loads(t)
            res["paths"] = {p: (v.get("get") or {}).get("summary", "") or (v.get("get") or {}).get("description", "")
                            for p, v in spec.get("paths", {}).items()}
            break
        for m in re.findall(r'url:\s*["\']([^"\']+)', t):
            res.setdefault("init_urls", []).append(m)
    except Exception as e:  # noqa: BLE001
        res[u] = repr(e)[:200]
for u in res.get("init_urls", []):
    try:
        full = u if u.startswith("http") else "https://openapi.taifex.com.tw/" + u.lstrip("./")
        spec = json.loads(get(full))
        res["paths"] = {p: (v.get("get") or {}).get("summary", "") or (v.get("get") or {}).get("description", "")
                        for p, v in spec.get("paths", {}).items()}
        break
    except Exception as e:  # noqa: BLE001
        res[u] = repr(e)[:200]
(out / "taifex_openapi.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(len(res.get("paths", {})), "paths")
