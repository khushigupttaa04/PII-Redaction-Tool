import re

from faker import Faker

from redactor.detectors import brand

fake = Faker("en_IN")
cache = {}     
people = {}    # "first|last" format 


def reset():
    Faker.seed(42)
    cache.clear()
    people.clear()


def person_key(name):
    words = re.findall(r"[a-z]+", name.lower())
    return f"{words[0]}|{words[-1]}"


def keep_format(real):
    prefix = re.match(r"\+\s?\d{1,2}\s?", real)
    keep = prefix.end() if prefix else 0
    return real[:keep] + re.sub(r"\d", lambda _: str(fake.random_digit()), real[keep:])


def fake_email(real):
    parts = re.split(r"[._-]", real.split("@")[0].lower())
    name = people.get(f"{parts[0]}|{parts[-1]}") or f"{fake.first_name()} {fake.last_name()}"
    return re.sub(r"[^a-z.]", "", name.lower().replace(" ", ".")) + "@example.com"


def fake_company(real):
    fake_brand = cache.setdefault(("BRAND", brand(real)), fake.last_name())
    if len(real.split()) == 1:
        return fake_brand
    return f"{fake_brand} {fake.random_element(['Industries', 'Enterprises', 'Holdings', 'Ventures', 'Systems', 'Traders'])} Limited"


GENERATORS = {
    "EMAIL": fake_email,
    "COMPANY": fake_company,
    "ADDRESS": lambda r: fake.address().replace("\n", ", "),
    "URL": lambda r: f"www.{fake.domain_word()}.com",
    "SSN": lambda r: fake.ssn(),
    "CREDIT_CARD": lambda r: fake.credit_card_number(),
    "IP_ADDRESS": lambda r: fake.ipv4_private(),
    "DOB": lambda r: fake.date_of_birth().strftime("%d/%m/%Y"),
    "PAN": lambda r: fake.bothify("???P?####?").upper(),
}                                     # anything else (phones, CIN, DIN, Aadhaar) keep_format


def fake_for(real, pii_type):
    key = person_key(real) if pii_type == "PERSON" else real.lower()
    if (pii_type, key) not in cache:
        if pii_type == "PERSON":
            cache[(pii_type, key)] = people.setdefault(key, f"{fake.first_name()} {fake.last_name()}")
        else:
            cache[(pii_type, key)] = GENERATORS.get(pii_type, keep_format)(real)
    value = cache[(pii_type, key)]
    return value.upper() if real.isupper() else value       # keep ALL CAPS style
