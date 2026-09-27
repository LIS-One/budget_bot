from datetime import date, datetime, timedelta

from budget_bot.registry import Registry

NOW = datetime(2026, 9, 27, 12, 0)


def test_invites_and_pairs(tmp_path):
    reg = Registry(tmp_path / "r.json")
    reg.upsert(1, name="Аня", chat_id=1)
    reg.upsert(2, name="Боря", chat_id=2)
    code = reg.create_invite(1, NOW)
    assert len(code) == 6 and reg.invite_owner(code, NOW) == 1
    assert reg.invite_owner(code.lower(), NOW) == 1
    assert reg.invite_owner(code, NOW + timedelta(days=8)) is None  # устарел
    new_code = reg.create_invite(1, NOW)
    assert reg.invite_owner(code, NOW) is None and reg.invite_owner(new_code, NOW) == 1
    reg.link(1, 2)
    assert reg.partner(1) == 2 and reg.partner(2) == 1
    assert reg.invite_owner(new_code, NOW) is None  # после связи приглашения сгорают
    # данные переживают перезапуск
    again = Registry(tmp_path / "r.json")
    assert again.partner(2) == 1 and again.name(1) == "Аня"
    assert again.unlink(2) == 1
    assert again.partner(1) is None and again.partner(2) is None


def test_one_sided_link_is_ignored(tmp_path):
    reg = Registry(tmp_path / "r.json")
    reg.upsert(1, partner_id=2)
    reg.upsert(2, partner_id=None)
    assert reg.partner(1) is None


def test_last_processed_and_remove(tmp_path):
    reg = Registry(tmp_path / "r.json")
    reg.set_last_processed(1, date(2026, 9, 27))
    assert reg.last_processed(1) == date(2026, 9, 27)
    reg.upsert(2)
    reg.link(1, 2)
    reg.remove(1)
    assert reg.user(1) is None and reg.partner(2) is None
