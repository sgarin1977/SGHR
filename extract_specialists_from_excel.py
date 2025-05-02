import pandas as pd
import json
import re

EXCEL_PATH = "Спправочник специалистов.xlsx"
OUTPUT_PATH = "specialists_raw.json"

def normalize_phone(phone):
    phone = str(phone).strip()
    if phone.startswith("+") or phone.startswith("00"):
        return phone
    match = re.fullmatch(r"9\d{8}", phone)
    if match:
        return "+351" + phone
    return phone

def extract_city(raw_city):
    if pd.isna(raw_city):
        return None
    parts = re.split(r"[ ,;/]", str(raw_city))
    for p in parts:
        if len(p) >= 3:
            return p.strip().title()
    return None

def extract_specialists_from_excel():
    df = pd.read_excel(EXCEL_PATH)
    specialists = []

    for _, row in df.iterrows():
        name = str(row["ФИО/название компании"]).strip().title()
        profession = str(row["категория"]).strip().title() if pd.notna(row["категория"]) else None
        direction = str(row["раздел"]).strip().title() if pd.notna(row["раздел"]) else None
        city = extract_city(row["место нахождения"])
        phone = normalize_phone(row["телефон"])

        if name and profession and direction and city and phone:
            specialists.append({
                "full_name": name,
                "profession": profession,
                "direction": direction,
                "city": city,
                "contacts": phone
            })

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(specialists, f, ensure_ascii=False, indent=2)

    print(f"✅ Извлечено специалистов: {len(specialists)}")
    print(f"📁 JSON сохранён в: {OUTPUT_PATH}")

if __name__ == "__main__":
    extract_specialists_from_excel()

