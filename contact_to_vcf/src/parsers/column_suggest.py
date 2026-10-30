"""Suggest name and phone columns from sample cells, not from fixed header words."""

from __future__ import annotations


def suggest_columns(
    columns: list[str], samples: list[tuple[str, ...]]
) -> tuple[int, int]:
    """Return ``(name_index, phone_index)`` for the preview.

    The phone column is the one whose sample cells look like phone numbers.
    The name column is a different column. The caller can still override both.
    """
    width = len(columns)
    if width == 0:
        return 0, 0
    if width == 1:
        return 0, 0
    scores = [0] * width
    for row in samples:
        for index, cell in enumerate(row):
            if index >= width:
                break
            if looks_like_phone(cell):
                scores[index] += 2
            elif cell.strip():
                scores[index] -= 1
    phone_index = max(range(width), key=lambda index: (scores[index], -index))
    if scores[phone_index] <= 0:
        phone_index = width - 1
    name_index = 0 if phone_index != 0 else 1
    for index, score in enumerate(scores):
        if index != phone_index and score <= 0:
            name_index = index
            break
    if name_index == phone_index:
        name_index = 0 if phone_index != 0 else 1
    return name_index, phone_index


def looks_like_phone(value: str) -> bool:
    text = value.strip()
    if text == "":
        return False
    if any(character.isalpha() for character in text):
        return False
    digits = sum(character.isdigit() for character in text)
    return 8 <= digits <= 15
