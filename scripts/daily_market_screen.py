"""Daily full-market screening with GPT review and email."""
import argparse
from dataclasses import asdict
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo
import requests
from json_repair import loads as repair_json_loads

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def shortlist_codes(result):
    if result.snapshot_count < 3000 or "cache" in result.snapshot_source.lower():
        raise ValueError("Full-market live snapshot unavailable; refusing stale/partial picks")
    codes=[]
    for pick in result.picks:
        if not re.fullmatch(r"\d{6}",pick.code) or pick.price <= 0:
            raise ValueError("Invalid screening candidate")
        if not pick.excluded_by_risk and pick.code not in codes:
            codes.append(pick.code)
    return codes[:3]

def extract_sse_text(lines):
    chunks=[]
    completed_text=""
    seen=[]
    event_type=""
    for line in lines:
        if isinstance(line,bytes):
            line=line.decode("utf-8",errors="replace")
        if line and line.startswith("event:"):
            event_type=line[6:].strip()
            continue
        if not line or not line.startswith("data:"):
            continue
        raw=line[5:].strip()
        if raw=="[DONE]":
            break
        try:
            event=json.loads(raw)
        except json.JSONDecodeError:
            event=repair_json_loads(raw)
        seen.append(f"{event.get('type')}:{','.join(event.keys())}")
        if event.get("type")=="response.output_text.delta" or event_type=="response.output_text.delta":
            chunks.append(event.get("delta",""))
        for choice in event.get("choices",[]):
            chunks.append(choice.get("delta",{}).get("content",""))
        response=event.get("response",event)
        texts=[item.get("text","") for output in response.get("output",[]) for item in output.get("content",[]) if item.get("type") in {"output_text","text"}]
        if texts:
            completed_text="\n".join(texts)
    text="".join(chunks) or completed_text
    if not text:
        logging.warning("GPT SSE event shapes: %s", seen[-12:])
    return text

def gpt_review(result,codes):
    base_url=os.environ["LLM_PRIMARY_BASE_URL"].rstrip("/")
    model=os.environ["LLM_PRIMARY_MODELS"].split(",",1)[0].strip()
    candidates=[]
    for p in result.picks:
        if p.code in codes:
            candidates.append({"code":p.code,"name":p.name,"factor_score":round(p.final_score,1),"price":p.price,"change_pct":p.change_pct,"pe":p.pe_ratio,"pb":p.pb_ratio,"turnover_rate":p.turnover_rate,"risk_level":p.risk_level,"risk_flags":p.risk_flags,"daily_source":p.daily_source,"factor_scores":p.factor_scores})
    prompt=("你是谨慎的A股研究助手。根据全市场多因子筛选结果输出中文Markdown。先判断候选是否真的值得买；可以全部观望或回避，禁止为凑数建议买入。逐只说明结论、技术和估值依据、买入触发条件、失效条件和主要风险，最后给出优先级。明确数据局限，不虚构新闻、财报或价格。\n"+f"数据源={result.snapshot_source}，扫描数={result.snapshot_count}，过滤后={result.after_filter_count}，候选={json.dumps(candidates,ensure_ascii=False)}")
    with requests.post(f"{base_url}/responses",headers={"Authorization":f"Bearer {os.environ['LLM_PRIMARY_API_KEY']}","Content-Type":"application/json"},json={"model":model,"input":prompt,"max_output_tokens":1800,"stream":True},timeout=(30,300),stream=True) as response:
        response.raise_for_status()
        text=extract_sse_text(response.iter_lines(decode_unicode=True))
    if not text.strip():
        raise RuntimeError("GPT Responses returned no text")
    return text.strip()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-run",action="store_true")
    args=parser.parse_args()
    os.chdir(ROOT)
    logging.basicConfig(level=logging.INFO)
    from src.core.trading_calendar import is_market_open
    today=datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if not args.force_run and not is_market_open("cn",today):
        print("A-share non-trading day: skipped")
        return
    from src.services.screening.config import Config
    from src.services.screening.pipeline import screen
    config=Config()
    config.fallback_snapshot_path=None
    result=screen("balanced_alpha",max_output=3,use_llm=False,daily_enrich=True,daily_enrich_max_candidates=30,config=config)
    codes=shortlist_codes(result)
    reports=ROOT/"reports"
    reports.mkdir(exist_ok=True)
    (reports/"market_screen.json").write_text(json.dumps(asdict(result),ensure_ascii=False,indent=2),encoding="utf-8")
    lines=[f"# 全市场选股候选 · {today}","","策略：AlphaSift 均衡多因子；候选仍需 GPT 深度分析确认买入条件。",f"数据源：{result.snapshot_source}；扫描 {result.snapshot_count} 只，过滤后 {result.after_filter_count} 只。","","| 代码 | 名称 | 因子评分 | 参考价格 | 风险 |","| --- | --- | --- | --- | --- |"]
    for p in result.picks:
        if p.code in codes:
            lines.append(f"| {p.code} | {p.name} | {p.final_score:.1f} | {p.price:.2f} | {p.risk_level} |")
    if not codes:
        lines.append("\n今日没有通过筛选的候选，不强行推荐。")
    if result.degradation:
        lines += ["","数据质量与降级记录：",*[f"- {x}" for x in result.degradation]]
    report="\n".join(lines)
    if codes:
        report += "\n\n---\n\n# GPT-6 候选复核\n\n"+gpt_review(result,codes)
    (reports/"market_screen.md").write_text(report,encoding="utf-8")
    summary_path=os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path,"a",encoding="utf-8") as handle:
            handle.write(report+"\n")
    from src.notification import NotificationService
    if not NotificationService().send(report):
        raise RuntimeError("Screening report notification failed")

if __name__=="__main__":
    main()
