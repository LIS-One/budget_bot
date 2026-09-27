"""Модель данных пользователя. Всё это хранится в личной Excel-таблице."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# Способ оплаты «не кредиткой»: дебет, Bit/PayBox, перевод, наличные — всё, что уходит со счёта сразу.
ACCOUNT = "Счёт"

# Типы операций (так же пишутся в колонку «Тип» листа «Операции»).
EXPENSE = "Расход"
INCOME = "Доход"
CARD_CHARGE = "Списание карты"
KINDS = (EXPENSE, INCOME, CARD_CHARGE)

# Служебные категории операций.
FIXED_CATEGORY = "Постоянные"
INCOME_CATEGORY = "Доход"
CARD_CHARGE_CATEGORY = "Списание карты"
SPECIAL_CATEGORIES = (FIXED_CATEGORY, INCOME_CATEGORY, CARD_CHARGE_CATEGORY)

# Откуда появилась операция.
SOURCE_BOT = "бот"
SOURCE_AUTO = "авто"
SOURCE_EXCEL = "Excel"

MAX_NAME_LEN = 40
MAX_COMMENT_LEN = 200


@dataclass
class Category:
    name: str
    emoji: str = "🔹"
    limit: float = 0.0
    keywords: list[str] = field(default_factory=list)
    code: str = ""  # код стандартной категории (groceries, cafe, …) или "" для своей

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.name}".strip()


@dataclass
class FixedExpense:
    name: str
    amount: float
    day: int = 1
    payment: str = ACCOUNT
    active: bool = True


@dataclass
class Card:
    name: str
    charge_day: int
    baseline: float = 0.0  # сумма к списанию на момент сверки
    baseline_at: datetime = field(default_factory=datetime.now)


@dataclass
class Transaction:
    id: int
    at: datetime
    kind: str
    amount: float
    category: str
    comment: str = ""
    payment: str = ACCOUNT
    source: str = SOURCE_BOT


@dataclass
class Profile:
    name: str = ""
    currency: str = "₪"
    income: float = 0.0
    payday: int | None = None
    savings_goal: float = 0.0
    balance: float = 0.0  # остаток на счёте на момент сверки
    balance_at: datetime = field(default_factory=datetime.now)
    created_at: datetime = field(default_factory=datetime.now)
    notes: str = ""


@dataclass
class SurveyItem:
    question: str
    answer: str
    monthly: float | None = None


@dataclass
class UserData:
    user_id: int
    profile: Profile
    categories: list[Category] = field(default_factory=list)
    fixed: list[FixedExpense] = field(default_factory=list)
    cards: list[Card] = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)
    survey: list[SurveyItem] = field(default_factory=list)

    # --- поиск -------------------------------------------------------------
    def category(self, name: str) -> Category | None:
        low = name.strip().lower()
        return next((c for c in self.categories if c.name.lower() == low), None)

    def card(self, name: str) -> Card | None:
        low = name.strip().lower()
        return next((c for c in self.cards if c.name.lower() == low), None)

    def transaction(self, tx_id: int) -> Transaction | None:
        return next((t for t in self.transactions if t.id == tx_id), None)

    def category_label(self, name: str) -> str:
        cat = self.category(name)
        if cat:
            return cat.label
        if name == FIXED_CATEGORY:
            return "📌 Постоянные"
        if name == INCOME_CATEGORY:
            return "💼 Доход"
        if name == CARD_CHARGE_CATEGORY:
            return "💳 Списание карты"
        return f"▫️ {name}"

    # --- производные величины ---------------------------------------------
    @property
    def payment_methods(self) -> list[str]:
        return [c.name for c in self.cards] + [ACCOUNT]

    @property
    def default_payment(self) -> str:
        return self.cards[0].name if self.cards else ACCOUNT

    @property
    def fixed_total(self) -> float:
        return sum(f.amount for f in self.fixed if f.active)

    @property
    def limits_total(self) -> float:
        return sum(c.limit for c in self.categories)

    @property
    def planned_savings(self) -> float:
        """Сколько останется по плану: доход − постоянные − лимиты категорий."""
        return self.profile.income - self.fixed_total - self.limits_total

    def next_tx_id(self) -> int:
        return max((t.id for t in self.transactions), default=0) + 1

    def add_transaction(self, **kwargs) -> Transaction:
        tx = Transaction(id=self.next_tx_id(), **kwargs)
        self.transactions.append(tx)
        return tx

    def remove_transaction(self, tx_id: int) -> Transaction | None:
        tx = self.transaction(tx_id)
        if tx:
            self.transactions.remove(tx)
        return tx
