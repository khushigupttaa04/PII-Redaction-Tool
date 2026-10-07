import re

from docx.oxml.ns import qn

W_T, W_TAB, W_BR, W_R, W_P = qn("w:t"), qn("w:tab"), qn("w:br"), qn("w:r"), qn("w:p")


def node_text(n):
    return (n.text or "") if n.tag == W_T else " "     # a tab / line break reads as a space


def paragraphs(doc):
    roots = [doc.element.body] + [part._element for s in doc.sections for part in (s.header, s.footer)]
    result = []
    for root in dict.fromkeys(roots):                    # linked headers repeat; read each once
        for p in root.iter(W_P):
            nodes = [n for n in p.iter(W_T, W_TAB, W_BR)
                     if next(n.iterancestors(W_P)) is p   # skip text of a nested text-box paragraph
                     and (n.tag == W_T or n.getparent().tag == W_R)]
            text = "".join(node_text(n) for n in nodes)
            if text.strip():
                result.append((nodes, text))
    return result


def replace_in_nodes(nodes, start, end, new):

    pos, placed = 0, False
    for n in nodes:
        text = node_text(n)
        s, e = pos, pos + len(text)
        pos = e
        if e <= start or s >= end or n.tag != W_T:
            continue
        n.text = text[:max(start - s, 0)] + ("" if placed else new) + (text[end - s:] if end < e else "")
        n.set(qn("xml:space"), "preserve")
        placed = True


def fix_links(doc, replaced):
    for rel in doc.part.rels.values():
        if rel.is_external:
            for real, new in replaced.items():
                rel._target = re.sub(re.escape(real), new, rel.target_ref, flags=re.I)


def clean_metadata(doc):
    props = doc.core_properties
    props.author = props.last_modified_by = props.title = props.subject = props.keywords = props.comments = ""
    for part in doc.part.package.parts:
        if part.partname == "/docProps/app.xml":
            part._blob = re.sub(rb"<Company>.*?</Company>", b"<Company></Company>", part.blob)
