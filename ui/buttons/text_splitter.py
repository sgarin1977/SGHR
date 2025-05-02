
def split_text(text: str, max_len: int = 400) -> tuple[str, bool]:
    if len(text) <= max_len:
        return text, False
    part = text[:max_len].rsplit(" ", 1)[0]
    return part + "...", True
