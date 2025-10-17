import time
import copy
import json
import requests
import random
import threading
from flask import Flask, render_template, redirect, url_for, request, flash, session, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import timedelta, datetime

app = Flask(__name__)

# --- Tashkilot balansini to‘ldirish/yechish va tranzaksiya tarixi ---
# Balansni o‘zgartirish (admin uchun)
@app.route('/tashkilot/balans', methods=['POST'])
def balans_ozgartir():
    org_id = request.form.get('org_id')
    summa = float(request.form.get('summa', 0))
    action = request.form.get('action')  # 'plus' yoki 'minus'
    if not org_id or not action or summa == 0:
        flash('Maʼlumotlar toʻliq emas!', 'danger')
        return redirect(url_for('tashkilotlar'))
    tashkilotlar = read_json('tashkilotlar.json')
    if isinstance(tashkilotlar, dict):
        tashkilotlar = tashkilotlar.get('items', [])
    org = next((o for o in tashkilotlar if o['id'] == org_id), None)
    if not org:
        flash('Tashkilot topilmadi!', 'danger')
        return redirect(url_for('tashkilotlar'))
    old_balance = org.get('balance', 0)
    if action == 'plus':
        org['balance'] = old_balance + summa
        t_type = 'to‘ldirish'
    elif action == 'minus':
        org['balance'] = old_balance - summa
        t_type = 'yechish'
    else:
        flash('Noto‘g‘ri amal!', 'danger')
        return redirect(url_for('tashkilotlar'))
    # Saqlash
    write_json('tashkilotlar.json', {'items': tashkilotlar})
    # Tranzaksiya logi
    log = read_json('tashkilot_tranzaksiyalar.json')
    if not isinstance(log, list):
        log = []
    log.append({
        'org_id': org_id,
        'org_name': org.get('name'),
        'summa': summa,
        'action': t_type,
        'balance_after': org['balance'],
        'vaqt': time.strftime('%Y-%m-%d %H:%M:%S'),
        'admin': current_user.id if hasattr(current_user, 'id') else 'admin'
    })
    write_json('tashkilot_tranzaksiyalar.json', log)
    flash(f'Balans {t_type} muvaffaqiyatli!', 'success')
    return redirect(url_for('tashkilotlar'))

# Tranzaksiya tarixi sahifasi
@app.route('/tashkilot/tranzaksiya/<org_id>')
def tashkilot_tranzaksiya(org_id):
    log = read_json('tashkilot_tranzaksiyalar.json')
    if not isinstance(log, list):
        log = []
    org_tranz = [l for l in log if l.get('org_id') == org_id]
    org_name = org_tranz[0]['org_name'] if org_tranz else ''
    return render_template('tashkilot_tranzaksiyalar.html', tranzaksiyalar=org_tranz, org_name=org_name)
# --- Tashkilot balansidan pul yechish va belgilash funksiyasi ---
def balansdan_pul_yech_va_belgila():
    hisobotlar = read_json('hisobot.json')
    balanslog = read_json('buyurtmabalansi.json')
    if not isinstance(balanslog, list):
        balanslog = []
    balanslangan_ids = {b['yandex_id'] for b in balanslog}
    tashkilotlar = read_json('tashkilotlar.json')
    if isinstance(tashkilotlar, dict):
        tashkilotlar = tashkilotlar.get('items', [])
    # id -> obj
    tashkilot_map = {t['name']: t for t in tashkilotlar}
    yangilangan = False
    for item in hisobotlar:
        yid = item.get('yandex_id')
        org = item.get('org_name')
        narx = item.get('yakuniy_narx')
        if not yid or not org or not narx:
            continue
        if yid in balanslangan_ids:
            continue  # allaqachon yechilgan
        # Tashkilot topiladi
        t = tashkilot_map.get(org)
        if not t:
            continue
        # Balansdan yechish
        t['balance'] = t.get('balance', 0) - narx
        balanslog.append({
            'yandex_id': yid,
            'org_name': org,
            'summa': narx,
            'vaqt': item.get('event_complete_at'),
            'status': 'yechildi'
        })
        yangilangan = True
    if yangilangan:
        # Tashkilotlar va balans logini saqlash
        write_json('tashkilotlar.json', {'items': list(tashkilot_map.values())})
        write_json('buyurtmabalansi.json', balanslog)
    return yangilangan
# --- Har 1 daqiqada avtomatik hisoblash background task ---
import threading
import time
def background_hisobot_updater():
    while True:
        try:
            hisobla_va_saqlash()
            balansdan_pul_yech_va_belgila()
        except Exception as e:
            print(f"Hisobot yangilashda xatolik: {e}")
        time.sleep(60)  # 1 daqiqa kutish
# --- Hisobot uchun hisob-kitob va saqlash funksiyasi ---
def hisobla_va_saqlash():
    from datetime import datetime
    hisobotlar = read_json('hisobot.json')
    if not isinstance(hisobotlar, list):
        hisobotlar = []
    mavjud_ids = {h.get('yandex_id') for h in hisobotlar}
    yangilangan = False
    for item in hisobotlar:
        # Eski hisoblanganlarni o'zgartirmaymiz
        if 'yakuniy_narx' in item:
            continue
        tarif = get_tarif_by_name(item.get('category', ''))
        if not tarif:
            item['tarif_nomi'] = item.get('category', '-')
            item['call_price'] = item['minute_price'] = item['distance_price'] = item['vat'] = '-'
            item['safar_vaqti_min'] = item['safar_vaqti_narxi'] = item['safar_masofa_narxi'] = item['yakuniy_narx'] = '-'
            continue
        item['tarif_nomi'] = tarif['name']
        item['call_price'] = int(tarif['call_price'])
        item['minute_price'] = int(tarif['minute_price'])
        item['distance_price'] = float(tarif['distance_price'])
        item['vat'] = int(tarif['vat'])
        try:
            t1 = datetime.fromisoformat(item.get('event_waiting_at', '').replace('Z', '+00:00'))
            t2 = datetime.fromisoformat(item.get('event_complete_at', '').replace('Z', '+00:00'))
            safar_vaqti_min = int((t2 - t1).total_seconds() // 60)
        except Exception:
            safar_vaqti_min = 0
        item['safar_vaqti_min'] = safar_vaqti_min
        try:
            safar_masofa = float(item.get('mileage', '0'))
        except Exception:
            safar_masofa = 0
        item['safar_vaqti_narxi'] = safar_vaqti_min * item['minute_price']
        item['safar_masofa_narxi'] = safar_masofa * item['distance_price']
        item['safar_chaqirish_narxi'] = item['call_price']
        narx = item['safar_chaqirish_narxi'] + item['safar_vaqti_narxi'] + item['safar_masofa_narxi']
        item['yakuniy_narx'] = int(narx * (1 + item['vat']/100))
        yangilangan = True
    if yangilangan:
        write_json('hisobot.json', hisobotlar)
    return hisobotlar
import time
import copy
import json
import requests
import random
import threading
from flask import Flask, render_template, redirect, url_for, request, flash, session
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import timedelta, datetime

app = Flask(__name__)

from typing import Any, Dict, List, Optional

def read_json(filename: str) -> Any:
    with open(filename, 'r', encoding='utf-8') as f:
        return json.load(f)

def write_json(filename: str, data: Any) -> None:
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# Tarifni nomi bo'yicha topish
def get_tarif_by_name(tarif_name: str) -> Optional[Dict[str, Any]]:
    tariflar: List[Dict[str, Any]] = read_json('tariflar.json')
    for tarif in tariflar:
        if tarif['name'] == tarif_name:
            return tarif
    return None
app.secret_key = 'your_secret_key'
app.permanent_session_lifetime = timedelta(seconds=3600)
def update_biznes_orders():
    try:
        BIZNES_API_URL = 'https://b2b-api.go.yandex.ru/integration/2.0/orders/list'
        headers = {'Authorization': f'Bearer {TASHKILOTLAR_TOKEN}'}
        # Xodimlar va tashkilotlar mapping
        with open('xodimlar.json', 'r', encoding='utf-8') as xf:
            xodimlar = json.load(xf).get('items', [])
        xodim_map = {x['id']: x for x in xodimlar}
        with open('tashkilotlar.json', 'r', encoding='utf-8') as tf:
            tashkilotlar = json.load(tf).get('items', [])
        tashkilot_map = {t['id']: t['name'] for t in tashkilotlar}

        resp = requests.get(BIZNES_API_URL, headers=headers, timeout=30)
        data = resp.json()
        api_orders = data.get('items', [])
        orders = []
        for o in api_orders:
            if o.get('status') == 'complete':
                staff_id = o.get('user_id', '')
                staff = xodim_map.get(staff_id, {})
                staff_name = staff.get('fullname', staff_id)
                org_id = staff.get('department_id', '')
                org_name = tashkilot_map.get(org_id, org_id)
                order_id = o.get('id', '')
                # Batafsil ma'lumot olish
                try:
                    info_url = f"https://b2b-api.go.yandex.ru/integration/2.0/orders/info?order_id={order_id}"
                    info_resp = requests.get(info_url, headers=headers, timeout=30)
                    info_data = info_resp.json()
                except Exception as e:
                    info_data = {}
                order = {
                    'id': order_id,
                    'title': o.get('source', {}).get('fullname', order_id),
                    'amount': o.get('cost_with_vat', o.get('cost', '')),
                    'staff_name': staff_name,
                    'org_name': org_name,
                    'status': 'Bajarilgan',
                    'details': f"Manzil: {o.get('destination', {}).get('fullname', '')} | Sana: {o.get('due_date', '')} | Klass: {o.get('class', '')}",
                    'info': info_data
                }
                orders.append(order)
        last_update = int(time.time())
        with open('biznesbuyurtmalar.json', 'w', encoding='utf-8') as f:
            json.dump({'orders': orders, 'last_update': last_update}, f, ensure_ascii=False, indent=2)
        return {'success': True, 'last_update': last_update}
    except Exception as e:
        print('Biznes buyurtmalar yangilashda xatolik:', e)
        return {'success': False, 'last_update': None}

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# Fetch and save biznes buyurtmalar from API, merge with old
def fetch_and_save_biznes_buyurtmalar():
    BIZNES_API_URL = 'https://b2b-api.go.yandex.ru/integration/2.0/orders/list'
    BIZNES_JSON = 'biznesbuyurtmalar.json'
    headers = {'Authorization': f'Bearer {TASHKILOTLAR_TOKEN}'}
    try:
        resp = requests.get(BIZNES_API_URL, headers=headers, timeout=30)
        new_data = resp.json()
        new_orders = new_data.get('orders', [])
        # Load old orders
        try:
            with open(BIZNES_JSON, 'r', encoding='utf-8') as f:
                old_data = json.load(f)
        except Exception:
            old_data = {'orders': []}
        old_orders = old_data.get('orders', [])
        # Merge: keep all old, add only new unique to id
        old_ids = {o.get('id') for o in old_orders}
        merged_orders = copy.deepcopy(old_orders)
        for order in new_orders:
            if order.get('id') not in old_ids:
                merged_orders.append(order)
        with open(BIZNES_JSON, 'w', encoding='utf-8') as f:
            json.dump({'orders': merged_orders}, f, ensure_ascii=False, indent=2)
        print('Biznes buyurtmalar yangilandi.')
    except Exception as e:
        print('Biznes buyurtmalar API xatolik:', e)
def schedule_data_update():
    fetch_and_save_tashkilotlar()
    fetch_and_save_xodimlar()
    fetch_and_save_biznes_buyurtmalar()
    threading.Timer(3600, schedule_data_update).start()
# View biznes buyurtmalar
@app.route('/biznes_buyurtmalar')
@login_required
def biznes_buyurtmalar():
    BIZNES_API_URL = 'https://b2b-api.go.yandex.ru/integration/2.0/orders/list'
    headers = {'Authorization': f'Bearer {TASHKILOTLAR_TOKEN}'}

    # Faqat fayldan o'qib ko'rsatish
    try:
        with open('biznesbuyurtmalar.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            orders = data.get('orders', [])
            last_update = data.get('last_update', None)
            if last_update:
                last_update = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last_update))
            else:
                last_update = 'Nomaʼlum'
    except Exception as e:
        print('Fayldan o‘qishda xatolik:', e)
        orders = []
        last_update = 'Nomaʼlum'
    return render_template('biznes_buyurtmalar.html', orders=orders, last_update=last_update)


# Eskiz SMS API config
ESKIZ_API_URL = 'https://notify.eskiz.uz/api/message/sms/send'
ESKIZ_EMAIL = 'islam.payments@gmail.com'
ESKIZ_PASSWORD = 'YygHyL6QWUlDb4t1ANIAOivUPdxs9W6BTau7eHBb'
ESKIZ_TOKEN = 'YygHyL6QWUlDb4t1ANIAOivUPdxs9W6BTau7eHBb'  # Token, agar login orqali olish kerak bo‘lsa, dinamik o‘zgartiriladi
SMS_TEXT = 'KOTTA BOLA VIP : Web ilovasiga kirish uchun tasdiqlash kodi: {}'

LOGO_PATH = '/static/logo.png'

TASHKILOTLAR_API_URL = 'https://b2b-api.go.yandex.ru/integration/2.0/departments/list'
TASHKILOTLAR_TOKEN = 'y0__xDL2_qjCBjVmRUgge-0pRTlxHKgpC_mutgU26RhN9ahn94m5A'
TASHKILOTLAR_JSON = 'tashkilotlar.json'
XODIMLAR_API_URL = 'https://b2b-api.go.yandex.ru/integration/2.0/users?limit=5000000'
XODIMLAR_JSON = 'xodimlar.json'
# Fetch and save xodimlar from API
def fetch_and_save_xodimlar():
    headers = {'Authorization': f'Bearer {TASHKILOTLAR_TOKEN}'}
    try:
        resp = requests.get(XODIMLAR_API_URL, headers=headers, timeout=30)
        new_data = resp.json()
        # Eski xodimlar.jsonni o‘qiymiz
        try:
            with open(XODIMLAR_JSON, 'r', encoding='utf-8') as f:
                old_data = json.load(f)
        except Exception:
            old_data = {'items': []}
        # Yangi ro‘yxatga eski maʼlumotlarni qo‘shish (if needed)
        # (No merging logic for now, just overwrite)
        with open(XODIMLAR_JSON, 'w', encoding='utf-8') as f:
            json.dump(new_data, f, ensure_ascii=False, indent=2)
        print('Xodimlar yangilandi.')
    except Exception as e:
        print('Xodimlar API xatolik:', e)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

def load_users():
    try:
        with open('keys.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            return {u['username']: {'password': u['password'], 'phone': u['phone']} for u in data.get('users', [])}
    except Exception:
        return {}

class User(UserMixin):
    def __init__(self, username):
        self.id = username

@login_manager.user_loader
def load_user_obj(user_id):
    users = load_users()
    if user_id in users:
        return User(user_id)
    return None

@app.route('/')
@login_required
def home():
    theme = request.args.get('theme', 'dark')
    # Load keys.json for current user
    try:
        with open('keys.json', 'r', encoding='utf-8') as f:
            users_json = json.load(f).get('users', [])
    except Exception:
        users_json = []
    # Find current user's org_id
    user_obj = next((u for u in users_json if u['username'] == current_user.id), None)
    org_id = user_obj.get('org_id') if user_obj else None
    # Superuser logic: any user in keys.json without org_id is a superuser
    is_superuser = user_obj and not user_obj.get('org_id')
    try:
        with open(TASHKILOTLAR_JSON, 'r', encoding='utf-8') as f:
            tashkilotlar_data = json.load(f)
            all_orgs = tashkilotlar_data.get('items', [])
            if current_user.id == 'admin' or is_superuser:
                tashkilotlar_json = all_orgs
            elif org_id:
                tashkilotlar_json = [o for o in all_orgs if o['id'] == org_id]
            else:
                tashkilotlar_json = []
    except Exception:
        tashkilotlar_json = []
    # Attach org_name for each user if possible
    org_map = {o['id']: o['name'] for o in tashkilotlar_json}
    for u in users_json:
        u['org_name'] = org_map.get(u.get('org_id'), '') if u.get('org_id') else ''
    # Superuser sees all users, others only themselves
    if current_user.id == 'admin' or is_superuser:
        pass  # users_json unchanged
    else:
        users_json = [user_obj] if user_obj else []
    # Load xodimlar.json for staff section and attach org_name
    try:
        with open(XODIMLAR_JSON, 'r', encoding='utf-8') as f:
            all_xodimlar = json.load(f).get('items', [])
    except Exception:
        all_xodimlar = []
    org_id_to_name = {o['id']: o['name'] for o in tashkilotlar_json}
    # Staff visibility logic
    if current_user.id == 'admin' or is_superuser:
        xodimlar_json = all_xodimlar
    elif org_id:
        xodimlar_json = [x for x in all_xodimlar if x.get('department_id') == org_id]
    else:
        xodimlar_json = []
    for x in xodimlar_json:
        dept_id = x.get('department_id')
        x['org_name'] = org_id_to_name.get(dept_id, '')
    # For filter dropdown, get all orgs
    all_orgs = tashkilotlar_json
    # Load tariflar.json
    try:
        with open('tariflar.json', 'r', encoding='utf-8') as f:
            tariflar_json = json.load(f)
    except Exception:
        tariflar_json = []
    # Load buyurtmabalansi.json
    try:
        with open('buyurtmabalansi.json', 'r', encoding='utf-8') as f:
            buyurtma_balansi = json.load(f)
    except Exception:
        buyurtma_balansi = []
    user_org_id = user_obj.get('org_id') if user_obj else None
    user_org_name = user_obj.get('org_name') if user_obj else None

    # --- STATISTIKA HISOBLASH ---
    # Jami xodimlar soni
    jami_xodimlar_soni = len(all_xodimlar)
    # Jami balans
    jami_balans = sum([o.get('balance', 0) for o in tashkilotlar_json])
    # Oxirgi 24 soat
    now = int(time.time())
    kirim_soni = chiqim_soni = kirim_summa = chiqim_summa = 0
    oxirgi_24soat_tranz = []
    for org in tashkilotlar_json:
        for c in org.get('comments', []):
            try:
                t = c['time']
                if isinstance(t, str) and t.isdigit():
                    t = int(t)
                elif isinstance(t, str):
                    t = int(datetime.strptime(t, '%Y-%m-%d %H:%M:%S').timestamp())
            except:
                t = 0
            if now - t <= 86400:
                oxirgi_24soat_tranz.append(c)
                if c['action'] == 'add':
                    kirim_soni += 1
                    kirim_summa += int(c['amount'])
                else:
                    chiqim_soni += 1
                    chiqim_summa += int(c['amount'])
    # Buyurtmalar soni
    try:
        with open('yandexbuyurtmalar.json', 'r', encoding='utf-8') as f:
            yandex_orders = json.load(f)
        yandex_buyurtmalar_soni = len(yandex_orders)
    except:
        yandex_buyurtmalar_soni = 0
    try:
        with open('biznesbuyurtmalar.json', 'r', encoding='utf-8') as f:
            biznes_orders = json.load(f).get('orders', [])
        biznes_buyurtmalar_soni = len(biznes_orders)
    except:
        biznes_buyurtmalar_soni = 0
    jami_buyurtmalar_soni = yandex_buyurtmalar_soni + biznes_buyurtmalar_soni
    # Faol tashkilotlar va xodimlar foizi
    faol_tashkilotlar = [o for o in tashkilotlar_json if o.get('balance', 0) > 0]
    faol_tashkilot_foizi = int(100 * len(faol_tashkilotlar) / len(tashkilotlar_json)) if tashkilotlar_json else 0
    faol_xodimlar = [x for x in all_xodimlar if x.get('is_active')]
    faol_xodim_foizi = int(100 * len(faol_xodimlar) / len(all_xodimlar)) if all_xodimlar else 0
    # TOP-3 kirim/chiqim tashkilotlar
    kirimlar = {}
    chiqimlar = {}
    for org in tashkilotlar_json:
        kid = org['id']
        kirimlar[kid] = sum([int(c['amount']) for c in org.get('comments', []) if c['action'] == 'add'])
        chiqimlar[kid] = sum([int(c['amount']) for c in org.get('comments', []) if c['action'] != 'add'])
    top3_kirim_tashkilot = sorted(kirimlar.items(), key=lambda x: x[1], reverse=True)[:3]
    top3_chiqim_tashkilot = sorted(chiqimlar.items(), key=lambda x: x[1], reverse=True)[:3]
    # Oxirgi 5 tranzaksiya
    all_tranz = []
    for org in tashkilotlar_json:
        for c in org.get('comments', []):
            all_tranz.append({**c, 'org_name': org['name']})
    all_tranz.sort(key=lambda x: x.get('time', 0), reverse=True)
    oxirgi_5_tranzaksiya = all_tranz[:5]
    # --- TASHKILOTGA XOS STATISTIKA ---
    tashkilot_stat = {}
    for org in tashkilotlar_json:
        oid = org['id']
        org_xodimlar = [x for x in all_xodimlar if x.get('department_id') == oid]
        org_kirimlar = [c for c in org.get('comments', []) if c['action'] == 'add']
        org_chiqimlar = [c for c in org.get('comments', []) if c['action'] != 'add']
        org_buyurtmalar = [b for b in buyurtma_balansi if b.get('org_id') == oid or b.get('org_name') == org['name']]
        tashkilot_stat[oid] = {
            'xodimlar_soni': len(org_xodimlar),
            'balans': org.get('balance', 0),
            'kirim_soni': len(org_kirimlar),
            'kirim_summa': sum([int(c['amount']) for c in org_kirimlar]),
            'chiqim_soni': len(org_chiqimlar),
            'chiqim_summa': sum([int(c['amount']) for c in org_chiqimlar]),
            'buyurtmalar_soni': len(org_buyurtmalar),
            'faol_xodim_foizi': int(100 * len([x for x in org_xodimlar if x.get('is_active')]) / len(org_xodimlar)) if org_xodimlar else 0,
            'oxirgi_5_tranzaksiya': sorted([{**c, 'org_name': org['name']} for c in org.get('comments', [])], key=lambda x: x.get('time', 0), reverse=True)[:5]
        }
    return render_template(
        'home.html',
        username=current_user.id,
        logo=LOGO_PATH,
        theme=theme,
        tashkilotlar_json=tashkilotlar_json,
        users_json=users_json,
        xodimlar_json=xodimlar_json,
        all_orgs=all_orgs,
        tariflar_json=tariflar_json,
        buyurtma_balansi=buyurtma_balansi,
        user_org_id=user_org_id,
        user_org_name=user_org_name,
        jami_xodimlar_soni=jami_xodimlar_soni,
        jami_balans=jami_balans,
        oxirgi_24soat_kirim_soni=kirim_soni,
        oxirgi_24soat_kirim_summa=kirim_summa,
        oxirgi_24soat_chiqim_soni=chiqim_soni,
        oxirgi_24soat_chiqim_summa=chiqim_summa,
        jami_buyurtmalar_soni=jami_buyurtmalar_soni,
        faol_tashkilot_foizi=faol_tashkilot_foizi,
        faol_xodim_foizi=faol_xodim_foizi,
        top3_kirim_tashkilot=top3_kirim_tashkilot,
        top3_chiqim_tashkilot=top3_chiqim_tashkilot,
        oxirgi_5_tranzaksiya=oxirgi_5_tranzaksiya,
        tashkilot_stat=tashkilot_stat
    )
# Tariflar saqlash
@app.route('/save_tarif', methods=['POST'])
@login_required
def save_tarif():
    import uuid
    tarif = {
        'id': str(uuid.uuid4()),
        'name': request.form.get('name'),
        'call_price': request.form.get('call_price'),
        'minute_price': request.form.get('minute_price'),
        'distance_price': request.form.get('distance_price'),
        'vat': request.form.get('vat')
    }
    try:
        with open('tariflar.json', 'r', encoding='utf-8') as f:
            tariflar = json.load(f)
    except Exception:
        tariflar = []
    tariflar.append(tarif)
    with open('tariflar.json', 'w', encoding='utf-8') as f:
        json.dump(tariflar, f, ensure_ascii=False, indent=2)
    flash('Tarif saqlandi!', 'success')
    return redirect(url_for('home') + '#tariffs-section')

# Tarif tahrirlash
@app.route('/edit_tarif/<id>', methods=['GET', 'POST'])
@login_required
def edit_tarif(id):
    try:
        with open('tariflar.json', 'r', encoding='utf-8') as f:
            tariflar = json.load(f)
    except Exception:
        tariflar = []
    tarif = next((t for t in tariflar if t['id'] == id), None)
    if not tarif:
        flash('Tarif topilmadi!', 'danger')
        return redirect(url_for('home') + '#tariffs-section')
    if request.method == 'POST':
        tarif['name'] = request.form.get('name')
        tarif['call_price'] = request.form.get('call_price')
        tarif['minute_price'] = request.form.get('minute_price')
        tarif['distance_price'] = request.form.get('distance_price')
        tarif['vat'] = request.form.get('vat')
        with open('tariflar.json', 'w', encoding='utf-8') as f:
            json.dump(tariflar, f, ensure_ascii=False, indent=2)
        flash('Tarif tahrirlandi!', 'success')
        return redirect(url_for('home') + '#tariffs-section')
    return render_template('edit_tarif.html', tarif=tarif)
# Save organization login
@app.route('/save_org_login', methods=['POST'])
@login_required
def save_org_login():
    org_id = request.form.get('org_id')
    login = request.form.get(f'login_{org_id}')
    password = request.form.get(f'password_{org_id}')
    phone = request.form.get(f'phone_{org_id}')
    # Load tashkilotlar.json for org name
    try:
        with open(TASHKILOTLAR_JSON, 'r', encoding='utf-8') as f:
            orgs = json.load(f).get('items', [])
    except Exception:
        orgs = []
    org_name = next((o['name'] for o in orgs if o['id'] == org_id), '')
    # Load keys.json
    try:
        with open('keys.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        data = {'users': []}
    # Only one login per org: remove old if exists
    data['users'] = [u for u in data['users'] if u.get('org_id') != org_id]
    # Add new user
    data['users'].append({
        'username': login,
        'password': password,
        'phone': phone,
        'org_id': org_id,
        'org_name': org_name
    })
    with open('keys.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    flash('Login, parol va telefon saqlandi!', 'success')
    return redirect(url_for('home') + '#profile-section')
# Edit organization login
@app.route('/edit_org_login/<username>', methods=['GET', 'POST'])
@login_required
def edit_org_login(username):
    # Load keys.json
    try:
        with open('keys.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        data = {'users': []}
    user = next((u for u in data['users'] if u['username'] == username), None)
    if request.method == 'POST' and user:
        user['username'] = request.form.get('login', user['username'])
        user['password'] = request.form.get('password', user['password'])
        user['phone'] = request.form.get('phone', user['phone'])
        with open('keys.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        flash('Login maʼlumotlari tahrirlandi!', 'success')
        return redirect(url_for('home') + '#profile-section')
    # Render edit form
    return render_template('edit_org_login.html', user=user)
# Delete organization login
@app.route('/delete_org_login/<username>', methods=['GET'])
@login_required
def delete_org_login(username):
    try:
        with open('keys.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        data = {'users': []}
    data['users'] = [u for u in data['users'] if u['username'] != username]
    with open('keys.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    flash('Login o‘chirildi!', 'success')
    return redirect(url_for('home') + '#profile-section')

@app.route('/login', methods=['GET', 'POST'])
def login():
    theme = request.args.get('theme', 'dark')
    if request.method == 'POST':
        session.permanent = True
        username = request.form.get('login')
        password = request.form.get('password')
        users = load_users()
        if username in users and users[username]['password'] == password:
            # Eskiz login to get JWT token
            login_url = 'https://notify.eskiz.uz/api/auth/login'
            login_data = {'email': ESKIZ_EMAIL, 'password': ESKIZ_PASSWORD}
            token = None
            try:
                login_resp = requests.post(login_url, data=login_data, timeout=10)
                token = login_resp.json().get('data', {}).get('token')
            except Exception as e:
                print('Eskiz login error:', e)
            if not token:
                flash('SMS serverga ulanishda xatolik!', 'danger')
                return render_template('login.html', logo=LOGO_PATH, theme=theme)
            # Generate OTP code
            otp_code = '{:06d}'.format(random.randint(0, 999999))
            session['otp_code'] = otp_code
            session['otp_user'] = username
            session['otp_time'] = int(time.time())  # Save code creation time
            # Send SMS via Eskiz
            phone = users[username].get('phone')
            if not phone:
                flash('Telefon raqami topilmadi. Iltimos, admin bilan bog‘laning.', 'danger')
                return render_template('login.html', logo=LOGO_PATH, theme=theme)
            headers = {'Authorization': f'Bearer {token}'}
            sms_message = f'KOTTA BOLA VIP : Web ilovasiga kirish uchun tasdiqlash kodi: {otp_code}'
            data = {
                'mobile_phone': phone,
                'message': sms_message,
                'from': '4546'
            }
            response = requests.post(ESKIZ_API_URL, headers=headers, data=data, timeout=10)
            print('Eskiz response:', response.text)
            return redirect(url_for('otp'))
        else:
            flash('Login yoki parol noto‘g‘ri!', 'danger')
    return render_template('login.html', logo=LOGO_PATH, theme=theme)

@app.route('/otp', methods=['GET', 'POST'])
def otp():
    if 'otp_code' not in session or 'otp_user' not in session or 'otp_time' not in session:
        return redirect(url_for('login'))
    expire_seconds = 180  # 3 minutes
    left = expire_seconds - (int(time.time()) - session['otp_time'])
    if request.method == 'POST':
        if request.form.get('resend') == '1':
            # Qayta yuborish tugmasi bosildi
            username = session.get('otp_user')
            users = load_users()
            if username in users:
                # Eskiz login to get JWT token
                login_url = 'https://notify.eskiz.uz/api/auth/login'
                login_data = {'email': ESKIZ_EMAIL, 'password': ESKIZ_PASSWORD}
                token = None
                try:
                    login_resp = requests.post(login_url, data=login_data, timeout=10)
                    token = login_resp.json().get('data', {}).get('token')
                except Exception as e:
                    print('Eskiz login error:', e)
                if not token:
                    flash('SMS serverga ulanishda xatolik!', 'danger')
                    return render_template('otp.html', left=expire_seconds)
                # Generate new OTP code
                otp_code = '{:06d}'.format(random.randint(0, 999999))
                session['otp_code'] = otp_code
                session['otp_time'] = int(time.time())
                phone = users[username].get('phone')
                sms_message = f'KOTTA BOLA VIP : Web ilovasiga kirish uchun tasdiqlash kodi: {otp_code}'
                data = {
                    'mobile_phone': phone,
                    'message': sms_message,
                    'from': '4546'
                }
                response = requests.post(ESKIZ_API_URL, headers={'Authorization': f'Bearer {token}'}, data=data, timeout=10)
                print('Eskiz response:', response.text)
                flash('Kod qayta yuborildi!', 'success')
                left = expire_seconds
            else:
                flash('Foydalanuvchi topilmadi!', 'danger')
        else:
            code = request.form.get('otp')
            if left > 0 and code == session.get('otp_code'):
                login_user(User(session['otp_user']))
                session.pop('otp_code', None)
                session.pop('otp_user', None)
                session.pop('otp_time', None)
                return redirect(url_for('home'))
            else:
                flash('Tasdiqlash kodi noto‘g‘ri yoki eskirgan!', 'danger')
    if left <= 0:
        expired = True
    else:
        expired = False
    return render_template('otp.html', left=left, expired=expired)



@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

def fetch_and_save_tashkilotlar():
    headers = {'Authorization': f'Bearer {TASHKILOTLAR_TOKEN}'}
    try:
        resp = requests.get(TASHKILOTLAR_API_URL, headers=headers, timeout=20)
        new_data = resp.json()
        # Eski tashkilotlar.jsonni o‘qiymiz
        try:
            with open(TASHKILOTLAR_JSON, 'r', encoding='utf-8') as f:
                old_data = json.load(f)
        except Exception:
            old_data = {'items': []}
        old_orgs = {o['id']: o for o in old_data.get('items', [])}
        # Yangi ro‘yxatga eski balans va kommentariyani qo‘shamiz
        for org in new_data.get('items', []):
            old_org = old_orgs.get(org['id'])
            if old_org:
                org['balance'] = old_org.get('balance', 0)
                org['comments'] = old_org.get('comments', [])
        with open(TASHKILOTLAR_JSON, 'w', encoding='utf-8') as f:
            json.dump(new_data, f, ensure_ascii=False, indent=2)
        print('Tashkilotlar yangilandi, balanslar saqlab qolindi.')
    except Exception as e:
        print('Tashkilotlar API xatolik:', e)

def schedule_data_update():
    fetch_and_save_tashkilotlar()
    fetch_and_save_xodimlar()
    threading.Timer(3600, schedule_data_update).start()

@app.route('/tashkilotlar')
def tashkilotlar():
    try:
        with open(TASHKILOTLAR_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        data = []
    return render_template('tashkilotlar.html', tashkilotlar=data)

@app.route('/update_balance', methods=['POST'])
def update_balance():
    org_id = request.form.get('org_id')
    amount = request.form.get('amount', type=float)
    comment = request.form.get('comment', '')
    action = request.form.get('action')
    if not org_id or not action or amount is None:
        flash('Maʼlumotlar toʻliq emas!', 'danger')
        return redirect(url_for('home') + '#kirimchiqim-section')
    try:
        with open('tashkilotlar.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
        tashkilotlar = data.get('items', []) if isinstance(data, dict) else data
    except Exception:
        tashkilotlar = []
    org = next((o for o in tashkilotlar if o['id'] == org_id), None)
    if not org:
        flash('Tashkilot topilmadi!', 'danger')
        return redirect(url_for('home') + '#kirimchiqim-section')
    old_balance = org.get('balance', 0)
    if action == 'add':
        org['balance'] = old_balance + amount
        t_type = 'to‘ldirish'
    elif action == 'subtract':
        org['balance'] = old_balance - amount
        t_type = 'yechish'
    else:
        flash('Noto‘g‘ri amal!', 'danger')
        return redirect(url_for('home') + '#kirimchiqim-section')
    # Kommentariya logini saqlash
    if 'comments' not in org:
        org['comments'] = []
    org['comments'].append({
        'amount': amount,
        'comment': comment,
        'action': action,
        'time': int(time.time())
    })
    with open('tashkilotlar.json', 'w', encoding='utf-8') as f:
        json.dump({'items': tashkilotlar}, f, ensure_ascii=False, indent=2)
    flash(f'Balans {t_type} muvaffaqiyatli!', 'success')
    return redirect(url_for('home') + '#kirimchiqim-section')
# Jinja2 filter: unix timestamp to date string
@app.template_filter('datetime')
def datetime_filter(ts):
    try:
        ts_float = float(ts)
        if ts_float > 0:
            return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts_float))
        else:
            return "Noma'lum"
    except Exception:
        return "Noma'lum"

@app.template_filter('uzb_datetime')
def uzb_datetime(dt_str):
    try:
        # dt_str: '2025-10-11T00:00:00+00:00' yoki shunga o'xshash
        from datetime import datetime, timedelta
        dt = datetime.strptime(dt_str[:19], '%Y-%m-%dT%H:%M:%S')
        dt_uzb = dt + timedelta(hours=5)
        return dt_uzb.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return dt_str

# Flask serverni ishga tushirganda har soatda avtomatik yangilash uchun
if __name__ == '__main__':
    # Flask reloader/threading fix: run updater only in main process
    import os
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        schedule_data_update()

    @app.route('/update_biznes_orders', methods=['POST', 'GET'])
    def update_biznes_orders_route():
        result = update_biznes_orders()
        if result['last_update']:
            formatted = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(result['last_update']))
            result['last_update'] = formatted
        return json.dumps(result), 200, {'Content-Type': 'application/json'}

    @app.route('/update_yandex_orders', methods=['POST'])
    @login_required
    def update_yandex_orders_route():
        API_URL = "https://fleet-api.taxi.yandex.net/v1/parks/orders/list"
        API_KEY = "UWtFnJHCCLktfHNZgmnMZxELFzlJokRTHDQgCMCP"
        CLID = "taxi/park/96ef821f965a4b4db656a15d21b322b0"
        PARK_ID = "96ef821f965a4b4db656a15d21b322b0"
        JSON_FILE = "yandexbuyurtma.json"
        headers = {
            "X-API-Key": API_KEY,
            "Content-Type": "application/json",
            "X-Client-ID": CLID
        }
        now = datetime.utcnow()
        interval = timedelta(hours=24)
        from_time = now - interval
        to_time = now
        payload = {
            "query": {
                "park": {
                    "id": PARK_ID,
                    "order": {
                        "ended_at": {
                            "from": from_time.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                            "to": to_time.strftime("%Y-%m-%dT%H:%M:%S+00:00")
                        }
                    }
                }
            },
            "limit": 500
        }
        response = requests.post(API_URL, headers=headers, data=json.dumps(payload))
        orders = []
        if response.status_code == 200:
            try:
                data = response.json()
                orders = data.get("orders", [])
            except Exception as e:
                print("JSON parse error:", e)
        else:
            print("Status Code:", response.status_code)
            print("Response:", response.text)
        old_orders = []
        if os.path.exists(JSON_FILE):
            with open(JSON_FILE, "r", encoding="utf-8") as f:
                try:
                    old_orders = json.load(f)
                except Exception:
                    old_orders = []
        old_ids = set(order.get("id") for order in old_orders)
        new_orders = [order for order in orders if order.get("id") not in old_ids and order.get("status") == "complete"]
        if new_orders:
            all_orders = old_orders + new_orders
            with open(JSON_FILE, "w", encoding="utf-8") as f:
                json.dump(all_orders, f, ensure_ascii=False, indent=2)
            print(f"{len(new_orders)} ta yangi buyurtma saqlandi.")
            return {"success": True, "new_count": len(new_orders)}
        else:
            print("Yangi buyurtma topilmadi.")
            return {"success": True, "new_count": 0}

    @app.route('/yandex_buyurtmalar')
    @login_required
    def yandex_buyurtmalar():
        try:
            with open('yandexbuyurtma.json', 'r', encoding='utf-8') as f:
                orders = json.load(f)
        except Exception:
            orders = []
        return render_template('yandex_buyurtmalar.html', orders=orders)


    # Hisobot to'liq ko'rsatish route
    @app.route('/hisobot_full', methods=['GET', 'POST'])
    @login_required
    def hisobot_full():
        from datetime import datetime
        try:
            with open('hisobot.json', 'r', encoding='utf-8') as f:
                all_hisobot = json.load(f)
        except Exception:
            all_hisobot = []
        # Foydalanuvchi tashkilotga bog'langanmi?
        try:
            with open('keys.json', 'r', encoding='utf-8') as f:
                users_json = json.load(f).get('users', [])
        except Exception:
            users_json = []
        user_obj = next((u for u in users_json if u['username'] == current_user.id), None)
        org_id = user_obj.get('org_id') if user_obj else None
        is_superuser = current_user.id == 'admin' or (user_obj and not user_obj.get('org_id'))
        # Sana filtrlash
        from_date = request.args.get('from_date')
        to_date = request.args.get('to_date')
        def parse_date(val):
            try:
                return datetime.strptime(val, '%Y-%m-%d')
            except:
                return None
        from_dt = parse_date(from_date) if from_date else None
        to_dt = parse_date(to_date) if to_date else None
        def in_range(item):
            date_str = item.get('event_complete_at') or item.get('date')
            try:
                item_dt = datetime.strptime(date_str[:10], '%Y-%m-%d')
            except:
                return False
            if from_dt and item_dt < from_dt:
                return False
            if to_dt and item_dt > to_dt:
                return False
            return True
        if is_superuser:
            hisobot = [h for h in all_hisobot if in_range(h)] if (from_dt or to_dt) else all_hisobot
        elif org_id:
            filtered = [h for h in all_hisobot if (h.get('org_id') == org_id or h.get('org_name') == user_obj.get('org_name'))]
            hisobot = [h for h in filtered if in_range(h)] if (from_dt or to_dt) else filtered
        else:
            hisobot = []

        # --- Hisob-kitob logikasi va faylga yozish ---
        hisobla_va_saqlash()
        return render_template('hisobot_full.html', hisobot=hisobot, from_date=from_date, to_date=to_date)



    

   
import subprocess

def run_solishtirma_test():
        subprocess.Popen(['python', 'solishtirma_test.py'])


if __name__ == '__main__':
         import os
         if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
            schedule_data_update()
            run_solishtirma_test()
            # Hisobotni avtomatik yangilovchi background thread
            threading.Thread(target=background_hisobot_updater, daemon=True).start()
            app.run(host='0.0.0.0', port=5000, debug=True)