"""Normalization of incoming leads: the JavaScript of node "Normalize" in workflows/02a_process_lead.json."""
import pytest

from jsrun import load_functions

FNS = load_functions("02a_process_lead.json", "Normalize",
                     ["normalizePhone", "normalizeEmail", "innValid", "canonicalRegion", "splitName", "scoreLead", "normalizeLead"])


@pytest.mark.parametrize("raw, e164", [
    ("8 (916) 123-45-67", "+79161234567"),
    ("+7 916 123 45 67", "+79161234567"),
    ("79161234567", "+79161234567"),
    ("9161234567", "+79161234567"),          # 10 digits without the country code
    ("+7 (921) 555-01-02 доб. 15", "+79215550102"),  # extension is dropped
    ("8-800-555-35-35", "+78005553535"),
    ("+7 812 300-40-50", "+78123004050"),
    ("+380 44 123 4567", "+380441234567"),    # foreign numbers keep their country code
])
def test_phone_to_e164(raw, e164):
    assert FNS.one("normalizePhone", raw) == {"value": e164, "error": None}


@pytest.mark.parametrize("raw", ["123", "12-34", "+7 123 456 78 90", "8 (016) 123-45-67", "phone", "+1"])
def test_invalid_phone_is_reported_not_guessed(raw):
    r = FNS.one("normalizePhone", raw)
    assert r["value"] is None and raw in r["error"]


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_missing_phone_is_not_an_error(raw):
    assert FNS.one("normalizePhone", raw) == {"value": None, "error": None}


@pytest.mark.parametrize("raw, norm", [
    ("Ivan.Petrov@Example.ru", "ivan.petrov@example.ru"),
    ("  anna@example.ru ", "anna@example.ru"),
    ("mailto:Boss@Firma.RU", "boss@firma.ru"),
])
def test_email(raw, norm):
    assert FNS.one("normalizeEmail", raw) == {"value": norm, "error": None}


@pytest.mark.parametrize("raw", ["not-an-email", "a@b", "a@b.c", "two words@x.ru"])
def test_invalid_email(raw):
    assert FNS.one("normalizeEmail", raw)["value"] is None


@pytest.mark.parametrize("raw, canon", [
    ("г. Москва", "москва"), ("Москва", "москва"), ("СПб", "санкт-петербург"),
    ("Санкт-Петербург", "санкт-петербург"), ("Новосибирская обл.", "новосибирская"), (None, None),
])
def test_region_canonical(raw, canon):
    assert FNS.one("canonicalRegion", raw) == canon


def test_split_name():
    assert FNS.call("splitName", ("Иван Петров",), ("Анна",), ("",)) == [
        {"first": "Иван", "last": "Петров"}, {"first": "Анна", "last": None}, {"first": None, "last": None}]


@pytest.mark.parametrize("msg, product, expected", [
    ("Срочно нужна интеграция с 1С, бюджет 300 тыс", None, {"topic": "integration", "urgency": "high", "budget": 300000}),
    ("Хотим IP-АТС на 20 сотрудников", None, {"topic": "telephony", "urgency": "normal", "budget": None}),
    ("Перезвоните сегодня", "crm", {"topic": "crm", "urgency": "high", "budget": None}),
    ("Бюджет 1,5 млн", None, {"topic": "other", "urgency": "normal", "budget": 1500000}),
])
def test_rule_based_score(msg, product, expected):
    r = FNS.one("scoreLead", msg, product)
    assert r["method"] == "rules"
    assert {k: r[k] for k in expected} == expected


def test_lead_with_no_usable_channel_is_rejected():
    r = FNS.one("normalizeLead", {"source": "web", "contact": {"name": "X", "phone": "123", "email": "nope"}})
    assert r["reject_reason"] and len(r["warnings"]) == 2


def test_bad_inn_is_dropped_but_lead_continues():
    r = FNS.one("normalizeLead", {"source": "web", "contact": {"phone": "+79161234567"}, "company": {"inn": "7707083894"}})
    assert r["reject_reason"] is None
    assert r["company"] == {"name": None, "inn": None, "inn_raw": "7707083894", "inn_valid": False}
    assert any("checksum" in w for w in r["warnings"])
