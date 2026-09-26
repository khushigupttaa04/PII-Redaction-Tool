"""Held-out benchmark on 5 unseen prospectuses (none used for tuning).
Usage: python -m eval.benchmark <folder with the .docx files>"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import docx

from eval.evaluate import gold_spans, score, table
from redactor import detectors, docx_io

gold = json.load(open("eval/gold_benchmark.json", encoding="utf-8"))
pooled, pooled_tokens, report = defaultdict(Counter), Counter(), []
redacted = Counter()        # type-agnostic: was the PII replaced at all, whatever type it was given?
for name, samples in gold.items():
    texts = [t for _, t in docx_io.paragraphs(docx.Document(Path(sys.argv[1]) / f"{name}.docx"))]
    preds = dict(zip(reversed(texts), reversed(detectors.detect_all(texts))))     # full document, both passes
    s_texts = [g["text"] for g in samples]
    s_gold, s_pred = [gold_spans(g["text"], g["pii"]) for g in samples], [preds[t] for t in s_texts]
    stats, tokens, errors = score(s_texts, s_gold, s_pred)
    for gold_list, pred_list in zip(s_gold, s_pred):
        redacted["gold_hit"] += sum(any(p[0] < g[1] and g[0] < p[1] for p in pred_list) for g in gold_list)
        redacted["gold"] += len(gold_list)
        redacted["pred_hit"] += sum(any(p[0] < g[1] and g[0] < p[1] for g in gold_list) for p in pred_list)
        redacted["pred"] += len(pred_list)
    for t, c in stats.items():
        pooled[t].update(c)
    pooled_tokens.update(tokens)
    report += [table(f"{name} ({len(samples)} paragraphs)", stats, tokens)] + [f"- {e}" for e in errors] + [""]
report.insert(0, table("All 5 unseen prospectuses pooled (100 paragraphs)", pooled, pooled_tokens) +
              f"Redaction-level (type ignored): precision {redacted['pred_hit'] / redacted['pred']:.1%}, "
              f"recall {redacted['gold_hit'] / redacted['gold']:.1%}\n")
print("\n".join(report))
open("eval/benchmark_results.md", "w", encoding="utf-8").write("\n".join(report))
