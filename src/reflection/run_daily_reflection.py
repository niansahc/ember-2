from src.reflection.generate_reflection import generate_reflection


def run_daily_reflection():
    return generate_reflection(
        memory_type="ingested",
        limit=20,
        store=True,
        cadence="daily",
    )


if __name__ == "__main__":
    result = run_daily_reflection()
    print(result)