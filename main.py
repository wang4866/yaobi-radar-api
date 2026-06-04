"""
妖币雷达 API — FastAPI 封装
自动缓存扫描结果，后端定时刷新，前端瞬间响应
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# 添加父目录到路径，导入妖币雷达核心
sys.path.insert(0, str(Path(__file__).parent.parent))
import yaobi_radar

# ── 配置 ──────────────────────────────────────
CACHE_TTL = 300  # 缓存有效期 5 分钟
REFRESH_INTERVAL = 300  # 后台刷新间隔 5 分钟
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
    """扫描结果缓存（内存 + 文件双保险）"""

    def __init__(self):
        self.data = None
        self.updated_at = None
        self.is_scanning = False

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
        # 写文件备份
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump({"data": data, "updated_at": self.updated_at}, f)
        except Exception:
            pass

    def load_from_file(self) -> bool:
        try:
            with open(CACHE_FILE) as f:
                saved = json.load(f)
            self.data = saved["data"]
            self.updated_at = saved["updated_at"]
            return True
        except Exception:
            return False


cache = ScanCache()


# ── 后台扫描任务 ──────────────────────────────

async def run_scan_async() -> dict:
    """异步执行扫描（在线程池中运行同步代码）"""
    loop = asyncio.get_event_loop()
    # 缩小深度扫描数量，加快 API 响应速度
    result = await loop.run_in_executor(None, yaobi_radar.run_scan, 20, False)
    return result


def format_result(raw: dict) -> dict:
    """格式化输出"""
    scored = []
    for s in raw.get("scored", []):
        scored.append({
            "symbol": s["symbol"],
            "score": s["score"],
            "signals": s["signals"],
            "details": s.get("details", {}),
        })
    # 按评分降序
    scored.sort(key=lambda x: x["score"], reverse=True)
    return {
        "success": True,
        "timestamp": raw.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")),
        "total_scanned": raw.get("total_scanned", 0),
        "yaobi_count": len(scored),
        "scored": scored,
    }


async def background_refresh():
    """后台定时刷新缓存"""
    await asyncio.sleep(10)  # 启动后等 10 秒再首次扫描
    while True:
        try:
            cache.is_scanning = True
            raw = await run_scan_async()
            formatted = format_result(raw)
            cache.set(formatted)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ 后台扫描完成: "
                  f"{formatted['yaobi_count']} 个妖币")
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ 后台扫描失败: {e}")
        finally:
            cache.is_scanning = False
        await asyncio.sleep(REFRESH_INTERVAL)


@app.on_event("startup")
async def startup():
    """启动时立即开始后台扫描（第一次扫描约 15-30 秒完成）"""
    print("🚀 妖币雷达 API 启动中...")
    # 尝试从文件加载上次缓存
    if cache.load_from_file():
        print(f"📦 加载历史缓存 (年龄 {cache.get_age_seconds()}s)")
    else:
        print("📦 无历史缓存，等待首次扫描...")

    # 启动后台刷新（10 秒后自动首次扫描）
    asyncio.create_task(background_refresh())
    print("✅ 后台扫描任务已启动")
    asyncio.create_task(background_refresh())


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
            "GET /scan/refresh": "强制刷新扫描（Pro 用户可用）",
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
        "is_scanning": cache.is_scanning,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


@app.get("/scan")
async def get_scan(
    top: int = Query(20, ge=1, le=100, description="返回前 N 个妖币"),
    min_score: int = Query(3, ge=0, le=20, description="最低评分过滤"),
):
    """
    获取最新妖币扫描结果

    返回缓存的扫描结果（每 5 分钟自动刷新），响应速度 < 10ms。
    """
    if not cache.data and cache.is_scanning:
        return JSONResponse(
            status_code=202,
            content={
                "success": True,
                "status": "warming_up",
                "message": "正在首次扫描中，请 15 秒后再试",
                "estimated_ready_in": 15,
            }
        )

    if not cache.data:
        return JSONResponse(
            status_code=202,
            content={
                "success": True,
                "status": "initializing",
                "message": "服务启动中，扫描尚未开始",
            }
        )

    # 过滤和截取
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


@app.get("/scan/refresh")
async def refresh_scan():
    """
    强制刷新扫描

    触发一次新的全市场扫描（耗时约 15-30 秒）。
    本服务使用缓存的扫描结果，默认每 5 分钟自动刷新。
    通过此端点可手动触发即时刷新。
    """
    if cache.is_scanning:
        raise HTTPException(409, "正在扫描中，请稍后再试")

    cache.is_scanning = True
    try:
        raw = await run_scan_async()
        cache.set(format_result(raw))
        return {
            "success": True,
            "message": "扫描完成",
            "yaobi_count": cache.data["yaobi_count"],
            "timestamp": cache.data["timestamp"],
        }
    except Exception as e:
        raise HTTPException(500, f"扫描失败: {e}")
    finally:
        cache.is_scanning = False


@app.get("/coins/{symbol}")
async def get_coin(symbol: str):
    """
    查询单个币种详情

    在最新扫描结果中查找指定币种。
    如果币种不在妖币列表（评分≤3），返回基本信息。
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

    # 不在缓存中，临时查一下这个币
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


@app.get("/openapi.json")
async def get_openapi():
    """返回 OpenAPI 规范"""
    return app.openapi()
