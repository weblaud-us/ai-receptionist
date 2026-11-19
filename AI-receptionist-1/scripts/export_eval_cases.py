"""
Write the approved eval cases (Phase 7, built from reviewed real calls) to tests/evals/cases/.

    uv run python -m scripts.export_eval_cases            # writes one JSON file per approved case
    uv run python -m scripts.export_eval_cases --check    # lists what would change, writes nothing

Then review the diff and commit it: from now on `RUN_EVALS=1 pytest tests/evals` replays these calls
against the real LLM on every model or prompt change. Cases are redacted when created; read each one
before committing anyway.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from config import settings
from core.log import configure_logging

CASES_DIR = Path(__file__).resolve().parent.parent / "tests" / "evals" / "cases"


def render(case: dict) -> str:
    return json.dumps(case, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


async def main(check: bool) -> None:
    from core.services import reviews
    from database.mongodb import connect_db, disconnect_db, get_db
    from utils.serialize import to_jsonable

    configure_logging(settings.LOG_LEVEL, "text")
    await connect_db(ensure_indexes=False)
    try:
        rows = await get_db()[reviews.CASES].find({"status": "approved"}).to_list(5000)
    finally:
        await disconnect_db()
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    changed = 0
    for row in rows:
        case = to_jsonable(reviews.case_file(row))
        path = CASES_DIR / f"{case['id']}.json"
        text = render(case)
        if path.exists() and path.read_text(encoding="utf-8") == text:
            continue
        changed += 1
        print(("would write " if check else "wrote ") + str(path.relative_to(CASES_DIR.parent.parent.parent)))
        if not check:
            path.write_text(text, encoding="utf-8")
    print(f"{len(rows)} approved case(s), {changed} new or changed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--check", action="store_true", help="list changes without writing")
    asyncio.run(main(parser.parse_args().check))
