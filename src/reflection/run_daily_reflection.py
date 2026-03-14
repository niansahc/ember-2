from src.reflection.generate_reflection import generate_reflection


def run_daily_reflection():
    return generate_reflection(memory_type="journal", limit=10, store=True)


if __name__ == "__main__":
    result = run_daily_reflection()
    print(result)