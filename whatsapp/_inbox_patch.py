    session_id = session["session_id"]

    # ── Detect conversation label from message intent ─────────────────────────
    body_lower = (message_body or "").lower()
    if any(k in body_lower for k in ["book", "reserv", "room", "suite", "check-in", "checkin"]):
        label = "Booking"
    elif any(k in body_lower for k in ["cancel", "refund", "cancellation"]):
        label = "Cancellation"
    elif any(k in body_lower for k in ["info", "price", "cost", "avail", "?", "how", "what", "when"]):
        label = "Info"
    else:
        label = session.get("label")

    # ── Update inbox metadata (increment unread for manager view) ─────────────
    await db.conversations.update_one(
        {"session_id": session_id},
        {
            "$set": {
                "last_message_preview": (message_body or "")[:80],
                "guest_name": profile_name or session.get("context", {}).get("guest_name"),
                "updated_at": datetime.utcnow(),
                **({"label": label} if label else {}),
            },
            "$inc": {"unread_count": 1},  # ← fixed: was incorrectly 0
        },
    )
