from src.reflection.generate_reflection import generate_reflection


def run_weekly_reflection():
    return generate_reflection(
        memory_type="ingested",
        limit=50,
        store=True,
        cadence="weekly",
    )


if __name__ == "__main__":
    result = run_weekly_reflection()
    print(result)