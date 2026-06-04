#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
妖币雷达 — 币安全市场妖币扫描 + 评分系统

纯公开API，无需API Key。扫描所有USDT合约对，检测妖币。
使用批量API调用 + 两阶段过滤，效率高。

使用: python3 yaobi_radar.py [--markdown report.md] [--max-deep 30]
"""

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import requests

# ── 配置 ─────────────────────────────────────
BINANCE_FAPI = "https://fapi.binance.com"
EXCLUDE_SYMBOLS = {
    "USDCUSDT", "BUSDUSDT", "TUSDUSDT", "USDPUSDT",
    "FDUSDUSDT", "DAIUSDT", "PAXGUSDT", "EURUSDT",
    "GBPUSDT", "AUDUSDT",
}
MIN_VOLUME_24H = 500_000  # 24h成交额至少50万U才考虑
DEEP_SCORE_LIMIT = 30     # 深度评分最多30个币


# ── HTTP工具 ──────────────────────────────────

_session = requests.Session()

def _get(path: str, params: dict = None, timeout: int = 15) -> list | dict:
    """带重试的GET请求"""
    for attempt in range(3):
        try:
            r = _session.get(f"{BINANCE_FAPI}{path}", params=params, timeout=timeout)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 10))
                print(f"  ⚠️ 限流429，等待 {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.Timeout:
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
        except Exception:
            if attempt < 2:
                time.sleep(1)
            else:
                return []
    return []


def _batch_get(endpoints: list[str], max_workers: int = 5) -> dict[str, list]:
    """并行获取多个币种的相同类型数据"""
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        fut_map = {pool.submit(_get, ep): ep for ep in endpoints}
        for fut in as_completed(fut_map):
            ep = fut_map[fut]
            try:
                results[ep] = fut.result()
            except Exception:
                results[ep] = []
    return results


# ── 数据获取（批量高效版）────────────────────

def fetch_all_tickers() -> list[dict]:
    """获取所有USDT合约24h ticker"""
    data = _get("/fapi/v1/ticker/24hr")
    if not data:
        return []
    result = []
    for t in data:
        sym = t["symbol"]
        if not sym.endswith("USDT"):
            continue
        if sym in EXCLUDE_SYMBOLS:
            continue
        if any(x in sym for x in ["UP", "DOWN", "BULL", "BEAR", "BKRW"]):
            continue
        try:
            vol = float(t["quoteVolume"])
        except (ValueError, TypeError):
            continue
        if vol < MIN_VOLUME_24H:
            continue
        result.append(t)
    return result


def fetch_funding_rates() -> dict[str, float]:
    """一次获取所有币种当前资金费率"""
    data = _get("/fapi/v1/premiumIndex")
    if not data:
        return {}
    return {item["symbol"]: float(item["lastFundingRate"]) for item in data}


def fetch_all_oi() -> dict[str, float]:
    """一次获取所有币种当前持仓量"""
    data = _get("/fapi/v1/ticker/24hr")  # 复用ticker vol数据
    if not data:
        return {}
    return {t["symbol"]: float(t.get("openInterest", 0)) for t in data}


def fetch_Klines_batch(symbols: list[str], interval: str = "15m", limit: int = 60,
                       max_workers: int = 10) -> dict[str, list]:
    """并行获取多个币种的K线数据"""
    endpoints = [f"/fapi/v1/klines?symbol={s}&interval={interval}&limit={limit}" for s in symbols]
    raw = _batch_get(endpoints, max_workers)
    result = {}
    for s in symbols:
        ep = f"/fapi/v1/klines?symbol={s}&interval={interval}&limit={limit}"
        result[s] = raw.get(ep, [])
    return result


def fetch_oi_snapshot(symbols: list[str], max_workers: int = 10) -> dict[str, list]:
    """并行获取多个币种的OI历史（12h=12条）"""
    endpoints = [f"/futures/data/openInterestHist?symbol={s}&period=1h&limit=12" for s in symbols]
    raw = _batch_get(endpoints, max_workers)
    result = {}
    for s in symbols:
        ep = f"/futures/data/openInterestHist?symbol={s}&period=1h&limit=12"
        result[s] = raw.get(ep, [])
    return result


def fetch_funding_history_batch(symbols: list[str], limit: int = 50,
                                max_workers: int = 10) -> dict[str, list[float]]:
    """并行获取多个币种的历史资金费率"""
    endpoints = [f"/fapi/v1/fundingRate?symbol={s}&limit={limit}" for s in symbols]
    raw = _batch_get(endpoints, max_workers)
    result = {}
    for s in symbols:
        ep = f"/fapi/v1/fundingRate?symbol={s}&limit={limit}"
        data = raw.get(ep, [])
        result[s] = [float(item["fundingRate"]) for item in data] if data else []
    return result


# ── 妖币评分引擎（单币）────────────────────

def score_single(symbol: str, klines_15m: list, klines_1d: list,
                 oi_data: list, rates_history: list[float],
                 ticker: dict, funding_rate: float | None) -> dict:
    """对一个币做妖币评分（使用预取数据，不再发API请求）"""
    info = {"symbol": symbol, "score": 0, "signals": [], "details": {}}

    # ---- 1. 双K反转 (权重: +4) ----
    if len(klines_15m) >= 4:
        k15 = klines_15m
        c1, c2, c3, c4 = [float(k[4]) for k in k15[-4:]]
        o1, o2, o3, o4 = [float(k[1]) for k in k15[-4:]]
        h1, h2, h3, h4 = [float(k[2]) for k in k15[-4:]]
        l1, l2, l3, l4 = [float(k[3]) for k in k15[-4:]]

        # 双K反转: 阴线+阳线
        bear_body = abs(o1 - c1)
        bull_body = abs(o2 - c2)
        if o1 > c1 and c2 > o2 and bull_body > bear_body * 0.6:
            info["score"] += 4
            info["signals"].append("🔄 双K反转 +4")
            info["details"]["dual_candle_reversal"] = 4

        # 扎针/下影线
        if len(k15) >= 3:
            o3, c3, h3, l3 = o3, c3, h3, l3
            lower_shadow = min(o3, c3) - l3
            candle_body = abs(o3 - c3)
            if candle_body > 0 and lower_shadow > candle_body * 2:
                info["score"] += 2
                info["signals"].append("💉 扎针/下影线 +2")
                info["details"]["pin_bar"] = 2

            # 放量下影
            vol3 = float(k15[-3][5])
            avg_vol = sum(float(k[5]) for k in k15[-20:]) / max(len(k15[-20:]), 1)
            if candle_body > 0 and lower_shadow > candle_body * 1.5 and vol3 > avg_vol * 1.5:
                info["score"] += 2
                info["signals"].append("📊 放量下影 +2")
                info["details"]["volume_pin"] = 2

    # ---- 2. 资金费率百分位 ----
    if len(rates_history) >= 7:
        current_rate = rates_history[-1]
        recent = rates_history[-21:]
        below = sum(1 for r in recent if r < current_rate)
        percentile = below / len(recent) * 100

        if percentile <= 10:
            strength = 3 if percentile <= 3 else 2 if percentile <= 7 else 1
            score = min(strength, 4)
            info["score"] += score
            info["signals"].append(f"💰 费率百分位{percentile:.0f}% (空头极端) +{score}")
            info["details"]["funding_percentile"] = percentile
        elif percentile >= 90:
            strength = 3 if percentile >= 97 else 2 if percentile >= 93 else 1
            score = min(strength, 4)
            info["score"] += score
            info["signals"].append(f"💰 费率百分位{percentile:.0f}% (多头拥挤) +{score}")
            info["details"]["funding_percentile"] = percentile

        # 费率递进
        if len(rates_history) >= 7:
            last3 = rates_history[-3:]
            if all(r < 0 for r in last3) and last3[-1] < last3[0]:
                info["score"] += 2
                info["signals"].append("📉 费率加速负 (空头恐慌加深) +2")
                info["details"]["funding_acceleration"] = 2

    # ---- 3. OI异常 ----
    if len(oi_data) >= 6:
        ois = [float(o["sumOpenInterest"]) for o in oi_data]
        current_oi = ois[-1]
        avg_oi = sum(ois) / len(ois)
        ratio = current_oi / max(avg_oi, 1)
        if ratio > 1.3:
            info["score"] += 2
            info["signals"].append(f"📈 OI暴增 +{int((ratio-1)*100)}% +2")
            info["details"]["oi_surge"] = round(ratio, 2)
        oi_last3 = ois[-3:]
        if len(oi_last3) >= 3 and oi_last3[-1] > oi_last3[0] * 1.05:
            info["score"] += 1
            info["signals"].append("📊 OI连续上升 +1")
            info["details"]["oi_trend"] = "rising"

    # ---- 4. 成交量异动（来自1d K线） ----
    if len(klines_1d) >= 3:
        vols = [float(k[5]) for k in klines_1d]
        current_vol = vols[-1]
        avg_vol = sum(vols[:-1]) / max(len(vols) - 1, 1)
        vol_ratio = current_vol / max(avg_vol, 1)
        if vol_ratio > 2.0:
            info["score"] += 1
            info["signals"].append(f"🔥 成交量{vol_ratio:.1f}x均值 +1")
            info["details"]["vol_ratio"] = round(vol_ratio, 1)
        info["details"]["vol_ratio"] = round(vol_ratio, 1)

    # ---- 5. 24h跌幅加分（跌越多反弹潜力越大） ----
    try:
        pct = float(ticker.get("priceChangePercent", 0))
        if pct < -8:
            info["score"] += 1
            info["signals"].append(f"📉 暴跌{pct:.1f}% (超跌反弹预期) +1")
    except (ValueError, TypeError):
        pass

    return info


# ── 报告生成 ──────────────────────────────────

def build_report(candidates: list[dict], scored: list[dict],
                 scanned_count: int) -> tuple[str, str]:
    """生成终端报告和markdown报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    term_lines = []
    md_lines = []

    scored.sort(key=lambda x: x["score"], reverse=True)

    # 终端报告
    term_lines.append(f"\n{'='*60}")
    term_lines.append(f"  妖币雷达 | {now}")
    term_lines.append(f"{'='*60}")
    term_lines.append(f"  扫描 {scanned_count} 个合约对，深度分析 {len(scored)} 个妖币\n")

    term_lines.append(f"  {'币对':<14} {'评分':<6} {'涨幅':<10} {'成交额':<12} {'信号'}")
    term_lines.append(f"  {'-'*14} {'-'*6} {'-'*10} {'-'*12} {'-'*30}")
    for s in scored[:20]:
        sym = s["symbol"]
        score = s["score"]
        sigs = " ".join(s["signals"][:3])
        cand = next((c for c in candidates if c["symbol"] == sym), None)
        if cand:
            pct = float(cand.get("priceChangePercent", 0))
            vol_m = float(cand.get("quoteVolume", 0)) / 1_000_000
            emoji = "🟢" if pct > 0 else "🔴"
            pct_str = f"{emoji} {pct:+.2f}%"
            vol_str = f"${vol_m:.1f}M"
        else:
            pct_str = "?"
            vol_str = "?"
        term_lines.append(f"  {sym:<14} {score:^4}  {pct_str:<10} {vol_str:<12} {sigs}")

    term_lines.append("")
    term_lines.append("  💡 评分解读:")
    term_lines.append("     0-3  待观察")
    term_lines.append("     4-6  有妖气")
    term_lines.append("     7-9  妖币确认 🐉")
    term_lines.append("    10+  妖王现身 👑")
    term_lines.append("")

    # Markdown报告
    md_lines.append("# 🐉 妖币雷达日报")
    md_lines.append("")
    md_lines.append(f"> **扫描时间**: {now}")
    md_lines.append("> **数据来源**: Binance FAPI (公开API, 无需Key)")
    md_lines.append("")
    md_lines.append(f"扫描 {scanned_count} 个USDT合约对，发现 **{len(scored)}** 个妖币（评分≥3）。")
    md_lines.append("")

    md_lines.append("## 🏆 妖币排行榜")
    md_lines.append("")
    md_lines.append("| 排名 | 币对 | 评分 | 24h涨跌 | 成交额 | 妖币信号 |")
    md_lines.append("|:----:|------|:----:|:-------:|:------:|----------|")
    for rank, s in enumerate(scored[:20], 1):
        sym = s["symbol"]
        score = s["score"]
        sigs = " ".join(s["signals"][:3])
        cand = next((c for c in candidates if c["symbol"] == sym), None)
        if cand:
            pct = float(cand.get("priceChangePercent", 0))
            vol_m = float(cand.get("quoteVolume", 0)) / 1_000_000
            pct_str = f"{pct:+.2f}%"
        else:
            pct_str = "?"
            vol_m = 0
        md_lines.append(f"| {rank} | {sym} | **{score}** | {pct_str} | ${vol_m:.1f}M | {sigs} |")

    md_lines.append("")
    md_lines.append("## 📋 评分标准")
    md_lines.append("")
    md_lines.append("| 信号 | 权重 | 说明 |")
    md_lines.append("|------|:----:|------|")
    md_lines.append("| 🔄 双K反转 | +4 | 15m K线阴+阳反转 |")
    md_lines.append("| 💉 扎针/下影线 | +2 | 下影线>实体2x |")
    md_lines.append("| 📊 放量下影 | +2 | 下影线+放量 |")
    md_lines.append("| 📉 费率加速负 | +2 | 连续3次负费率加深 |")
    md_lines.append("| 📈 OI暴增 | +2 | OI>1.3x均值 |")
    md_lines.append("| 💰 费率百分位 | +1~3 | 极端百分位 |")
    md_lines.append("| 📊 OI上升 | +1 | OI连续上升 |")
    md_lines.append("| 🔥 成交量异动 | +1 | 成交量>2x均值 |")
    md_lines.append("| 📉 超跌反弹 | +1 | 24h跌>8% |")
    md_lines.append("")
    md_lines.append("---")
    md_lines.append("*Generated by [妖币雷达](https://github.com/wang4866/yaobi-radar)*")

    return "\n".join(term_lines), "\n".join(md_lines)


# ── 可调用的扫描函数（供其他模块导入）─────────

def run_scan(max_deep: int = DEEP_SCORE_LIMIT, verbose: bool = False) -> dict:
    """执行一次完整扫描，返回结构化结果。"""
    def log(msg):
        if verbose: print(msg, flush=True)

    log("  📡 获取全市场数据...")
    tickers = fetch_all_tickers()
    all_funding_rates = fetch_funding_rates()
    log(f"  OK ({len(tickers)}个合约对)")

    log("  🔍 初步筛选...")
    candidates = []
    for t in tickers:
        try:
            pct = abs(float(t["priceChangePercent"]))
            vol = float(t["quoteVolume"])
            sym = t["symbol"]
            fr = all_funding_rates.get(sym, 0)
            if (pct > 4.0 or vol > 10_000_000 or abs(fr) > 0.001 or float(t.get("priceChangePercent", 0)) < -6):
                candidates.append(t)
        except (ValueError, TypeError):
            continue
    log(f"  {len(candidates)}个候选")

    def rank_key(t):
        try:
            pct = abs(float(t["priceChangePercent"]))
            vol = float(t["quoteVolume"])
            return pct * 0.6 + math.log10(max(vol, 1)) * 2
        except: return 0

    candidates.sort(key=rank_key, reverse=True)
    deep_symbols = [t["symbol"] for t in candidates[:max_deep]]

    if not deep_symbols:
        return {"scored": [], "candidates": [], "total_scanned": len(tickers),
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")}

    log(f"  📦 批量获取K线/OI/费率历史... ({len(deep_symbols)}个币)")
    klines_15m_data = fetch_Klines_batch(deep_symbols, "15m", 60, max_workers=10)
    klines_1d_data = fetch_Klines_batch(deep_symbols, "1d", 8, max_workers=10)
    oi_data = fetch_oi_snapshot(deep_symbols, max_workers=10)
    funding_history = fetch_funding_history_batch(deep_symbols, limit=50, max_workers=10)
    log("  OK")

    log("  ⚡ 妖力评分中...")
    scored = []
    for i, sym in enumerate(deep_symbols):
        if verbose:
            print(f"\r    [{i+1}/{len(deep_symbols)}] {sym:<12}", end="", flush=True)
        try:
            ticker = next(t for t in tickers if t["symbol"] == sym)
            result = score_single(sym,
                klines_15m_data.get(sym, []),
                klines_1d_data.get(sym, []),
                oi_data.get(sym, []),
                funding_history.get(sym, []),
                ticker, all_funding_rates.get(sym))
            if result["score"] >= 3: scored.append(result)
        except Exception: pass
    if verbose: print()

    return {"scored": scored, "candidates": candidates, "tickers": tickers,
            "total_scanned": len(tickers),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")}


# ── CLI主流程 ─────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="妖币雷达 — 币安全市场妖币扫描器")
    parser.add_argument("--markdown", "-m", type=str, default="",
                        help="输出markdown文件路径")
    parser.add_argument("--max-deep", type=int, default=DEEP_SCORE_LIMIT,
                        help=f"深度评分币数上限 (默认{DEEP_SCORE_LIMIT})")
    parser.add_argument("--json", "-j", action="store_true",
                        help="输出JSON到stdout")
    parser.add_argument("--no-headline", action="store_true",
                        help="不打印启动横幅（用于AI调用）")
    args = parser.parse_args()

    if not args.no_headline:
        print("🐉 妖币雷达启动中...")
        print(f"  ⏱  {datetime.now().strftime('%H:%M:%S')}")

    result = run_scan(max_deep=args.max_deep, verbose=True)

    if args.json:
        json_out = {"timestamp": result["timestamp"],
                    "total_scanned": result["total_scanned"],
                    "scored": [{"symbol": s["symbol"], "score": s["score"],
                                "signals": s["signals"],
                                "price_change_pct": float(
                                    next((c.get("priceChangePercent", 0)
                                          for c in result["candidates"]
                                          if c["symbol"] == s["symbol"]), 0)),
                                "quote_volume": float(
                                    next((c.get("quoteVolume", 0)
                                          for c in result["candidates"]
                                          if c["symbol"] == s["symbol"]), 0)),
                               } for s in result["scored"]]}
        print(json.dumps(json_out, ensure_ascii=False, indent=2))
        return

    if not result["scored"]:
        print("  ⚠️  没有找到妖币")
        if args.markdown:
            with open(args.markdown, "w") as f:
                f.write("# 🐉 妖币雷达日报\n\n⚠️ 本次扫描未发现妖币\n")
        return

    term_report, md_report = build_report(
        result["candidates"], result["scored"], result["total_scanned"])
    print(term_report)

    if args.markdown:
        out_dir = os.path.dirname(args.markdown) or "."
        os.makedirs(out_dir, exist_ok=True)
        with open(args.markdown, "w") as f:
            f.write(md_report)
        print(f"  📝 报告已保存: {args.markdown}")

    print(f"  ✅ 扫描完成")


if __name__ == "__main__":
    main()
