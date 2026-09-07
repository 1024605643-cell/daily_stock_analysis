"""Run the bundled AlphaSift screen, then DSA analysis of its shortlist."""

import argparse
from dataclasses import asdict
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def shortlist_codes(result):
    """Reject incomplete universe responses and unsafe candidate identifiers."""
    if result.snapshot_count < 3000 or "cache" in result.snapshot_source.lower():
        raise ValueError("Full-market live snapshot unavailable; refusing stale/partial picks")
    codes = []
    for pick in result.picks:
        if not re.fullmatch(r"\d{6}", pick.code) or pick.price <= 0:
            raise ValueError("Invalid screening candidate")
        if not pick.excluded_by_risk and pick.code not in codes:
            codes.append(pick.code)
    return codes[:3]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-run", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT)
    logging.basicConfig(level=logging.INFO)
    from src.core.trading_calendar import is_market_open

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if not args.force_run and not is_market_open("cn", today):
        print("A-share non-trading day: skipped")
        return

    from src.services.screening.config import Config
    from src.services.screening.pipeline import screen

    config = Config()
    config.fallback_snapshot_path = None
    result = screen(
        "balanced_alpha", max_output=3, use_llm=False,
        daily_enrich=True, daily_enrich_max_candidates=30, config=config,
    )
    codes = shortlist_codes(result)
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "market_screen.json").write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8",
    )
    lines = [
        f"# 全市场选股候选 · {today}",
        "", "策略：AlphaSift 均衡多因子；候选仍需 GPT 深度分析确认买入条件。",
        f"数据源：{result.snapshot_source}；扫描 {result.snapshot_count} 只，"
        f"过滤后 {result.after_filter_count} 只。",
        "", "| 代码 | 名称 | 因子评分 | 参考价格 | 风险 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for pick in result.picks:
        if pick.code in codes:
            lines.append(
                f"| {pick.code} | {pick.name} | {pick.final_score:.1f} | "
                f"{pick.price:.2f} | {pick.risk_level} |"
            )
    if not codes:
        lines.append("\n今日没有通过筛选的候选，不强行推荐。")
    if result.degradation:
        lines += ["", "数据质量与降级记录：", *[f"- {x}" for x in result.degradation]]
    report = "\n".join(lines)
    (reports / "market_screen.md").write_text(report, encoding="utf-8")
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(report + "\n")
    from src.notification import NotificationService

    if not NotificationService().send(report):
        raise RuntimeError("Screening report notification failed")
    if codes:
        command = [sys.executable, "main.py", "--stocks", ",".join(codes)]
        if args.force_run:
            command.append("--force-run")
        subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
