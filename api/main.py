"""
妖币雷达 API — Vercel Serverless 版

适配 Vercel 无服务器环境：
- ❌ 无后台任务 → ✅ 按需扫描 + /tmp 缓存
- ✅ 使用 /tmp 持久化（Vercel 同一实例保活期间共享）
- ✅ 冷启动时自动触发首次扫描
"""
import json
import sys
import time
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# yaobi_radar.py 在项目根目录，Vercel 自动包含在路径中
import yaobi_radar

# ── 配置 ──────────────────────────────────────
CACHE_TTL = 300  # 缓存有效期 5 分钟
CACHE_FILE = "/tmp/yaobi_cache.json"

app = FastAPI(
    title="妖币雷达 API",
    description="🐉 Binance 全市场妖币扫描与评分系统\n\n"
                "自动扫描所有 USDT 永续合约，9 维度妖力评分，发现潜在波动币种。\n\n"
                "数据来源: Binance FAPI (公开 API，无需 API Key)",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 缓存管理 ──────────────────────────────────

class ScanCache:
    """扫描结果缓存（/tmp 文件持久化）"""

    def __init__(self):
        self.data = None
        self.updated_at = None
        self.is_scanning = False
        self._load()

    def is_valid(self) -> bool:
        if self.data is None:
            return False
        age = time.time() - self.updated_at
        return age < CACHE_TTL

    def get_age_seconds(self) -> int:
        if self.updated_at is None:
            return -1
        return int(time.time() - self.updated_at)

    def set(self, data: dict):
        self.data = data
        self.updated_at = time.time()
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump({"data": data, "updated_at": self.updated_at}, f)
        except Exception:
            pass

    def _load(self) -> bool:
        try:
            with open(CACHE_FILE) as f:
                saved = json.load(f)
            self.data = saved["data"]
            self.updated_at = saved["updated_at"]
            return True
        except Exception:
            return False


cache = ScanCache()


def format_result(raw: dict) -> dict:
    """格式化扫描结果"""
    scored = []
    for s in raw.get("scored", []):
        scored.append({
            "symbol": s["symbol"],
            "score": s["score"],
            "signals": s["signals"],
            "details": s.get("details", {}),
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return {
        "success": True,
        "timestamp": raw.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")),
        "total_scanned": raw.get("total_scanned", 0),
        "yaobi_count": len(scored),
        "scored": scored,
    }


def run_scan_sync(top_n: int = 20) -> dict:
    """同步执行扫描"""
    raw = yaobi_radar.run_scan(top_n, False)
    return format_result(raw)


# ── API 路由 ──────────────────────────────────

@app.get("/")
async def root():
    """API 根路径"""
    return {
        "name": "妖币雷达 API",
        "version": "2.0.0",
        "docs": "/docs",
        "endpoints": {
            "GET /scan": "获取最新妖币扫描结果（缓存）",
            "GET /scan/refresh": "强制刷新扫描",
            "GET /coins/{symbol}": "查询单个币种详情",
            "GET /health": "服务健康检查",
        }
    }


@app.get("/health")
async def health():
    """健康检查"""
    return {
        "status": "ok",
        "cache_valid": cache.is_valid(),
        "cache_age_seconds": cache.get_age_seconds(),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


@app.get("/scan")
async def get_scan(
    top: int = Query(20, ge=1, le=100, description="返回前 N 个妖币"),
    min_score: int = Query(3, ge=0, le=20, description="最低评分过滤"),
):
    """
    获取最新妖币扫描结果

    返回缓存的扫描结果。首次请求会触发一次全市场扫描（约 10-20 秒）。
    后续请求秒回，缓存每 5 分钟自动过期并重新扫描。
    """
    # 有有效缓存 → 直接返回
    if cache.is_valid():
        scored = [s for s in cache.data["scored"] if s["score"] >= min_score]
        scored = scored[:top]
        return {
            "success": True,
            "timestamp": cache.data["timestamp"],
            "total_scanned": cache.data["total_scanned"],
            "yaobi_count": len(scored),
            "cache_age_seconds": cache.get_age_seconds(),
            "scored": scored,
        }

    # 缓存过期或无缓存 → 执行扫描
    try:
        result = run_scan_sync()
        cache.set(result)
        scored = [s for s in result["scored"] if s["score"] >= min_score]
        scored = scored[:top]
        return {
            "success": True,
            "timestamp": result["timestamp"],
            "total_scanned": result["total_scanned"],
            "yaobi_count": len(scored),
            "cache_age_seconds": 0,
            "scored": scored,
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "error": f"扫描失败: {str(e)}",
            }
        )


@app.get("/scan/refresh")
async def refresh_scan():
    """
    强制刷新扫描

    触发一次新的全市场扫描（耗时约 10-20 秒）。
    """
    try:
        result = run_scan_sync()
        cache.set(result)
        return {
            "success": True,
            "message": "扫描完成",
            "yaobi_count": result["yaobi_count"],
            "timestamp": result["timestamp"],
        }
    except Exception as e:
        raise HTTPException(500, f"扫描失败: {e}")


@app.get("/coins/{symbol}")
async def get_coin(symbol: str):
    """
    查询单个币种详情
    """
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"

    # 从缓存中查找
    if cache.data:
        for s in cache.data.get("scored", []):
            if s["symbol"] == symbol:
                return {
                    "success": True,
                    "found": True,
                    "coin": s,
                    "cache_timestamp": cache.data["timestamp"],
                }

    # 临时查一下
    try:
        tickers = yaobi_radar.fetch_all_tickers()
        ticker = next((t for t in tickers if t["symbol"] == symbol), None)
        if ticker:
            return {
                "success": True,
                "found": True,
                "coin": {
                    "symbol": symbol,
                    "score": 0,
                    "signals": ["未进入深度分析（评分 < 3 或成交量不足）"],
                    "details": {
                        "price": float(ticker.get("lastPrice", 0)),
                        "price_change_pct": float(ticker.get("priceChangePercent", 0)),
                        "volume_24h_usdt": float(ticker.get("quoteVolume", 0)),
                    }
                }
            }
    except Exception:
        pass

    raise HTTPException(404, f"未找到币种 {symbol}")
