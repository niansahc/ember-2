import re


def clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[^\x00-\x7F]+", " ", text)
    text = re.sub(r"\s+", " ", text)

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    cleaned_lines = []

    for line in lines:
        digit_ratio = sum(c.isdigit() for c in line) / max(len(line), 1)

        if digit_ratio > 0.55 and len(line) > 40:
            cleaned_lines.append("[structured numeric/table-like content]")
        else:
            cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()