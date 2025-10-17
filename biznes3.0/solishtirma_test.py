import json, time, os

YANDEX_FILE = "yandexbuyurtma.json"
BIZNES_FILE = "biznesbuyurtmalar.json"
HISOBOT_FILE = "hisobot.json"

def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                return data
            except:
                return []
    return []

def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def normalize_name(name: str):
    return name.strip().lower().replace("’", "'")

def is_same_order(y, b):
    try:
        # Haydovchi ismi
        name_ok = normalize_name(y["driver_profile"]["name"]) == normalize_name(b["info"]["performer"]["fullname"])
        # Mashina raqami
        car_ok = y["car"]["license"]["number"].replace(" ", "").upper() == b["info"]["performer"]["vehicle"]["number"].replace(" ", "").upper()
        # Kategoriya
        class_ok = y["category"] == b["info"]["class"]
        # Kordinatalar
        y_lat, y_lon = y["address_from"]["lat"], y["address_from"]["lon"]
        b_lon, b_lat = b["info"]["source"]["geopoint"]
        geo_ok = abs(y_lat - b_lat) < 0.0001 and abs(y_lon - b_lon) < 0.0001
        return name_ok and car_ok and class_ok and geo_ok
    except:
        return False

def extract_report(y, b):
    event_waiting = next((e["event_at"] for e in y["events"] if e["order_status"] == "waiting"), None)
    event_complete = next((e["event_at"] for e in y["events"] if e["order_status"] == "complete"), None)
    return {
        "short_id": y.get("short_id"),
        "biznes_id": b.get("id"),
        "yandex_id": y.get("id"),
        "staff_name": b.get("staff_name"),
        "org_name": b.get("org_name"),
        "user_id": b["info"].get("user_id"),
        "status": b["info"].get("status"),
        "category": y.get("category"),
        "event_waiting_at": event_waiting,
        "event_complete_at": event_complete,
        "payment_method": y.get("payment_method"),
        "driver_id": y["driver_profile"].get("id"),
        "driver_name": y["driver_profile"].get("name"),
        "brand_model": y["car"].get("brand_model"),
        "number": y["car"]["license"].get("number"),
        "mileage": y.get("mileage"),
        "source_coords": {
            "yandex": [y["address_from"]["lon"], y["address_from"]["lat"]],
            "biznes": [b["info"]["source"]["geopoint"][0], b["info"]["source"]["geopoint"][1]]
        },
        "source_address": {
            "yandex": y["address_from"]["address"],
            "biznes": b["info"]["source"]["fullname"]
        },
        "destination_address": b["info"]["destination"]["fullname"]
    }

def main():
    print("🚕 KOTTA BOLA TAXI — avtomatik hisobot monitor ishga tushdi...")
    hisobot = load_json(HISOBOT_FILE)
    existing_ids = {(item.get("yandex_id"), item.get("biznes_id")) for item in hisobot if item.get("yandex_id") is not None and item.get("biznes_id") is not None}

    while True:
        yandex_data = load_json(YANDEX_FILE)
        biznes_raw = load_json(BIZNES_FILE)
        biznes_data = biznes_raw.get("orders", []) if isinstance(biznes_raw, dict) else []

        new_found = 0
        for y in yandex_data:
            for b in biznes_data:
                if is_same_order(y, b):
                    key = (y.get("id"), b.get("id"))
                    if key not in existing_ids:
                        report = extract_report(y, b)
                        hisobot.append(report)
                        existing_ids.add(key)
                        new_found += 1
                        print(f"🟢 Yangi buyurtma qo'shildi: {report['driver_name']} ({report['number']})")

        if new_found:
            save_json(HISOBOT_FILE, hisobot)
            print(f"✅ {new_found} ta yangi mos buyurtma hisobotga qo‘shildi.")
        else:
            print("⏳ Yangi mos buyurtma topilmadi...")

        # 60 soniyada bir tekshiradi
        time.sleep(60)

if __name__ == "__main__":
    main()
