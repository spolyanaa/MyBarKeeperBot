# barkeeperbot.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import logging
import datetime as dt
from typing import Dict, List, Tuple, Optional

import pandas as pd
from pandas import DataFrame

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputFile,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from dotenv import load_dotenv

load_dotenv()

# ================== НАСТРОЙКИ ==================
TOKEN = os.getenv("BOT_TOKEN") # <-- замени на свой токен
if not TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env")

DATA_FILE = "data.xlsx"
SHEET_INVENTORY = "inventory"
SHEET_MOVES = "movements"
SHEET_SETTINGS = "settings"  # пороги закупа
SHEET_EXPIRY = "expiry"      # сроки годности

PAGE_SIZE = 10

# Планировщик (локальное время)
TZ = dt.timezone(dt.timedelta(hours=0))  # при необходимости замени на свой часовой пояс

# ================== ЛОГИ ==================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger(__name__)

# ================== КАТАЛОГ ==================
CATEGORIES: Dict[str, Dict[str, List[str]]] = {
    "beer_bottle": {
        "title": "Пиво (бутылочное/баночное)",
        "items": [
            "Миллер ЖБ",
            "Миллер Стекло",
            "Миллер Лайм",
            "Крушовица",
            "Крушовица Светлое",
            "Крушовица Темное",
            "Крушовица БА",
            "Особая варка речка",
            "Старопрамен",
            "J Hardy лимон",
            "J Hardy гранат",
            "J Hardy чили маракуйа",
            "Волки IPA",
            "Волки Session IPA",
            "Волки APA",
            "Волки Светлячок",
            "Волки Шоколадный стаут",
            "Волки Вишневый Эль",
            "Волки Медовуха Манго",
            "Волки Васька",
            "Волки WIT",
        ],
    },
    "beer_draft": {
        "title": "Пиво разливное",
        "items": [
            "Эдельвйес н/ф",
            "Речка Вишня",
            "Речка Белое особое",
            "Крушовица Светлое",
            "Крушовица Темное",
            "IPA Эль",
            "Квас",
            "Сидорова коза",
        ],
    },
    "strong": {
        "title": "Крепкое",
        "items": [
            "Finlandia blackurrant",
            "Bacardi Spiced",
            "Bacardi carta blanca",
            "Bacardi carta negra",
            "Jim Beam black cherry",
            "Jim Beam kentucky straight bourbon",
            "Tullamore D.E.W.",
            "Glenfiddich 12",
            "Bombay sapphire Gin",
            "Jagermeister",
            "Cointreau",
            "Vana Tallin chocolate liqueur",
            "Espolon blanco Tequila",
            "Torres reserva imperial",
            "Урарту",
            "Black monkey",
            "White cross",
            "Maverick gin",
            "Сябры",
        ],
    },
    "wine": {
        "title": "Вина и аперитивы",
        "items": [
            "Campari milano",
            "Verouth cinzano bianco",
            "Aperol aperitivo",
            "Casilleri del diablo chardonnay reserva",
            "Rose blend portugal",
            "Castelli romeo and guiletta prosecco",
            "Mondoro brut",
            "Deviils rock riesling",
            "Coni sur bicicleta reserva gewurztraminer",
            "Casillero del diablo carmenere reserva красное",
        ],
    },
    "soft": {
        "title": "б/а",
        "items": [
            "Святой источник н/г",
            "Святой источник газ",
            "Borjomi ПЭТ 0,5",
            "Borjomi Стекло 0,33",
            "Borjomi ЖБ 0,33",
            "Borjomi цитрус",
            "Borjomi мандарин",
            "Borjomi груша",
            "Gorilla Classic",
            "Pepsi",
            "7up",
            "Mirinda",
            "Mountew dew",
            "Сок Ананас",
            "Сок Вишня",
            "Сок Апельсин",
            "Сок Яблоко",
            "Bonaqua",
            "Schweppes",
        ],
    },
    "syrup": {
        "title": "Сиропы",
        "items": [
            "Richeza Lemon and concentrate",
            "Richeza peach",
            "Richeza pear",
            "Richeza basil and lemon",
            "Richeza kiwi and feijoa",
            "Richeza yuzu",
            "Richeza blackcurrant and mint",
            "Richeza mango and passion fruit",
        ],
    },
}

ALL_PRODUCTS: List[str] = sum([v["items"] for v in CATEGORIES.values()], [])

# ================== СОСТОЯНИЯ ==================
(
    ROLE,                 # выбор роли
    # Бармен
    B_CAT, B_ITEM, B_QTY, B_CONFIRM,
    # Админ
    A_MENU,
    A_STATS_MENU,
    A_DODEP_MENU,
    A_DODEP_SET_MODE,     # выбор режимов порогов (нищий/люксовый)
    A_DODEP_SET_CAT,      # выбор категории для настройки порога
    A_DODEP_SET_ITEM,     # выбор товара для настройки порога
    A_DODEP_SET_QTY,      # ввод порога
    A_RECEIVE_MENU,       # меню приема
    A_RECEIVE_PICK_ITEM,  # выбрать продукт из каталога для приема
    A_RECEIVE_QTY,        # ввести количество для приема
    A_RECEIVE_NEW_NAME,   # ввести имя нового продукта
    A_RECEIVE_NEW_QTY,    # ввести кол-во нового продукта
    A_EXPIRY_PICK_ITEM,   # выбор товара для ввода срока годности
    A_EXPIRY_ENTER_DATE,  # ввод даты
) = range(19)

# ================== ПАМЯТЬ В ЗАПУСКЕ ==================
ACTIVE_ADMINS: set[int] = set()

# ================== EXCEL УТИЛИТЫ ==================
def ensure_excel() -> None:
    """Создаёт файл и нужные листы, если их нет."""
    if not os.path.exists(DATA_FILE):
        inv = pd.DataFrame(columns=["product", "unit", "qty"])
        mov = pd.DataFrame(columns=["ts", "who", "action", "user_id", "product", "qty"])
        setdf = pd.DataFrame(columns=["product", "poor_threshold", "luxe_threshold"])
        exp = pd.DataFrame(columns=["product", "expiry_date", "qty"])
        with pd.ExcelWriter(DATA_FILE, engine="openpyxl", mode="w") as w:
            inv.to_excel(w, index=False, sheet_name=SHEET_INVENTORY)
            mov.to_excel(w, index=False, sheet_name=SHEET_MOVES)
            setdf.to_excel(w, index=False, sheet_name=SHEET_SETTINGS)
            exp.to_excel(w, index=False, sheet_name=SHEET_EXPIRY)
        log.info("Создан новый Excel с базовыми листами.")

    # Убедимся, что все листы есть
    xl = pd.ExcelFile(DATA_FILE, engine="openpyxl")
    existing = set(xl.sheet_names)
    changed = False
    if SHEET_INVENTORY not in existing:
        pd.DataFrame(columns=["product", "unit", "qty"]).to_excel(
            DATA_FILE, sheet_name=SHEET_INVENTORY, index=False, engine="openpyxl"
        )
        changed = True
    if SHEET_MOVES not in existing:
        pd.DataFrame(columns=["ts", "who", "action", "user_id", "product", "qty"]).to_excel(
            DATA_FILE, sheet_name=SHEET_MOVES, index=False, engine="openpyxl"
        )
        changed = True
    if SHEET_SETTINGS not in existing:
        pd.DataFrame(columns=["product", "poor_threshold", "luxe_threshold"]).to_excel(
            DATA_FILE, sheet_name=SHEET_SETTINGS, index=False, engine="openpyxl"
        )
        changed = True
    if SHEET_EXPIRY not in existing:
        pd.DataFrame(columns=["product", "expiry_date", "qty"]).to_excel(
            DATA_FILE, sheet_name=SHEET_EXPIRY, index=False, engine="openpyxl"
        )
        changed = True
    if changed:
        log.info("Добавил недостающие листы в Excel.")


def load_df(sheet: str) -> DataFrame:
    return pd.read_excel(DATA_FILE, sheet_name=sheet, engine="openpyxl")


def save_df_map(dfs: Dict[str, DataFrame]) -> None:
    # читаем все текущие, обновляем только нужные
    try:
        xl = pd.ExcelFile(DATA_FILE, engine="openpyxl")
        all_sheets = {name: xl.parse(name) for name in xl.sheet_names}
    except Exception:
        all_sheets = {}
    all_sheets.update(dfs)
    with pd.ExcelWriter(DATA_FILE, engine="openpyxl", mode="w") as w:
        for name, df in all_sheets.items():
            df.to_excel(w, sheet_name=name, index=False)


def add_movement(
    who: str, action: str, user_id: int, product: str, qty: float
) -> None:
    """Пишем строку в movements и корректируем остатки в inventory."""
    ensure_excel()
    try:
        mov = load_df(SHEET_MOVES)
    except Exception:
        mov = pd.DataFrame(columns=["ts", "who", "action", "user_id", "product", "qty"])
    try:
        inv = load_df(SHEET_INVENTORY)
    except Exception:
        inv = pd.DataFrame(columns=["product", "unit", "qty"])

    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mov = pd.concat(
        [mov, pd.DataFrame([{"ts": ts, "who": who, "action": action, "user_id": user_id, "product": product, "qty": qty}])],
        ignore_index=True,
    )

    inv = inv.copy()
    if product in inv.get("product", pd.Series([])).values:
        idx = inv.index[inv["product"] == product][0]
        cur = float(inv.at[idx, "qty"]) if pd.notna(inv.at[idx, "qty"]) else 0.0
        delta = -qty if action == "consume" else qty
        inv.at[idx, "qty"] = cur + delta
    else:
        # Если не было — создаём
        delta = -qty if action == "consume" else qty
        new_qty = max(0.0, delta) if action == "receive" else 0.0  # расход для нового = 0
        inv = pd.concat(
            [inv, pd.DataFrame([{"product": product, "unit": "", "qty": new_qty}])],
            ignore_index=True,
        )

    save_df_map({SHEET_MOVES: mov, SHEET_INVENTORY: inv})


def get_thresholds() -> DataFrame:
    ensure_excel()
    try:
        s = load_df(SHEET_SETTINGS)
    except Exception:
        s = pd.DataFrame(columns=["product", "poor_threshold", "luxe_threshold"])
    if s.empty:
        # инициализируем строками для всех товаров (0 пороги по умолчанию)
        base = pd.DataFrame(
            [{"product": p, "poor_threshold": 0, "luxe_threshold": 0} for p in ALL_PRODUCTS]
        )
        save_df_map({SHEET_SETTINGS: base})
        return base
    # дополним отсутствующие позиции
    present = set(s["product"].astype(str).tolist())
    missing = [p for p in ALL_PRODUCTS if p not in present]
    if missing:
        add_rows = pd.DataFrame(
            [{"product": p, "poor_threshold": 0, "luxe_threshold": 0} for p in missing]
        )
        s = pd.concat([s, add_rows], ignore_index=True)
        save_df_map({SHEET_SETTINGS: s})
    return s


def set_threshold(product: str, mode: str, value: float) -> None:
    """mode in {'poor','luxe'}"""
    s = get_thresholds().copy()
    if product in s["product"].values:
        idx = s.index[s["product"] == product][0]
        if mode == "poor":
            s.at[idx, "poor_threshold"] = value
        else:
            s.at[idx, "luxe_threshold"] = value
    else:
        s = pd.concat(
            [s, pd.DataFrame([{"product": product, "poor_threshold": value if mode == "poor" else 0,
                               "luxe_threshold": value if mode == "luxe" else 0}])],
            ignore_index=True
        )
    save_df_map({SHEET_SETTINGS: s})


def compute_order(mode: str) -> List[Tuple[str, float]]:
    """Возвращает список (product, need_qty) исходя из порога (poor/luxe) и текущих остатков."""
    s = get_thresholds()
    inv = load_df(SHEET_INVENTORY)
    inv_map = {str(r["product"]): float(r["qty"]) if pd.notna(r["qty"]) else 0.0 for _, r in inv.iterrows()}
    out: List[Tuple[str, float]] = []
    for _, r in s.iterrows():
        prod = str(r["product"])
        thr = float(r["poor_threshold"] if mode == "poor" else r["luxe_threshold"])
        cur = float(inv_map.get(prod, 0.0))
        need = max(0.0, thr - cur)
        if need > 0:
            out.append((prod, need))
    return out


def record_expiry(product: str, expiry_date: dt.date, qty: float) -> None:
    """Сохраняем срок годности (суммируем по продукту/дате)."""
    ensure_excel()
    try:
        exp = load_df(SHEET_EXPIRY)
    except Exception:
        exp = pd.DataFrame(columns=["product", "expiry_date", "qty"])
    exp["expiry_date"] = pd.to_datetime(exp["expiry_date"], errors="coerce").dt.date

    # ищем совпадение по продукту+дата
    mask = (exp["product"] == product) & (exp["expiry_date"] == expiry_date)
    if mask.any():
        idx = exp.index[mask][0]
        old = float(exp.at[idx, "qty"]) if pd.notna(exp.at[idx, "qty"]) else 0.0
        exp.at[idx, "qty"] = old + qty
    else:
        exp = pd.concat(
            [exp, pd.DataFrame([{"product": product, "expiry_date": expiry_date, "qty": qty}])],
            ignore_index=True,
        )
    save_df_map({SHEET_EXPIRY: exp})


def list_products_kb(prefix: str, page: int = 0) -> InlineKeyboardMarkup:
    items = ALL_PRODUCTS
    total = len(items)
    start = page * PAGE_SIZE
    end = min(total, start + PAGE_SIZE)
    page_items = items[start:end]
    rows: List[List[InlineKeyboardButton]] = []
    for name in page_items:
        rows.append([InlineKeyboardButton(name, callback_data=f"{prefix}:{name}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"nav:{prefix}:{page-1}"))
    if end < total:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"nav:{prefix}:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="back"), InlineKeyboardButton("🏠 В начало", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def categories_kb(next_prefix: str) -> InlineKeyboardMarkup:
    rows = []
    for key in ["beer_bottle", "beer_draft", "strong", "wine", "soft", "syrup"]:
        rows.append([InlineKeyboardButton(CATEGORIES[key]["title"], callback_data=f"cat:{next_prefix}:{key}:0")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="back"), InlineKeyboardButton("🏠 В начало", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def items_in_category_kb(cat_key: str, next_prefix: str, page: int = 0) -> InlineKeyboardMarkup:
    items = CATEGORIES[cat_key]["items"]
    total = len(items)
    start = page * PAGE_SIZE
    end = min(total, start + PAGE_SIZE)
    page_items = items[start:end]
    rows: List[List[InlineKeyboardButton]] = [[InlineKeyboardButton(n, callback_data=f"{next_prefix}:{n}")] for n in page_items]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"catnav:{next_prefix}:{cat_key}:{page-1}"))
    if end < total:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"catnav:{next_prefix}:{cat_key}:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="back"), InlineKeyboardButton("🏠 В начало", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🍹 Бармен", callback_data="role:barmen")],
        [InlineKeyboardButton("🧮 Администратор", callback_data="role:admin")],
    ])


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Поделиться таблицей", callback_data="admin:share")],
        [InlineKeyboardButton("📊 Статистика", callback_data="admin:stats")],
        [InlineKeyboardButton("🧾 Додеп", callback_data="admin:dodep")],
        [InlineKeyboardButton("📦 Приём товара", callback_data="admin:receive")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back"), InlineKeyboardButton("🏠 В начало", callback_data="home")],
    ])


def stats_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("За месяц", callback_data="stats:30")],
        [InlineKeyboardButton("За 4 дня", callback_data="stats:4")],
        [InlineKeyboardButton("За сутки", callback_data="stats:1")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back"), InlineKeyboardButton("🏠 В начало", callback_data="home")],
    ])


def dodep_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Нищий закуп", callback_data="dodep:poor")],
        [InlineKeyboardButton("Люксовый закуп", callback_data="dodep:luxe")],
        [InlineKeyboardButton("Настроить закуп", callback_data="dodep:setup")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back"), InlineKeyboardButton("🏠 В начало", callback_data="home")],
    ])


def receive_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Приём по заявке (последний расчёт)", callback_data="recv:auto")],
        [InlineKeyboardButton("Добавить товар вручную (из меню)", callback_data="recv:manual")],
        [InlineKeyboardButton("Добавить новый продукт", callback_data="recv:new")],
        [InlineKeyboardButton("Ввести сроки годности", callback_data="recv:expiry")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back"), InlineKeyboardButton("🏠 В начало", callback_data="home")],
    ])


def confirm_more_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Добавить ещё", callback_data="b:more")],
        [InlineKeyboardButton("❌ Нет, это всё", callback_data="b:done")],
        [InlineKeyboardButton("🏠 В начало", callback_data="home")],
    ])


# =============== ХЕЛПЕРЫ СТАТИСТИКИ ===============
def compute_stats(days: int) -> str:
    """Суммируем расход (action=consume) за N дней, группируем по продукту."""
    ensure_excel()
    try:
        mov = load_df(SHEET_MOVES)
    except Exception:
        mov = pd.DataFrame(columns=["ts", "who", "action", "user_id", "product", "qty"])
    if mov.empty:
        return "Пока нет данных."

    mov["ts"] = pd.to_datetime(mov["ts"], errors="coerce")
    since = pd.Timestamp.now() - pd.Timedelta(days=days)
    mask = (mov["action"] == "consume") & (mov["ts"] >= since)
    df = mov.loc[mask].copy()
    if df.empty:
        return "За выбранный период расхода нет."

    grp = df.groupby("product", as_index=False)["qty"].sum().sort_values("qty", ascending=False)
    lines = [f"• {r['product']}: {r['qty']:.0f}" for _, r in grp.iterrows()]
    return "\n".join(lines)


# ================== ХЕНДЛЕРЫ ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.effective_message.reply_text(
        "Привет! Я бот, который ведёт учёт продукции на складе. Давай знакомиться:",
        reply_markup=main_menu_kb()
    )
    return ROLE


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("понг")


# ====== ЕДИНЫЙ КЛИК-ОБРАБОТЧИК ======
async def cb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    # Домой
    if data == "home":
        context.user_data.clear()
        await q.edit_message_text("Выбери роль:", reply_markup=main_menu_kb())
        return ROLE

    # Назад
    if data == "back":
        # Пытаемся понять, где мы были, по "ui_state"
        ui = context.user_data.get("ui_state", "root")
        if ui == "barmen_categories" or ui == "barmen_item" or ui == "barmen_qty":
            await q.edit_message_text("Выбери категорию:", reply_markup=categories_kb("bitem"))
            context.user_data["ui_state"] = "barmen_categories"
            return B_CAT
        if ui in {"admin_menu", "admin_stats", "admin_dodep", "admin_receive"}:
            await q.edit_message_text("Здравствуйте, начальник! Что делаем?", reply_markup=admin_menu_kb())
            context.user_data["ui_state"] = "admin_menu"
            return A_MENU
        if ui == "dodep_setup_pick_mode" or ui == "dodep_setup_pick_cat" or ui == "dodep_setup_pick_item" or ui == "dodep_setup_qty":
            await q.edit_message_text("Додеп:", reply_markup=dodep_menu_kb())
            context.user_data["ui_state"] = "admin_dodep"
            return A_DODEP_MENU
        if ui == "receive_menu" or ui == "receive_pick_item" or ui == "receive_qty" or ui == "receive_new_name" or ui == "receive_new_qty" or ui == "expiry_pick_item" or ui == "expiry_enter_date":
            await q.edit_message_text("Меню приёма товара:", reply_markup=receive_menu_kb())
            context.user_data["ui_state"] = "receive_menu"
            return A_RECEIVE_MENU

        # по умолчанию в главное
        await q.edit_message_text("Выбери роль:", reply_markup=main_menu_kb())
        return ROLE

    # ===== ВЫБОР РОЛИ =====
    if data.startswith("role:"):
        role = data.split(":", 1)[1]
        if role == "barmen":
            context.user_data["ui_state"] = "barmen_categories"
            await q.edit_message_text(
                "Ну как прошла смена? Выбери категорию и затем напиток. "
                "После этого введи количество потраченных бутылок:",
                reply_markup=categories_kb("bitem")
            )
            return B_CAT
        if role == "admin":
            ACTIVE_ADMINS.add(q.from_user.id)
            context.user_data["ui_state"] = "admin_menu"
            await q.edit_message_text("Здравствуйте, начальник! Что делаем?", reply_markup=admin_menu_kb())
            return A_MENU

    # ====== БАРМЕН: ВЫБОР КАТЕГОРИИ -> СПИСОК ТОВАРОВ ======
    if data.startswith("cat:bitem:"):
        _, _, cat_key, page = data.split(":")
        page = int(page)
        context.user_data["ui_state"] = "barmen_item"
        context.user_data["b_cat"] = cat_key
        await q.edit_message_text(
            f"Категория: {CATEGORIES[cat_key]['title']}\nВыбери напиток:",
            reply_markup=items_in_category_kb(cat_key, "bchoose", page)
        )
        return B_ITEM

    if data.startswith("catnav:bchoose:"):
        _, _, cat_key, page = data.split(":")
        page = int(page)
        context.user_data["ui_state"] = "barmen_item"
        context.user_data["b_cat"] = cat_key
        await q.edit_message_text(
            f"Категория: {CATEGORIES[cat_key]['title']}\nВыбери напиток:",
            reply_markup=items_in_category_kb(cat_key, "bchoose", page)
        )
        return B_ITEM

    if data.startswith("bchoose:"):
        product = data.split(":", 1)[1]
        context.user_data["b_product"] = product
        context.user_data["ui_state"] = "barmen_qty"
        await q.edit_message_text(
            f"Вы выбрали: <b>{product}</b>\n\nВведи <b>количество потраченных бутылок</b> числом (например, 5).",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="back"), InlineKeyboardButton("🏠 В начало", callback_data="home")]
            ])
        )
        return B_QTY

    if data == "b:more":
        context.user_data["ui_state"] = "barmen_categories"
        await q.edit_message_text("Добавь ещё! Выбери категорию:", reply_markup=categories_kb("bitem"))
        return B_CAT

    if data == "b:done":
        await q.edit_message_text("Класс, спасибо! Доброй ночи! 🌙")
        await q.message.reply_text("Выбери роль:", reply_markup=main_menu_kb())
        return ROLE

    # ====== АДМИН: МЕНЮ ======
    if data == "admin:share":
        ensure_excel()
        try:
            with open(DATA_FILE, "rb") as f:
                await q.message.reply_document(
                    document=InputFile(f, filename="data.xlsx"),
                    caption="Текущая таблица учёта (Excel)."
                )
        except Exception as e:
            await q.message.reply_text(f"Не удалось отправить файл: {e}")
        return A_MENU

    if data == "admin:stats":
        context.user_data["ui_state"] = "admin_stats"
        await q.edit_message_text("Выбери период:", reply_markup=stats_menu_kb())
        return A_STATS_MENU

    if data.startswith("stats:"):
        days = int(data.split(":")[1])
        txt = compute_stats(days)
        await q.message.reply_text(f"Статистика расхода за {days} дн.:\n\n{txt}")
        return A_STATS_MENU

    if data == "admin:dodep":
        context.user_data["ui_state"] = "admin_dodep"
        await q.edit_message_text("Додеп:", reply_markup=dodep_menu_kb())
        return A_DODEP_MENU

    if data == "dodep:poor":
        order = compute_order("poor")
        if not order:
            await q.message.reply_text("По нищему закупу — ничего не требуется докупать.")
        else:
            lines = [f"• {p} — {q:.0f}" for p, q in order]
            await q.message.reply_text("Нищий закуп (докупить):\n" + "\n".join(lines))
        return A_DODEP_MENU

    if data == "dodep:luxe":
        order = compute_order("luxe")
        if not order:
            await q.message.reply_text("По люксовому закупу — ничего не требуется докупать.")
        else:
            lines = [f"• {p} — {q:.0f}" for p, q in order]
            await q.message.reply_text("Люксовый закуп (докупить):\n" + "\n".join(lines))
        return A_DODEP_MENU

    if data == "dodep:setup":
        # выбрать, какие пороги будем настраивать
        context.user_data["ui_state"] = "dodep_setup_pick_mode"
        await q.edit_message_text(
            "Что настраиваем?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Порог Нищего закупа", callback_data="setup:mode:poor")],
                [InlineKeyboardButton("Порог Люксового закупа", callback_data="setup:mode:luxe")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="back"), InlineKeyboardButton("🏠 В начало", callback_data="home")]
            ])
        )
        return A_DODEP_SET_MODE

    if data.startswith("setup:mode:"):
        mode = data.split(":")[2]  # poor/luxe
        context.user_data["setup_mode"] = mode
        context.user_data["ui_state"] = "dodep_setup_pick_cat"
        await q.edit_message_text(
            f"Настройка порога: {'Нищий' if mode=='poor' else 'Люксовый'} закуп.\nВыбери категорию:",
            reply_markup=categories_kb("setupitem")
        )
        return A_DODEP_SET_CAT

    if data.startswith("cat:setupitem:"):
        _, _, cat_key, page = data.split(":")
        page = int(page)
        context.user_data["ui_state"] = "dodep_setup_pick_item"
        context.user_data["setup_cat"] = cat_key
        await q.edit_message_text(
            f"Категория: {CATEGORIES[cat_key]['title']}\nВыбери продукт:",
            reply_markup=items_in_category_kb(cat_key, "setupchoose", page)
        )
        return A_DODEP_SET_ITEM

    if data.startswith("catnav:setupchoose:"):
        _, _, cat_key, page = data.split(":")
        page = int(page)
        context.user_data["ui_state"] = "dodep_setup_pick_item"
        await q.edit_message_text(
            f"Категория: {CATEGORIES[cat_key]['title']}\nВыбери продукт:",
            reply_markup=items_in_category_kb(cat_key, "setupchoose", page)
        )
        return A_DODEP_SET_ITEM

    if data.startswith("setupchoose:"):
        prod = data.split(":", 1)[1]
        context.user_data["setup_product"] = prod
        context.user_data["ui_state"] = "dodep_setup_qty"
        await q.edit_message_text(
            f"Укажи числом порог для «{prod}» ({'Нищий' if context.user_data.get('setup_mode')=='poor' else 'Люксовый'} закуп):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back"),
                                                InlineKeyboardButton("🏠 В начало", callback_data="home")]])
        )
        return A_DODEP_SET_QTY

    if data == "admin:receive":
        context.user_data["ui_state"] = "receive_menu"
        await q.edit_message_text("Меню приёма товара:", reply_markup=receive_menu_kb())
        return A_RECEIVE_MENU

    if data == "recv:auto":
        # Принимаем по последнему расчёту — используем poor как пример (можно хранить последний выбор)
        mode = context.user_data.get("last_order_mode", "poor")
        order = compute_order(mode)
        if not order:
            await q.message.reply_text("Нет актуальной заявки (по выбранному порогу закуп не требуется).")
            return A_RECEIVE_MENU
        # Плюсуем в остатки всё из заявки
        for prod, qty in order:
            if qty > 0:
                add_movement("admin", "receive", q.from_user.id, prod, qty)
        await q.message.reply_text("Заявка принята в учёт. Не забудьте ввести сроки годности при необходимости.")
        return A_RECEIVE_MENU

    if data == "recv:manual":
        # меню категорий -> товары -> ввод количества -> +в остаток
        context.user_data["ui_state"] = "receive_pick_item"
        await q.edit_message_text("Выберите категорию товара для приёмки:", reply_markup=categories_kb("recvitem"))
        return A_RECEIVE_PICK_ITEM

    if data.startswith("cat:recvitem:"):
        _, _, cat_key, page = data.split(":")
        page = int(page)
        context.user_data["ui_state"] = "receive_pick_item"
        context.user_data["recv_cat"] = cat_key
        await q.edit_message_text(
            f"Категория: {CATEGORIES[cat_key]['title']}\nВыберите продукт:",
            reply_markup=items_in_category_kb(cat_key, "recvchoose", page)
        )
        return A_RECEIVE_PICK_ITEM

    if data.startswith("catnav:recvchoose:"):
        _, _, cat_key, page = data.split(":")
        page = int(page)
        context.user_data["ui_state"] = "receive_pick_item"
        await q.edit_message_text(
            f"Категория: {CATEGORIES[cat_key]['title']}\nВыберите продукт:",
            reply_markup=items_in_category_kb(cat_key, "recvchoose", page)
        )
        return A_RECEIVE_PICK_ITEM

    if data.startswith("recvchoose:"):
        prod = data.split(":", 1)[1]
        context.user_data["recv_product"] = prod
        context.user_data["ui_state"] = "receive_qty"
        await q.edit_message_text(
            f"Вы выбрали приём: <b>{prod}</b>\n\nВведите <b>количество поступивших бутылок</b> числом:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back"),
                                                InlineKeyboardButton("🏠 В начало", callback_data="home")]])
        )
        return A_RECEIVE_QTY

    if data == "recv:new":
        context.user_data["ui_state"] = "receive_new_name"
        await q.edit_message_text(
            "Введите НОВЫЙ продукт (название):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back"),
                                                InlineKeyboardButton("🏠 В начало", callback_data="home")]])
        )
        return A_RECEIVE_NEW_NAME

    if data == "recv:expiry":
        # меню всех продуктов -> выбор -> ввод даты
        context.user_data["ui_state"] = "expiry_pick_item"
        await q.edit_message_text("Выберите продукт для ввода срока годности:", reply_markup=list_products_kb("expchoose", 0))
        return A_EXPIRY_PICK_ITEM

    if data.startswith("nav:expchoose:"):
        _, _, page = data.split(":")
        page = int(page)
        context.user_data["ui_state"] = "expiry_pick_item"
        await q.edit_message_text("Выберите продукт для ввода срока годности:", reply_markup=list_products_kb("expchoose", page))
        return A_EXPIRY_PICK_ITEM

    if data.startswith("expchoose:"):
        prod = data.split(":", 1)[1]
        context.user_data["exp_product"] = prod
        context.user_data["ui_state"] = "expiry_enter_date"
        await q.edit_message_text(
            f"Продукт: <b>{prod}</b>\nВведи срок годности формата ДД.ММ.ГГГГ и количество через запятую.\n"
            "Например: 25.12.2025, 6",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back"),
                                                InlineKeyboardButton("🏠 В начало", callback_data="home")]])
        )
        return A_EXPIRY_ENTER_DATE

    return ConversationHandler.END


# ====== ВВОД КОЛИЧЕСТВА БАРМЕН ======
async def barmen_qty(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip().replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d+)?", text):
        await update.message.reply_text(
            "Введите количество ЧИСЛОМ. Например: 5",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back"),
                                                InlineKeyboardButton("🏠 В начало", callback_data="home")]])
        )
        return B_QTY
    qty = float(text)
    prod = context.user_data.get("b_product")
    if not prod:
        await update.message.reply_text("Сначала выберите категорию и напиток.", reply_markup=categories_kb("bitem"))
        context.user_data["ui_state"] = "barmen_categories"
        return B_CAT
    # Пишем расход
    add_movement("barman", "consume", update.effective_user.id, prod, qty)
    await update.message.reply_text(f"Записал расход: {prod} — {qty:.0f}.", reply_markup=confirm_more_kb())
    return B_CONFIRM


# ====== ВВОД ПОРОГА ЗАКУПА ======
async def dodep_set_qty(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip().replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d+)?", text):
        await update.message.reply_text(
            "Введите порог числом. Например: 10",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back"),
                                                InlineKeyboardButton("🏠 В начало", callback_data="home")]])
        )
        return A_DODEP_SET_QTY
    value = float(text)
    mode = context.user_data.get("setup_mode", "poor")
    prod = context.user_data.get("setup_product")
    if not prod:
        await update.message.reply_text("Сначала выбери продукт.", reply_markup=categories_kb("setupitem"))
        context.user_data["ui_state"] = "dodep_setup_pick_cat"
        return A_DODEP_SET_CAT

    set_threshold(prod, mode, value)
    await update.message.reply_text(f"Готово. Порог ({'Нищий' if mode=='poor' else 'Люксовый'}) для «{prod}» = {value:.0f}.")
    # запомним последний расчётный режим
    context.user_data["last_order_mode"] = mode
    return A_DODEP_MENU


# ====== ПРИЁМ ТОВАРА (КОЛИЧЕСТВО) ======
async def receive_qty(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip().replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d+)?", text):
        await update.message.reply_text(
            "Введите количество ЧИСЛОМ. Например: 8",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back"),
                                                InlineKeyboardButton("🏠 В начало", callback_data="home")]])
        )
        return A_RECEIVE_QTY
    qty = float(text)
    prod = context.user_data.get("recv_product")
    if not prod:
        await update.message.reply_text("Сначала выберите продукт из меню.", reply_markup=categories_kb("recvitem"))
        context.user_data["ui_state"] = "receive_pick_item"
        return A_RECEIVE_PICK_ITEM

    add_movement("admin", "receive", update.effective_user.id, prod, qty)
    await update.message.reply_text(f"Принял на склад: {prod} — {qty:.0f}.")
    return A_RECEIVE_MENU


# ====== НОВЫЙ ПРОДУКТ (ИМЯ, ПОТОМ КОЛ-ВО) ======
async def receive_new_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text(
            "Введите название продукта (текстом).",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back"),
                                                InlineKeyboardButton("🏠 В начало", callback_data="home")]])
        )
        return A_RECEIVE_NEW_NAME
    context.user_data["new_product_name"] = name
    await update.message.reply_text(
        f"Новый продукт: <b>{name}</b>\nВведите количество (числом):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back"),
                                            InlineKeyboardButton("🏠 В начало", callback_data="home")]])
    )
    return A_RECEIVE_NEW_QTY


async def receive_new_qty(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip().replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d+)?", text):
        await update.message.reply_text(
            "Введите количество ЧИСЛОМ. Например: 6",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back"),
                                                InlineKeyboardButton("🏠 В начало", callback_data="home")]])
        )
        return A_RECEIVE_NEW_QTY
    qty = float(text)
    prod = context.user_data.get("new_product_name")
    add_movement("admin", "receive", update.effective_user.id, prod, qty)
    # добавим продукт в справочник ALL_PRODUCTS (в сессии не сохраняем навсегда, хранится в Excel)
    if prod not in ALL_PRODUCTS:
        ALL_PRODUCTS.append(prod)
    await update.message.reply_text(f"Добавлен новый продукт: {prod} — {qty:.0f}.")
    return A_RECEIVE_MENU


# ====== СРОКИ ГОДНОСТИ ======
async def expiry_enter_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    # формат: ДД.ММ.ГГГГ, QTY
    m = re.match(r"^\s*(\d{2})\.(\d{2})\.(\d{4})\s*,\s*(\d+(?:\.\d+)?)\s*$", text)
    if not m:
        await update.message.reply_text(
            "Неверный формат. Нужен: ДД.ММ.ГГГГ, количество\nНапример: 25.12.2025, 6",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back"),
                                                InlineKeyboardButton("🏠 В начало", callback_data="home")]])
        )
        return A_EXPIRY_ENTER_DATE
    d, mth, y, qty_s = m.groups()
    try:
        dte = dt.date(int(y), int(mth), int(d))
    except Exception:
        await update.message.reply_text(
            "Дата некорректна. Повторите ввод.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back"),
                                                InlineKeyboardButton("🏠 В начало", callback_data="home")]])
        )
        return A_EXPIRY_ENTER_DATE
    qty = float(qty_s)
    prod = context.user_data.get("exp_product")
    if not prod:
        await update.message.reply_text("Сначала выберите продукт.", reply_markup=list_products_kb("expchoose", 0))
        context.user_data["ui_state"] = "expiry_pick_item"
        return A_EXPIRY_PICK_ITEM

    record_expiry(prod, dte, qty)
    await update.message.reply_text(f"Срок годности записан: {prod} — до {dte.strftime('%d.%m.%Y')}, {qty:.0f} шт.")
    return A_RECEIVE_MENU


# ================== СИСТЕМНЫЕ ДЖОБЫ ==================
async def job_daily_expiry(context: ContextTypes.DEFAULT_TYPE):
    """Каждый день в 09:00 — проверка сроков, напоминание за месяц."""
    ensure_excel()
    try:
        exp = load_df(SHEET_EXPIRY)
    except Exception:
        return
    if exp.empty:
        return
    exp["expiry_date"] = pd.to_datetime(exp["expiry_date"], errors="coerce").dt.date
    today = dt.date.today()
    warn_date = today + dt.timedelta(days=30)
    due = exp.loc[exp["expiry_date"] == warn_date]
    if due.empty:
        return
    # отправляем активным администраторам
    for admin_id in list(ACTIVE_ADMINS):
        lines = [f"• {r['product']} — срок до {pd.to_datetime(r['expiry_date']).strftime('%d.%m.%Y')} ({int(r['qty'])} шт.)"
                 for _, r in due.iterrows()]
        if lines:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text="Упс! Кажется, через месяц истекает срок годности:\n" + "\n".join(lines)
                )
            except Exception:
                pass


async def job_tuesday_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Каждый вторник в 10:00 — напоминание про заявку."""
    for admin_id in list(ACTIVE_ADMINS):
        try:
            await context.bot.send_message(chat_id=admin_id, text="Алё? Пора закупаться!")
        except Exception:
            pass


# ================== РЕГИСТРАЦИЯ ХЕНДЛЕРОВ ==================
def build_app() -> Application:
    ensure_excel()
    app = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ROLE: [CallbackQueryHandler(cb_handler)],
            # Бармен
            B_CAT: [CallbackQueryHandler(cb_handler)],
            B_ITEM: [CallbackQueryHandler(cb_handler)],
            B_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, barmen_qty),
                    CallbackQueryHandler(cb_handler)],
            B_CONFIRM: [CallbackQueryHandler(cb_handler)],
            # Админ
            A_MENU: [CallbackQueryHandler(cb_handler)],
            A_STATS_MENU: [CallbackQueryHandler(cb_handler)],
            A_DODEP_MENU: [CallbackQueryHandler(cb_handler)],
            A_DODEP_SET_MODE: [CallbackQueryHandler(cb_handler)],
            A_DODEP_SET_CAT: [CallbackQueryHandler(cb_handler)],
            A_DODEP_SET_ITEM: [CallbackQueryHandler(cb_handler)],
            A_DODEP_SET_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, dodep_set_qty),
                              CallbackQueryHandler(cb_handler)],
            A_RECEIVE_MENU: [CallbackQueryHandler(cb_handler)],
            A_RECEIVE_PICK_ITEM: [CallbackQueryHandler(cb_handler)],
            A_RECEIVE_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_qty),
                            CallbackQueryHandler(cb_handler)],
            A_RECEIVE_NEW_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_name),
                                 CallbackQueryHandler(cb_handler)],
            A_RECEIVE_NEW_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_qty),
                                CallbackQueryHandler(cb_handler)],
            A_EXPIRY_PICK_ITEM: [CallbackQueryHandler(cb_handler)],
            A_EXPIRY_ENTER_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, expiry_enter_date),
                                  CallbackQueryHandler(cb_handler)],
        },
        fallbacks=[CommandHandler("start", start)],
        per_message=False,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("ping", ping))

    # Планировщик
    jq = app.job_queue
    # ежедневно в 09:00
    jq.run_daily(job_daily_expiry, time=dt.time(hour=9, minute=0, tzinfo=TZ))
    # каждый вторник в 10:00
    jq.run_daily(job_tuesday_reminder, time=dt.time(hour=10, minute=0, tzinfo=TZ), days=(1,))  # 0=Пн, 1=Вт,...

    return app


def main():
    app = build_app()
    print("✅ Бот запущен! Ctrl+C для остановки.")
    app.run_polling()


if __name__ == "__main__":
    main()
