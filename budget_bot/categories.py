"""Стандартные категории бюджета и автоопределение категории по описанию траты."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from .models import EXPENSE, Category, Transaction

NEED, WANT, BUFFER = "need", "want", "buffer"


@dataclass(frozen=True)
class CategoryDef:
    code: str
    emoji: str
    name: str
    group: str  # need — обязательное, want — желания, buffer — резерв
    max_cut: float  # какую долю оценки реально срезать без боли
    keywords: tuple[str, ...]
    presets: tuple[int, ...] = ()  # быстрые суммы в месяц (в шекелях) для анкеты


_DEFS: list[CategoryDef] = [
    CategoryDef("groceries", "🛒", "Продукты", NEED, 0.10, (
        "продукт", "супер", "супермаркет", "магазин", "шуферсаль", "шуфер", "shufersal", "рами леви",
        "rami levy", "виктори", "victory", "ошер ад", "osher ad", "йохананоф", "yochananof", "мега",
        "tiv taam", "тив таам", "ампм", "am pm", "хаци хинам", "hazi hinam", "carrefour", "карфур",
        "хлеб", "молоко", "овощ", "фрукт", "мясо", "курица", "яйца", "крупа", "макарон", "рынок", "шук",
        "виноград", "клубник", "сыр",
        "סופר", "שופרסל", "רמי לוי", "מכולת", "ויקטורי", "יוחננוף", "אושר עד",
    )),
    CategoryDef("cafe", "☕", "Кафе и перекусы", WANT, 0.50, (
        "кофе", "кафе", "капуч", "латте", "эспрессо", "американо", "круассан", "перекус", "шаурм",
        "шаверм", "фалафел", "бурекас", "сэндвич", "сендвич", "булочк", "выпечк", "пончик", "морожен",
        "пицц", "бургер", "хот дог", "cafe", "coffee", "aroma", "арома", "cofix", "кофикс", "landwer",
        "ландвер", "greg", "грег", "starbucks", "mcdonalds", "макдональдс", "макдак", "бк", "קפה", "ארומה",
    )),
    CategoryDef("restaurants", "🍽", "Рестораны и бары", WANT, 0.50, (
        "ресторан", "бар", "бары", "баре", "паб", "пиво", "пивко", "коктейл", "вино", "ужин", "суши",
        "стейк", "pub", "restaurant", "мисада", "מסעדה", "בר",
    )),
    CategoryDef("delivery", "🛵", "Доставка еды", WANT, 0.60, (
        "wolt", "вольт", "10bis", "тенбис", "тен бис", "cibus", "сибус", "доставк", "mishloha",
        "мишлоха", "заказ еды", "משלוח", "וולט",
    )),
    CategoryDef("transport", "🚌", "Транспорт", NEED, 0.05, (
        "такси", "gett", "гет", "гетт", "yango", "янго", "uber", "убер", "автобус", "поезд", "rav kav",
        "равкав", "рав кав", "moovit", "мувит", "метро", "трамвай", "бензин", "заправк", "топливо",
        "паз", "paz", "delek", "делек", "sonol", "сонол", "дор алон", "парковк", "стоянк", "pango",
        "панго", "cellopark", "самокат", "каршеринг", "автотель", "autotel", "шерут", "маршрутк",
        "מונית", "דלק", "חניה", "רב קו", "אוטובוס", "רכבת",
    )),
    CategoryDef("health", "💊", "Здоровье и аптека", NEED, 0.0, (
        "аптек", "суперфарм", "super pharm", "superpharm", "be pharm", "би фарм", "лекарств", "таблетк",
        "врач", "доктор", "стоматолог", "зубн", "анализ", "линз", "очки", "маккаби", "maccabi", "клалит",
        "clalit", "меухедет", "леумит", "психолог", "терапевт", "витамин", "בית מרקחת", "סופר פארם",
    ), (50, 100, 200)),
    CategoryDef("clothes", "👕", "Одежда и обувь", WANT, 0.40, (
        "одежд", "обув", "кроссовк", "кросы", "футболк", "джинс", "куртк", "платье", "штаны", "носки",
        "белье", "zara", "зара", "h m", "castro", "кастро", "fox", "фокс", "terminal x", "терминал икс",
        "pull bear", "bershka", "бершка", "adidas", "адидас", "nike", "найк", "renuar", "בגדים", "נעליים",
    ), (100, 200, 400)),
    CategoryDef("hobby", "🎨", "Хобби", WANT, 0.30, (
        "хобби", "гитар", "струн", "краск", "холст", "кисти", "рисован", "фото", "пленк", "музык",
        "синтезатор", "настолк", "лего", "lego", "пряж", "вязан", "рукодел", "книг",
    ), (100, 250, 500)),
    CategoryDef("tobacco", "🚬", "Сигареты и вейп", WANT, 0.25, (
        "сигарет", "сигар", "табак", "вейп", "vape", "iqos", "айкос", "стики", "heets", "хитс",
        "кальян", "сиги", "сижк", "одноразк", "elf bar", "эльф бар", "סיגריות", "טבק",
    )),
    CategoryDef("games", "🎮", "Игры", WANT, 0.50, (
        "steam", "стим", "playstation", "плейстейшн", "ps plus", "ps5", "ps4", "psn", "xbox", "иксбокс",
        "nintendo", "нинтендо", "игра", "игры", "игру", "донат", "робукс", "robux", "genshin", "геншин",
        "epic games", "game pass", "battle pass", "баттл пасс", "скин",
    ), (50, 100, 200, 400)),
    CategoryDef("partner", "❤️", "Вторая половинка", WANT, 0.25, (
        "свидани", "цветы", "букет", "девушк", "парню", "парня", "подарок девушк", "подарок парн",
        "романтик", "валентин", "годовщин", "с любим", "жене", "мужу",
    ), (200, 400, 700, 1000)),
    CategoryDef("impulse", "🛍", "Спонтанные покупки", WANT, 0.60, (
        "aliexpress", "алиэкспресс", "али", "ali", "temu", "тему", "shein", "шейн", "amazon", "амазон",
        "iherb", "айхерб", "сладост", "шоколад", "чипсы", "конфет", "мелоч", "безделуш", "импульс",
        "спонтан", "вкусняш", "снеки", "снэк",
    )),
    CategoryDef("unexpected", "⚡", "Непредвиденное", BUFFER, 0.0, (
        "ремонт", "штраф", "поломк", "сломал", "эвакуатор", "починк", "непредвиден", "внезапн",
        "сантехник", "электрик",
    )),
    CategoryDef("beauty", "💅", "Уход и красота", WANT, 0.30, (
        "парикмах", "стрижк", "барбер", "маникюр", "педикюр", "косметик", "шампун", "крем", "салон",
        "брови", "ресниц", "депиляц", "эпиляц", "духи", "парфюм", "макияж",
    ), (50, 150, 300)),
    CategoryDef("sport", "🏋️", "Спорт", WANT, 0.20, (
        "спортзал", "зал", "фитнес", "тренировк", "тренер", "бассейн", "йога", "пилатес", "кроссфит",
        "holmes place", "холмс", "бокс", "протеин", "спорт", "gym", "джим",
    ), (100, 200, 350)),
    CategoryDef("pet", "🐾", "Питомец", NEED, 0.05, (
        "корм", "ветеринар", "ветклиник", "вет", "наполнител", "зоомагазин", "кошк", "кот", "собак",
        "пес", "питомц", "груминг", "petshop",
    ), (100, 250, 400)),
    CategoryDef("study", "📚", "Учёба", NEED, 0.0, (
        "учеб", "курс", "универ", "колледж", "семинар", "тетрад", "канцеляр", "принтер", "распечат",
        "экзамен", "репетитор", "психометр", "ульпан", "udemy", "coursera",
    ), (100, 300, 600)),
    CategoryDef("gifts", "🎁", "Подарки", WANT, 0.30, (
        "подар", "день рождения", "др", "праздник", "сувенир", "открытк",
    ), (100, 200, 400)),
    CategoryDef("travel", "✈️", "Поездки", WANT, 0.40, (
        "отель", "гостиниц", "хостел", "авиабилет", "самолет", "перелет", "booking", "букинг",
        "airbnb", "эйрбнб", "поездк", "отпуск", "путешеств", "цимер", "эйлат",
    ), (200, 500, 1000)),
    CategoryDef("party", "🍻", "Вечеринки и алкоголь", WANT, 0.50, (
        "вечеринк", "тусовк", "туса", "клуб", "алкогол", "алко", "водк", "виски", "текил", "шампанск",
        "бутылк",
    ), (100, 250, 500)),
    CategoryDef("fun", "🎬", "Развлечения", WANT, 0.40, (
        "кино", "концерт", "театр", "стендап", "боулинг", "квест", "караоке", "музей", "выставк",
        "аттракцион", "билет", "yes planet", "cinema city", "синема сити", "фестивал",
    ), (100, 200, 400)),
    CategoryDef("tech", "📱", "Техника", WANT, 0.50, (
        "наушник", "телефон", "смартфон", "зарядк", "кабель", "ноутбук", "компьютер", "клавиатур",
        "мышк", "планшет", "ipad", "iphone", "айфон", "samsung", "самсунг", "ksp", "ivory", "айвори",
        "электроник", "гаджет", "чехол",
    ), (100, 300, 600)),
    CategoryDef("home", "🪴", "Для дома", NEED, 0.30, (
        "для дома", "посуд", "ikea", "икеа", "икея", "эйс", "хозтовар", "моющ", "бытов", "мебел",
        "постельн", "полотенц", "max stock", "макс сток", "home center", "хоум центр", "лампочк",
        "декор", "уборк", "чистящ",
    ), (100, 200, 400)),
    CategoryDef("other", "📦", "Прочее", WANT, 0.30, ()),
]

DEFAULTS: dict[str, CategoryDef] = {d.code: d for d in _DEFS}

# Категории, которые предлагаются мультивыбором «На что ещё тратишься часто?»
EXTRA_CODES: tuple[str, ...] = (
    "beauty", "sport", "pet", "study", "gifts", "travel", "party", "fun", "tech", "home",
)

# «Сильные» слова: канал покупки важнее, чем что именно купили («пицца wolt» — это доставка).
STRONG_KEYWORDS = frozenset({"wolt", "вольт", "10bis", "тенбис", "тен бис", "cibus", "сибус", "mishloha", "мишлоха"})

INCOME_WORDS = ("зарплат", "зп", "стипенд", "аванс", "доход", "премия", "кэшбэк", "кешбэк", "кешбек")

CUSTOM_EMOJI = "🔹"

# Короткие имена для кнопок (полные названия не влезают в две кнопки в ряд).
SHORT_NAMES = {
    "groceries": "Продукты", "cafe": "Кафе", "restaurants": "Рестораны", "delivery": "Доставка",
    "transport": "Транспорт", "health": "Здоровье", "clothes": "Одежда", "hobby": "Хобби",
    "tobacco": "Сигареты", "games": "Игры", "partner": "Половинка", "impulse": "Спонтанные",
    "unexpected": "Резерв", "beauty": "Уход", "sport": "Спорт", "pet": "Питомец", "study": "Учёба",
    "gifts": "Подарки", "travel": "Поездки", "party": "Вечеринки", "fun": "Развлечения",
    "tech": "Техника", "home": "Дом", "other": "Прочее",
}


def short_label(emoji: str, name: str, code: str = "", max_len: int = 12) -> str:
    d = DEFAULTS.get(code)
    short = SHORT_NAMES[code] if d and d.name == name else name
    if len(short) > max_len:
        short = short[: max_len - 1].rstrip() + "…"
    return f"{emoji} {short}"

_EMOJI_PREFIX = re.compile(
    r"^\s*([\U0001F300-\U0001FAFF☀-➿⬀-⯿][️‍\U0001F300-\U0001FAFF"
    r"☀-➿\U0001F3FB-\U0001F3FF]*)\s*"
)


def split_emoji(text: str) -> tuple[str | None, str]:
    """«🎸 Гитара» → («🎸», «Гитара»)."""
    m = _EMOJI_PREFIX.match(text)
    if not m:
        return None, text.strip()
    return m.group(1), text[m.end():].strip()


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[\W_]+", " ", text)
    return " ".join(text.split())


def make_category(code: str, limit: float = 0.0) -> Category:
    d = DEFAULTS[code]
    return Category(name=d.name, emoji=d.emoji, limit=limit, keywords=list(d.keywords), code=code)


def definition(cat: Category) -> CategoryDef | None:
    return DEFAULTS.get(cat.code)


def max_cut(cat: Category) -> float:
    d = definition(cat)
    return d.max_cut if d else 0.3


def group(cat: Category) -> str:
    d = definition(cat)
    return d.group if d else WANT


def looks_like_income(comment: str) -> bool:
    tokens = normalize(comment).split()
    return any(t.startswith(w) if len(w) >= 4 else t == w for t in tokens for w in INCOME_WORDS)


def detect_category(
    text: str,
    categories: Sequence[Category],
    transactions: Iterable[Transaction] = (),
) -> Category | None:
    """Категория для описания траты: сначала из истории пользователя, потом по ключевым словам."""
    norm = normalize(text)
    if not norm or not categories:
        return None

    by_name = {c.name.lower(): c for c in categories}
    history = sorted(
        (t for t in transactions if t.kind == EXPENSE and t.comment),
        key=lambda t: (t.at, t.id),
        reverse=True,
    )
    for tx in history[:1000]:
        if normalize(tx.comment) == norm and tx.category.lower() in by_name:
            return by_name[tx.category.lower()]

    tokens = norm.split()
    padded = f" {norm} "
    best: Category | None = None
    best_score = 0
    for cat in categories:
        for raw in cat.keywords:
            kw = normalize(raw)
            if not kw:
                continue
            if " " in kw:
                hit = f" {kw}" in padded
            elif len(kw) >= 4:
                hit = any(t.startswith(kw) for t in tokens)
            else:
                hit = kw in tokens
            if hit:
                # своя категория пользователя при равенстве важнее стандартной
                score = len(kw) + (100 if kw in STRONG_KEYWORDS else 0) + (0.5 if not cat.code else 0)
                if score > best_score:
                    best, best_score = cat, score
    return best
