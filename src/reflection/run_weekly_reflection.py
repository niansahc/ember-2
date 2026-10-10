# Process entrypoint: load .env before any src import (see load_env_file).
if __name__ == "__main__":
    from src.core.config import load_env_file

    load_env_file()

from src.reflection.generate_reflection import generate_reflection


def run_weekly_reflection():
    return generate_reflection(
        memory_types=["journal", "ingested"],
        limit=50,
        store=True,
        cadence="weekly",
    )


if __name__ == "__main__":
    result = run_weekly_reflection()
    print(result)