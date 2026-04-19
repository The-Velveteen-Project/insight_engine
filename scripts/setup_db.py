"""Initialize the SQLite database and create all tables."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db.session import init_db


async def main() -> None:
    await init_db()
    print("Database initialized.")


if __name__ == "__main__":
    asyncio.run(main())
