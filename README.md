<div align="center">
  <h1>🐉 妖币雷达 · Yaobi Radar</h1>
  <p>
    <strong>币安USDT合约全市场妖币扫描器</strong>
  </p>
  <p>
    纯公开API · 无需API Key · 一键跑 · AI Agent Skill
  </p>
</div>

---

## 这是什么

**妖币雷达** 是一个全自动妖币扫描器，扫描币安所有 USDT 永续合约，用多维度评分系统找出"妖币"——那些有暴拉/暴跌潜力的高波动币。

灵感来自实盘量化系统 [量仔](https://github.com/wang4866/liangzai-quant-trader) 的8分析师辩论引擎和妖币埋伏方法论。

## 它能做什么

- 🔭 **全市场扫描** — 500+ USDT合约对，找出异动币
- 🎯 **妖力评分** — 9维评分系统（双K反转、资金费率、OI异常、Taker买卖比、多空比…）
- 📊 **自动报告** — 终端彩色输出 + Markdown文件
- 🤖 **AI Agent Skill** — 任何AI代理（Claude Code、Codex、Cursor）都能直接调用
- ⏰ **GitHub Actions** — 每天自动跑，发报告到 Issues 或 Pages

## 快速开始

```bash
# 克隆
git clone https://github.com/wang4866/yaobi-radar.git
cd yaobi-radar

# 安装依赖（就一个requests）
pip install requests

# 直接跑
python3 yaobi_radar.py

# 输出markdown报告
python3 yaobi_radar.py --markdown report.md
```

## 输出示例

```
============================================================
  妖币雷达 | 2025-05-01 08:30:00 UTC
============================================================
  扫描 482 个合约对，发现 12 个妖币

  币对              评分   涨幅        成交额        信号
  --------------------------------------------------------------
  EDENUSDT           9     🟢 +18.23%   $12.5M    🔄 双K反转 +4 ...
  BSVUSDT            8     🔴 -6.12%    $9.2M     💉 扎针/下影线 +2 ...
  HANAUSDT           7     🟢 +8.50%    $5.1M     📈 OI暴增 +25% +2 ...
  ...
```

## 评分系统

| 信号 | 权重 | 说明 |
|------|:----:|------|
| 🔄 双K反转 | +4 | 15m K线阴+阳反转形态 |
| 💉 扎针/下影线 | +2 | 下影线>实体2倍 |
| 📊 放量下影 | +2 | 下影线+成交量放大 |
| 📉 费率加速负 | +2 | 连续3次负费率加深 |
| 📈 OI暴增 | +2 | 持仓量>1.3x均值 |
| 💰 费率百分位 | +1~3 | 处于历史极端百分位 |
| 📊 OI连续上升 | +1 | OI趋势向上 |
| 🟢 Taker买比 | +1 | Taker主动买>卖 |
| 👥 多空比低位 | +1 | 大户多空比<0.8 |
| 🔥 成交量异动 | +1 | 成交量>2x均值 |

**评分解读：**
- 0–3：待观察
- 4–6：有妖气
- 7–9：妖币确认 🐉
- 10+：妖王现身 👑

## AI Agent 使用

这个项目自带 **SKILL.md**，任何支持 Skill 系统的 AI 工具（Claude Code、Codex、OpenCode、Cursor 等）都可以直接加载。

详见 [SKILL.md](./SKILL.md)。

## GitHub Actions 自动扫描

项目包含 `.github/workflows/daily.yml`，开箱即用：
- 每天 UTC 08:00 自动扫描
- 报告发布到 GitHub Issues
- 无需服务器、无需API Key

部署：fork 这个仓库，Actions 会自动启用。

## 技术细节

- **纯公开 API** — 只使用 Binance FAPI 公开端点，不需要任何 API Key
- **限流保护** — 内置 Binance 权重限制保护，不会触发 429
- **轻量** — 只有一个 `requests` 依赖

## 免责声明

🚨 **这不是投资建议。** 妖币扫描器仅用于市场数据分析和学习目的。加密货币交易存在极高风险，妖币尤其危险（经常出现±30%以上的日内波动）。请自行承担风险。

## License

MIT

---

*Made in China, for the world. 🇨🇳 → 🌍*
