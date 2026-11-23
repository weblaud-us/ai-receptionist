"""scripts/upgrade_voices.py: practices on their country's stock Aura voice move to Cartesia; a
practice that chose its own voice (or another provider) is left alone."""

from scripts.upgrade_voices import needs_upgrade, upgrade


def _tenant(_id, country, provider, voice_id, **extra):
    return {"_id": _id, "name": _id, "slug": _id, "country": country,
            "agent": {"voice_provider": provider, "voice_id": voice_id}, **extra}


def test_needs_upgrade_only_for_the_stock_voice():
    assert needs_upgrade(_tenant("a", "AU", "deepgram", "aura-2-theia-en"))
    assert needs_upgrade(_tenant("b", "GB", "deepgram", ""))
    assert not needs_upgrade(_tenant("c", "AU", "deepgram", "aura-2-hyperion-en"))   # picked by the practice
    assert not needs_upgrade(_tenant("d", "AU", "elevenlabs", ""))
    assert not needs_upgrade(_tenant("e", "US", "deepgram", "aura-2-andromeda-en"))  # no local default


async def test_dry_run_changes_nothing_and_apply_moves_only_stock_voices(db):
    await db.tenants.insert_many([
        _tenant("ten_stock_au", "AU", "deepgram", "aura-2-theia-en"),
        _tenant("ten_stock_gb", "GB", "deepgram", "aura-2-pandora-en"),
        _tenant("ten_chosen", "AU", "deepgram", "aura-2-hyperion-en"),
        _tenant("ten_deleted", "AU", "deepgram", "aura-2-theia-en", billing={"deleted_at": "2026-01-01"}),
    ])
    planned = await upgrade(db, apply=False)
    assert {m["id"] for m in planned} == {"ten_stock_au", "ten_stock_gb"}
    assert (await db.tenants.find_one({"_id": "ten_stock_au"}))["agent"]["voice_provider"] == "deepgram"

    await upgrade(db, apply=True)
    au = await db.tenants.find_one({"_id": "ten_stock_au"})
    assert au["agent"] == {"voice_provider": "cartesia", "voice_id": ""}
    assert (await db.tenants.find_one({"_id": "ten_chosen"}))["agent"]["voice_id"] == "aura-2-hyperion-en"
    assert (await db.tenants.find_one({"_id": "ten_deleted"}))["agent"]["voice_provider"] == "deepgram"
    assert await upgrade(db, apply=False) == []          # idempotent
