
import json
import re
import sys

import docx

from redactor import detectors, docx_io, fakes, images


def replacements(texts, all_spans):
    fakes.reset()
    for text, spans in zip(texts, all_spans):          # people first, so their emails can reuse fake names
        for s, e, t, _ in spans:
            if t == "PERSON":
                fakes.fake_for(text[s:e], t)
    for i, (text, spans) in enumerate(zip(texts, all_spans)):
        for s, e, t, score in reversed(spans):
            yield i, s, e, t, score, text[s:e], fakes.fake_for(text[s:e], t)


def redact_docx(src, dst):
    doc = docx.Document(src)
    paras = docx_io.paragraphs(doc)
    texts = [t for _, t in paras]
    log = []
    for i, s, e, t, score, real, new in replacements(texts, detectors.detect_all(texts)):
        docx_io.replace_in_nodes(paras[i][0], s, e, new)
        log.append({"type": t, "score": round(score, 2), "original": real, "fake": new})
    docx_io.fix_links(doc, {x["original"]: x["fake"] for x in log})
    docx_io.clean_metadata(doc)
    images.redact_images(doc)
    doc.save(dst)
    return log


def redact_text(src, dst):
    lines = open(src, encoding="utf-8").read().split("\n")
    log = []
    for i, s, e, t, score, real, new in replacements(lines, detectors.detect_all(lines)):
        lines[i] = lines[i][:s] + new + lines[i][e:]
        log.append({"type": t, "score": round(score, 2), "original": real, "fake": new})
    open(dst, "w", encoding="utf-8").write("\n".join(lines))
    return log


def redact_file(src, dst):
    return redact_docx(src, dst) if src.lower().endswith(".docx") else redact_text(src, dst)


def leak_check(dst, log):
    """Re-read the output and list every replaced real value that still appears anywhere in it."""
    if dst.lower().endswith(".docx"):
        text = " ".join(t for _, t in docx_io.paragraphs(docx.Document(dst)))
    else:
        text = open(dst, encoding="utf-8").read()
    text = detectors.ALLOW.sub(" ", text.translate(detectors.NORMALIZE))   # protected phrases are not leaks
    real = {x["original"] for x in log}
    return sorted(v for v in real if re.search(rf"\b{re.escape(v)}\b", text, re.I))


if __name__ == "__main__":
    log = redact_file(sys.argv[1], sys.argv[2])
    json.dump(log, open("redaction_log.json", "w"), indent=1, ensure_ascii=False)   # private: holds real values
    print(f"Replaced {len(log)} PII items -> {sys.argv[2]}")
    leaks = leak_check(sys.argv[2], log)
    print(f"Leak check: {len(leaks)} of {len({x['original'] for x in log})} replaced values still present {leaks or ''}")
