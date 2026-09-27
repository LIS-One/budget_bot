import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from budget_bot.app import build_application  # noqa: E402
from budget_bot.config import Config  # noqa: E402
from tg_sim import FakeTelegram, Sim  # noqa: E402


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(
        bot_token="123456:TEST-TOKEN",
        data_dir=tmp_path / "data",
        timezone=ZoneInfo("Asia/Jerusalem"),
        currency="₪",
        summary_weekday=0,
        summary_time=time(9, 0),
        daily_time=time(8, 0),
        allowed_user_ids=frozenset(),
    )


@pytest.fixture
async def sim(cfg):
    fake = FakeTelegram()
    state = Sim(None, fake, datetime(2026, 9, 27, 14, 30))
    app = build_application(cfg, clock=state.clock, request=fake, persistence=False)
    state.app = app
    await app.initialize()
    try:
        yield state
    finally:
        await app.shutdown()
