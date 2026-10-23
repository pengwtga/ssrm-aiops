"""评测脚本（命令行版）

用法：
    python -m app.eval [limit]                    # 常规集（data/incidents.jsonl），LLM 通道
    python -m app.eval 30 --rule                  # 强制规则基线
    python -m app.eval 30 --hard                  # 困难集（data/incidents_hard.jsonl）
    python -m app.eval 30 --hard --rule           # 困难集 + 规则基线（对照组）
"""

import asyncio
import sys
import time
from collections import Counter
from pathlib import Path

from app.classifier import RootCauseClassifier
from app.dataset import load_dataset
from app.llm_client import llm_client

DATA_DIR = Path(__file__).parent.parent / "data"


async def main(limit: int, use_rule: bool, hard: bool):
    path = DATA_DIR / "incidents_hard.jsonl" if hard else DATA_DIR / "incidents.jsonl"
    classifier = RootCauseClassifier(llm_client)

    async def classify(row):
        if use_rule:
            result = classifier._rule_baseline(row["incident"])
            result["source"] = "rule_forced"
            return result
        return await classifier.classify(row["incident"])

    rows = load_dataset(limit=limit, shuffle=True, path=path)
    correct, latencies, sources = 0, [], Counter()
    confusions = []

    for row in rows:
        t0 = time.monotonic()
        result = await classify(row)
        latencies.append((time.monotonic() - t0) * 1000)
        sources[result.get("source", "?")] += 1
        pred = result["root_cause_class"]
        label = row["label"]
        if pred == label:
            correct += 1
        else:
            confusions.append((label, pred, row["incident"].get("incident_id", "?")))

    total = len(rows)
    tag = "困难集" if hard else "常规集"
    channel = "规则基线(强制)" if use_rule else "LLM 容灾链"
    print(f"\n===== SSRM 根因分类评测 [{tag} | {channel}] =====")
    print(f"样本数: {total} | 通道: {dict(sources)}")
    print(f"总体准确率: {correct}/{total} = {correct / total:.1%}")
    print(f"平均延迟: {sum(latencies) / len(latencies):.0f} ms")
    if confusions:
        print("混淆样例（label → predicted）:")
        for label, pred, iid in confusions[:8]:
            print(f"  {iid}: {label} → {pred}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    limit = int(args[0]) if args else 30
    use_rule = "--rule" in sys.argv
    hard = "--hard" in sys.argv
    asyncio.run(main(limit, use_rule, hard))
