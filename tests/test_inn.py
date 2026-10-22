"""INN checksum. Two copies exist in workflows (lead processing and the robot); both are checked against an
independent Python implementation of the algorithm, on known INNs and on generated ones."""
import random

import pytest

from jsrun import load_functions

LEAD = load_functions("02a_process_lead.json", "Normalize", ["innValid"])
ROBOT = load_functions("05a_robot_inn_check.json", "Check INN", ["innValid"], cut_at="const r = $json.r;")

W10 = [2, 4, 10, 3, 5, 9, 4, 6, 8]
W11 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
W12 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]


def control(digits, weights):
    return sum(d * w for d, w in zip(digits, weights)) % 11 % 10


def py_inn_valid(s: str) -> bool:
    if not s.isdigit() or len(s) not in (10, 12):
        return False
    d = [int(c) for c in s]
    if len(s) == 10:
        return control(d, W10) == d[9]
    return control(d, W11) == d[10] and control(d, W12) == d[11]


def make_inn(rng: random.Random, length: int) -> str:
    d = [rng.randint(0, 9) for _ in range(length - (1 if length == 10 else 2))]
    if length == 10:
        d.append(control(d, W10))
    else:
        d.append(control(d, W11))
        d.append(control(d, W12))
    return "".join(map(str, d))


KNOWN = [
    ("7707083893", True),   # ПАО Сбербанк
    ("7736207543", True),   # ООО «Яндекс»
    ("7710140679", True),   # АО «Тинькофф Банк»
    ("500100732259", True),  # individual entrepreneur, 12 digits
    ("7707083894", False),  # last digit changed
    ("500100732258", False),
    ("770708389", False),   # 9 digits
    ("77070838931", False),  # 11 digits
    ("77O7083893", False),  # letter O
    ("", False),
]


@pytest.mark.parametrize("impl", [LEAD, ROBOT], ids=["02a", "05a"])
@pytest.mark.parametrize("inn, valid", KNOWN)
def test_known(impl, inn, valid):
    assert impl.one("innValid", inn) is valid
    assert py_inn_valid(inn) is valid


@pytest.mark.parametrize("impl", [LEAD, ROBOT], ids=["02a", "05a"])
def test_generated_valid_and_corrupted(impl):
    rng = random.Random(2026)
    good = [make_inn(rng, 10) for _ in range(300)] + [make_inn(rng, 12) for _ in range(300)]
    bad = []
    for inn in good:  # change one digit: the checksum must catch it (unless the change keeps it valid by chance)
        i = rng.randrange(len(inn))
        bad.append(inn[:i] + str((int(inn[i]) + rng.randint(1, 9)) % 10) + inn[i + 1:])
    js = impl.call("innValid", *[(x,) for x in good + bad])
    assert all(js[: len(good)])
    assert js[len(good):] == [py_inn_valid(x) for x in bad]
    assert sum(js[len(good):]) < len(bad) * 0.15  # the vast majority of corruptions are detected
