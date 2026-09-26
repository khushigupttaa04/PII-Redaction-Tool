"""Find PII in text with Microsoft Presidio (built-in + our custom recognizers), then apply
document-level rules on top. Every detection is a span (start, end, type, score)."""
import logging
import re
from pathlib import Path

import yaml
from cleanco.termdata import terms_by_type
from presidio_analyzer import AnalyzerEngine, BatchAnalyzerEngine, EntityRecognizer, RecognizerRegistry, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import (CreditCardRecognizer, EmailRecognizer, InAadhaarRecognizer,
                                                      InGstinRecognizer, InPanRecognizer, IpRecognizer, SpacyRecognizer,
                                                      UsSsnRecognizer)

logging.getLogger("presidio-analyzer").setLevel(logging.ERROR)
logging.getLogger("tldextract").setLevel(logging.CRITICAL)     # offline suffix-list warning

CONFIG = yaml.safe_load(open(Path(__file__).parent.parent / "config.yaml"))
ADDRESS_WORDS = set(CONFIG["address_words"])
THRESHOLDS = CONFIG["thresholds"]

# 1-to-1 character swaps, so positions in the cleaned copy still match the original text
NORMALIZE = str.maketrans({"\u00a0": " ", "\t": " ", "\u2013": "-", "\u2014": "-", "\u2212": "-",
                           "\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'"})

# Presidio's type names -> our names
RENAME = {"EMAIL_ADDRESS": "EMAIL", "US_SSN": "SSN", "IN_AADHAAR": "AADHAAR", "IN_PAN": "PAN", "IN_GSTIN": "GSTIN"}

# words just before a match that mean "this number is not PII" (also applied to Presidio's built-ins)
AVOID = {"CREDIT_CARD": r"\b(?:order|ticket|ref|invoice)\b", "SSN": r"\b(?:order|ticket|ref)\b",
         "IP_ADDRESS": r"version|v\.|firmware|release", "AADHAAR": r"\b(?:order|ticket|ref|invoice)\b"}


class RegexRecognizer(EntityRecognizer):
    """Our own rule as a Presidio recognizer: a regex, an optional required hint word nearby
    (context), an optional validator, and an optional capture group for the PII part."""

    def __init__(self, entity, pattern, context=None, validate=None, group=0, score=0.85):
        self.regex, self.hint, self.validate, self.group, self.score = re.compile(pattern), context, validate, group, score
        super().__init__(supported_entities=[entity], name=f"{entity}_{pattern[:20]}")

    def load(self):
        pass

    def analyze(self, text, entities, nlp_artifacts=None):
        results = []
        for m in self.regex.finditer(text):
            if self.hint and not re.search(self.hint, text[max(0, m.start() - 40):m.start()], re.I):
                continue
            if self.validate and not self.validate(m.group(self.group)):
                continue
            results.append(RecognizerResult(self.supported_entities[0], *m.span(self.group), self.score))
        return results


def luhn(number):
    """Credit card checksum: rejects random 16-digit numbers like order IDs."""
    digits = [int(d) for d in re.sub(r"\D", "", number)][::-1]
    total = sum(d if i % 2 == 0 else (d * 2 - 9 if d > 4 else d * 2) for i, d in enumerate(digits))
    return 13 <= len(digits) <= 19 and total % 10 == 0


def is_address(text):
    """Text with 2+ address words (Floor, Road, Marg...) is an address even without a PIN code."""
    return sum(w in ADDRESS_WORDS for w in re.findall(r"[a-z]+", text.lower())) >= 2


MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
DATE = rf"\b\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{2,4}}\b|\b{MONTH} \d{{1,2}},? \d{{4}}\b|\b\d{{1,2}}(?:st|nd|rd|th)? {MONTH},? \d{{4}}\b"
ADDR = "|".join(ADDRESS_WORDS)
PIN = r"(?:\b\d{2}[\dlIO]\s?\d{3}\b|\b[A-Z]{2} \d{5}\b)"          # Indian PIN (typo-tolerant) or US "IL 62704"
# Legal-entity suffixes from many countries (cleanco library), minus 2-letter and plain-English ones
# ("AS" would turn "Ind AS" into a company, "Company" would turn "Our Company" into one), plus config extras.
SUFFIXES = {t.strip() for terms in terms_by_type.values() for t in terms
            if len(t.strip()) > 2 and t.strip() not in ("company", "private", "unlimited", "vat")}
SUFFIXES |= {"private limited", "pvt. ltd", "pvt ltd", "family trust"} | {x.lower() for x in CONFIG["extra_company_suffixes"]}
COMPANY_SUFFIX = "|".join(re.escape(x) for x in sorted(SUFFIXES, key=len, reverse=True))

# Presidio built-ins cover email, credit card (Luhn), IPv4/IPv6, US SSN, Aadhaar (Verhoeff checksum), PAN, GSTIN.
# Our recognizers cover what the built-ins miss or get wrong on this kind of document.
RECOGNIZERS = [
    EmailRecognizer(), CreditCardRecognizer(), IpRecognizer(), UsSsnRecognizer(),
    InAadhaarRecognizer(), InPanRecognizer(), InGstinRecognizer(),
    SpacyRecognizer(supported_entities=["PERSON", "LOCATION"]),
    # Presidio's card patterns miss newer ranges (Mastercard 2-series, 19-digit Visa), so we add any 13-19 digits + Luhn
    RegexRecognizer("CREDIT_CARD", r"\b(?:\d[ -]?){12,18}\d\b", validate=luhn),
    RegexRecognizer("URL", r"(?:https?://|\bwww\.)[\w-]+(?:\.[\w-]+)+(?:/[^\s|,;)\"]*)?"),
    RegexRecognizer("URL", r"\bwww\.[\w-]+(?:\.\s|\s\.)(?:com|in|org|net)\b"),         # link broken by a space
    # emails broken by PDF conversion: "cs@anjalilabtech." (rest on next line), "ipo@iiflca p.com"
    RegexRecognizer("EMAIL", r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.(?=\s|$)"),
    RegexRecognizer("EMAIL", r"[\w.+-]+@[\w.-]+\s[\w-]{1,10}\.(?:com|in|org|net|co)\b"),
    # strict formats count anywhere; loose ones (020..., 1800..., bare mobiles) need a word like "Tel" nearby
    RegexRecognizer("PHONE", r"\+\s?91[\s-]?\d[\d\s-]{7,12}\d|\+1[\s-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}"),
    RegexRecognizer("PHONE", r"(?<![\w.,/-])(?:0\d{2,4}[\s-]?\d{3,4}[\s-]?\d{3,4}|1800[\s-]?\d{3}[\s-]?\d{3,4}|[6-9]\d{9}"
                             r"|\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})(?![\w/-])", context=r"tel|phone|fax|mobile|contact|call"),
    RegexRecognizer("DOB", DATE, context=r"born|birth|dob|d\.o\.b"),                     # other dates are not PII
    RegexRecognizer("CIN", r"\b[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b"),
    RegexRecognizer("DIN", r"\b\d{8}\b", context=r"\bDIN\b"),
    RegexRecognizer("SEBI_REG", r"\bIN[A-Z]\d{9}\b"),
    # company = capitalised words + a legal suffix (written with a capital: "GmbH", "LLC", "Limited")
    RegexRecognizer("COMPANY", rf"\b(?!Private\b|Limited\b)[A-Z][\w&.'-]*(?:\s+(?:[A-Z0-9][\w&.'-]*|and|of|&)){{0,6}}?"
                               rf"\s+(?=[A-Z])(?i:{COMPANY_SUFFIX})(?!\w)"),
    # any ID written after a label: auditor FRN, registration / licence / membership numbers
    RegexRecognizer("REG_ID", r"(?i:\bFRN|registration (?:no|number)|reg\. no|licen[cs]e (?:no|number)|membership (?:no|number))"
                              r"\.?\s*[:.]?\s*(?!IN[A-Z]\d{9}|[LU]\d{5})([A-Z0-9][A-Z0-9/-]{4,})",
                    validate=lambda v: any(ch.isdigit() for ch in v), group=1),
    # address ending in a PIN code; may start with a number ("201, Tower 2") or a building name ("ICICI Venture House")
    RegexRecognizer("ADDRESS", rf"(?:(?<![\w-])(?!(?:19|20)\d\d\b)\d[\w/-]*|(?:(?!Limited\b)[A-Z][\w.]*\s){{0,2}}(?i:{ADDR})\b)"
                               rf"[^;:\n\"]{{0,160}}?[A-Za-z]\s*[-,]?\s*{PIN}(?:,?\s?[A-Z][a-z]+(?: [A-Z][a-z]+)?)?(?:,?\s?India)?"),
    # address without a PIN code, after "Address:" / "office at" / "located at"
    RegexRecognizer("ADDRESS", r"(?i:address|office|located)\s*(?:at|:)\s*([^;\n]{10,200}?)(?=[;\n]|\.\s|$)",
                    validate=is_address, group=1),
]
ENTITIES = sorted({e for r in RECOGNIZERS for e in r.supported_entities})

registry = RecognizerRegistry(supported_languages=["en"])
for recognizer in RECOGNIZERS:
    registry.add_recognizer(recognizer)
nlp_engine = NlpEngineProvider(nlp_configuration={
    "nlp_engine_name": "spacy", "models": [{"lang_code": "en", "model_name": "en_core_web_lg"}]}).create_engine()
analyzer = BatchAnalyzerEngine(AnalyzerEngine(registry=registry, nlp_engine=nlp_engine, supported_languages=["en"]))
nlp = nlp_engine.nlp["en"]                                    # the same spaCy model, reused for the vocabulary check

# a name word: Title Case, ALL CAPS, or an initial ("Kushal", "HEGDE", "M")
NAME_WORD = r"(?:[A-Z][a-z]+|[A-Z]{2,}|[A-Z]\.?)"
NAME_RUN = re.compile(rf"\b{NAME_WORD}(?:\s+{NAME_WORD})+")
ALIAS = re.compile(r'\s*\(\s*"([^"]{2,40})"')
ALLOW = re.compile("|".join(rf"\b{re.escape(a)}\b" for a in CONFIG["allow_list"]), re.I)


def keep_result(text, r, lowercase_words):
    """Filters on top of Presidio: confidence threshold, allow-list, 'not PII' words before a number,
    and for PERSON: 2+ plain words, not a street ("... Marg"), no ordinary lowercase words ("ASBA Account")."""
    value, pii_type = text[r.start:r.end], RENAME.get(r.entity_type, r.entity_type)
    before = text[max(0, r.start - 40):r.start]
    if r.score < THRESHOLDS.get(pii_type, THRESHOLDS["default"]) or ALLOW.search(value):
        return False
    if pii_type in AVOID and re.search(AVOID[pii_type], before, re.I):
        return False
    if pii_type == "PERSON":
        words = value.split()
        next_word = text[r.end:].strip().split(" ")[0].lower()
        return (len(words) >= 2 and re.fullmatch(r"[A-Za-z .'/]+", value) is not None
                and words[-1].lower() not in SUFFIXES                                  # "Muegge GmbH" is a company
                and ADDRESS_WORDS.isdisjoint({words[-1].lower(), next_word})
                and lowercase_words.isdisjoint(value.lower().split()))
    return True


def find_aliases(text, spans):
    """'Care Analytics Private Limited ("CareEdge Research")' -> the quoted alias is the same entity."""
    for start, end, pii_type, score in spans:
        m = ALIAS.match(text, end)
        # keep only distinctive aliases ("CareEdge"), not dictionary words ("Group Entities", "Company")
        if pii_type in ("COMPANY", "PERSON") and m and any(nlp.vocab[w].is_oov for w in m.group(1).split()):
            yield (*m.span(1), pii_type, score)


def name_variants(text, name_words, lowercase_words):
    """spaCy often misses Indian names. Split capitalised runs at ordinary words ("RAJNIKANT M RADADIYA AND
    SANDIPBHAI RADADIYA" is two names), then a part is a name if it has 2+ known name words, or one known
    name word and 2-4 words in total ("Sharadaben Jayantilal Radadiya", surname known)."""
    for run in NAME_RUN.finditer(text):
        parts, part = [], []
        for w in re.finditer(r"\S+", run.group()):
            if len(w.group()) > 1 and w.group().lower() in lowercase_words:
                parts.append(part)
                part = []
            else:
                part.append(w)
        for part in parts + [part]:
            hits = sum(w.group().lower() in name_words for w in part)
            if hits >= 2 or (hits == 1 and 2 <= len(part) <= 4):
                yield (run.start() + part[0].start(), run.start() + part[-1].end(), "PERSON", 0.7)


def expand_name(text, start, end, lowercase_words):
    """spaCy sometimes catches 2 of 3 name words ("Kumar Jain" in "Pawan Kumar Jain"): grow the span over up to
    2 neighbouring name words on each side that are not ordinary words. Punctuation stops it."""
    def is_name(word):
        return re.fullmatch(NAME_WORD, word) and (len(word.rstrip(".")) == 1 or word.lower() not in lowercase_words)
    for _ in range(2):
        m = re.search(r"(\S+) $", text[:start])
        if m and is_name(m.group(1)):
            start = m.start(1)
        m = re.match(r" (\S+)", text[end:])
        if m and is_name(m.group(1)):
            end += m.end(1)
    return start, end


def brand(company):
    return re.sub(r"^the\s+", "", company, flags=re.I).split()[0].lower()


def resolve(spans):
    """Overlapping detections: keep the longer one (an email beats the name inside it)."""
    kept = []
    for s in sorted(spans, key=lambda s: s[0] - s[1]):
        if all(s[1] <= k[0] or s[0] >= k[1] for k in kept):
            kept.append(s)
    return sorted(kept)


def detect_all(texts):
    """Detect PII in all paragraphs of one document.
    Pass 1: Presidio on each paragraph (batched through spaCy). Pass 2: document-level rules re-use
    what pass 1 found anywhere, so a name, company or brand caught once is caught everywhere."""
    texts = [t.translate(NORMALIZE) for t in texts]
    plain = re.sub(r"\S*(?:@|www\.|://)\S*", " ", " ".join(texts))         # emails/links hold names in lowercase
    lowercase_words = set(re.findall(r"\b[a-z]+\b", plain))                  # "account", "branch": ordinary words

    first, places = [], set()
    for text, results in zip(texts, analyzer.analyze_iterator(texts, language="en", entities=ENTITIES, batch_size=64)):
        places |= {text[r.start:r.end].lower() for r in results if r.entity_type == "LOCATION"}   # "India" is not a brand
        spans = resolve([(r.start, r.end, RENAME.get(r.entity_type, r.entity_type), r.score) for r in results
                         if r.entity_type != "LOCATION" and keep_result(text, r, lowercase_words)])
        first.append(resolve(spans + list(find_aliases(text, spans))))

    known = {text[s:e].lower(): t for text, spans in zip(texts, first)
             for s, e, t, _ in spans if t in ("PERSON", "COMPANY") and e - s > 3}
    # the unusual first word of a company ("KSH", "Nuvama") is a brand: redact it on its own too
    brands = {brand(v) for v, t in known.items() if t == "COMPANY"}
    known.update({b: "COMPANY" for b in brands - places if len(b) >= 3 and b not in lowercase_words})
    name_words = {w.lower() for value, t in known.items() if t == "PERSON" for w in re.findall(r"[a-z]{3,}", value)}
    repeat = re.compile("|".join(rf"\b{re.escape(v)}\b" for v in sorted(known, key=len, reverse=True)), re.I)

    result = []
    for text, spans in zip(texts, first):
        keep = [(m.start(), m.end(), "KEEP", 1.0) for m in ALLOW.finditer(text)]       # allow-listed phrases win
        repeats = [(m.start(), m.end(), known[m.group().lower()], 0.7) for m in repeat.finditer(text)] if known else []
        found = spans + repeats + list(name_variants(text, name_words, lowercase_words))
        found = [(*expand_name(text, s, e, lowercase_words), t, sc) if t == "PERSON" else (s, e, t, sc) for s, e, t, sc in found]
        spans = resolve(keep + found)
        result.append([s for s in spans if s[2] != "KEEP"])
    return result
