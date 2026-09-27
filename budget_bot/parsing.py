"""Разбор того, что пишет пользователь: суммы, «45 кофе», списки «Netflix 50, Spotify 20», числа месяца."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import MAX_COMMENT_LEN, MAX_NAME_LEN

_NUMBER = re.compile(
    r"(?<![\w.,])"
    r"(?P<sign>[-+−–])?\s?"
    r"(?P<num>"
    r"\d{1,3}(?:[   ]\d{3})+(?:[.,]\d{1,2})?"  # 1 200 / 1 200,50
    r"|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?"  # 1,200 / 1,200.50
    r"|\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?"  # 1.200 / 1.200,50
    r"|\d+(?:[.,]\d{1,2})?"  # 1200 / 12.5 / 12,50
    r")(?!\d)"
    r"(?P<mult>[kк](?![^\W\d_])|\s?тыс(?:\.|яч[аи]?)?(?![^\W\d_]))?",
    re.IGNORECASE,
)
# Что может стоять вплотную к числу, не мешая считать его суммой: «45₪», «45шек», «45р».
_ALLOWED_AFTER = re.compile(r"(?i)(₪|\$|€|£|₽|шек|ш\b|nis|ils|руб|р\b|р\.)")
_CURRENCY_WORDS = re.compile(
    r"(?i)(?<![\w])(₪|\$|€|£|₽|шек\w*|шекел\w*|ш\.?|nis|ils|sh|руб\w*|р\.?|usd|eur|евро|долл\w*)(?![\w])"
)
_PERIODS = (
    ("week", re.compile(r"(?i)(в\s*нед|/\s*нед|неделю|недел|нед\b|в\s*week|/w\b|weekly)")),
    ("month", re.compile(r"(?i)(в\s*мес|/\s*мес|месяц|мес\b|/m\b|monthly)")),
    ("day", re.compile(r"(?i)(в\s*день|/\s*день|ежеднев|каждый\s*день|daily)")),
    ("year", re.compile(r"(?i)(в\s*год|/\s*год|в\s*году|yearly)")),
)
_ZERO_WORDS = re.compile(r"(?i)^\s*(ноль|нет|ничего|не\s+плачу|не\s+трачу|не\s+курю|0)\s*[.!]?\s*$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


@dataclass
class NumberMatch:
    value: float
    start: int
    end: int


def _to_float(num: str) -> float:
    s = num.replace(" ", " ").replace(" ", " ")
    if " " in s:  # 1 200,50
        s = s.replace(" ", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?", s):  # 1,200.50
        s = s.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?", s):  # 1.200,50
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    return float(s)


def find_numbers(text: str) -> list[NumberMatch]:
    """Все суммы в тексте (со знаком и множителем «к»/«тыс»)."""
    found: list[NumberMatch] = []
    for m in _NUMBER.finditer(text):
        tail = text[m.end():]
        if tail and tail[0].isalpha() and not _ALLOWED_AFTER.match(tail):
            continue  # «10bis», «5шт» — это не сумма
        value = _to_float(m.group("num"))
        if m.group("mult"):
            value *= 1000
        if m.group("sign") and m.group("sign") in "-−–":
            value = -value
        found.append(NumberMatch(value, m.start(), m.end()))
    return found


def clean_text(text: str, max_len: int = MAX_COMMENT_LEN) -> str:
    text = _CONTROL.sub(" ", text or "")
    text = " ".join(text.split())
    return text[:max_len].strip()


def _clean_label(text: str, max_len: int) -> str:
    text = _CURRENCY_WORDS.sub(" ", text)
    text = clean_text(text, 10_000).strip(" ,.;:-–—•*·/\\|")
    return text[:max_len].strip()


def detect_period(text: str) -> str | None:
    for period, rx in _PERIODS:
        if rx.search(text):
            return period
    return None


def parse_amount(text: str, allow_negative: bool = False) -> float | None:
    """Сумма из ответа на вопрос анкеты/настроек. «нет»/«ноль» → 0."""
    if _ZERO_WORDS.match(text or ""):
        return 0.0
    nums = find_numbers(text or "")
    if not nums:
        return None
    value = nums[0].value
    if allow_negative and value > 0 and re.search(r"(?i)минус|в\s+минусе", text):
        value = -value
    if value < 0 and not allow_negative:
        value = -value
    if abs(value) > 1e9:
        return None
    return round(value, 2)


def to_monthly(value: float, period: str | None, default: str = "month") -> float:
    period = period or default
    factor = {"day": 30.0, "week": 52 / 12, "month": 1.0, "year": 1 / 12}[period]
    return value * factor


@dataclass
class Entry:
    amount: float
    comment: str
    income: bool


def parse_entry(text: str) -> Entry | None:
    """«45 кофе», «кофе 45₪», «+12000 зарплата» → сумма, комментарий, признак дохода."""
    text = (text or "").strip()
    nums = [n for n in find_numbers(text) if n.value != 0]
    if not nums:
        return None
    first = nums[0]
    amount = abs(first.value)
    if amount > 1e8:
        return None
    income = text.startswith("+")
    comment = _clean_label(text[: first.start] + " " + text[first.end:], MAX_COMMENT_LEN)
    return Entry(round(amount, 2), comment, income)


def parse_items(text: str, default_name: str = "Без названия") -> list[tuple[str, float]]:
    """«Netflix 50, Spotify 20» / «ваад байт 150 страховка 120» → [(название, сумма), …].

    Бросает ValueError с понятным текстом, если в каком-то куске нет суммы.
    """
    items: list[tuple[str, float]] = []
    for part in re.split(r"[\n;]+|,(?=\s*[^\d\s])", text or ""):
        part = part.strip()
        if not part:
            continue
        nums = [n for n in find_numbers(part)]
        if not nums:
            raise ValueError(f"не нашёл сумму в «{clean_text(part, 40)}»")
        if len(nums) == 1:
            n = nums[0]
            name = parse_name(_clean_label(part[: n.start] + " " + part[n.end:], MAX_NAME_LEN))
            items.append((name or default_name, abs(n.value)))
            continue
        before_first = _clean_label(part[: nums[0].start], MAX_NAME_LEN)
        if before_first:  # «название сумма название сумма»
            prev_end = 0
            for n in nums:
                name = parse_name(_clean_label(part[prev_end: n.start], MAX_NAME_LEN))
                items.append((name or default_name, abs(n.value)))
                prev_end = n.end
        else:  # «сумма название сумма название»
            for i, n in enumerate(nums):
                end = nums[i + 1].start if i + 1 < len(nums) else len(part)
                name = parse_name(_clean_label(part[n.end: end], MAX_NAME_LEN))
                items.append((name or default_name, abs(n.value)))
    return items


def parse_day(text: str) -> int | None:
    """Число месяца 1–31: «10», «10-го», «каждое 15 число»."""
    m = re.search(r"(?<!\d)(\d{1,2})(?!\d)", text or "")
    if not m:
        return None
    day = int(m.group(1))
    return day if 1 <= day <= 31 else None


def parse_name(text: str, max_len: int = MAX_NAME_LEN) -> str:
    """Название категории/карты/платежа. Убираем символы, которые Excel в SUMIFS понимает как шаблон
    или оператор (* ? ~ и = в начале), иначе формулы в таблице посчитают не то, а также < > & —
    чтобы название можно было вставлять в HTML-сообщения Telegram как есть."""
    name = re.sub(r"[*?~\"<>&]", "", clean_text(text, 10_000))
    name = name.lstrip("=<>+-@ ").strip(" ,.;:")
    return name[:max_len].strip()
