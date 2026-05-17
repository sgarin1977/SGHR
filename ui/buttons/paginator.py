from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

PER_PAGE = 6

def paginate_list(items, prefix, page, lang):
    start = page * PER_PAGE
    end = start + PER_PAGE
    buttons = []
    row = []

    for i, item in enumerate(items[start:end], start=1):
        row.append(InlineKeyboardButton(text=item.name_ru, callback_data=f"{prefix}:{item.id}"))
        if i % 2 == 0:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}_page:{page-1}"))
    if end < len(items):
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{prefix}_page:{page+1}"))
    if nav:
        buttons.append(nav)

    return buttons

def build_pagination_buttons(page: int, total_pages: int, prefix: str) -> InlineKeyboardMarkup:
    buttons = []

    if page > 1:
        buttons.append(InlineKeyboardButton("◀️", callback_data=f"{prefix}_page_{page - 1}"))

    buttons.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))

    if page < total_pages:
        buttons.append(InlineKeyboardButton("▶️", callback_data=f"{prefix}_page_{page + 1}"))

    return InlineKeyboardMarkup(inline_keyboard=[buttons])

