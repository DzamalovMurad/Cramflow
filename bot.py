import asyncio
import logging
import sqlite3
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, FSInputFile,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import CommandStart, Command

# ══════════════════════════════════════════════════════════════
#  КОНФИГУРАЦИЯ
# ══════════════════════════════════════════════════════════════
TOKEN       = "8960573135:AAGNDzTdvtPS33u441smZGYp2KL4RRp3ZOU"
OWNER_ID    = 1829746409
SHOP_NAME   = "CramFlow — Цветы"
SHOP_PHONE  = "+7 (999) 000-00-00"
CHANNEL_URL = "https://t.me/ваш_канал"
SUPPORT_TG  = "@ваш_менеджер"

# Путь к фото приветствия — положи файл рядом с bot.py и укажи имя
WELCOME_PHOTO_PATH = "welcome.jpg"  # ← переименуй своё фото в welcome.jpg

PAYMENT_INFO = (
    "💳 <b>Оплата:</b>\n"
    "• Наличными курьеру\n"
    "• Сбер: <code>1234 5678 9012 3456</code> (Иван И.)\n"
    "• Тинькофф: <code>+7 (999) 000-00-00</code>"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
#  БАЗА ДАННЫХ
# ══════════════════════════════════════════════════════════════
DB_PATH = "shop.db"

def get_db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def db_init() -> None:
    with get_db() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS bouquets (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                name      TEXT    NOT NULL,
                desc      TEXT    NOT NULL,
                price     INTEGER NOT NULL,
                photo     TEXT    NOT NULL,
                active    INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS sales (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                name      TEXT    NOT NULL,
                desc      TEXT    NOT NULL,
                price     INTEGER NOT NULL,
                old_price INTEGER NOT NULL,
                photo     TEXT    NOT NULL,
                active    INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS orders (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER,
                username   TEXT,
                bouquets   TEXT,
                total      INTEGER,
                cust_name  TEXT,
                phone      TEXT,
                address    TEXT,
                comment    TEXT    DEFAULT '',
                status     TEXT    DEFAULT 'new',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS cart (
                user_id    INTEGER,
                item_id    INTEGER,
                source     TEXT,
                name       TEXT,
                price      INTEGER
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
        """)
    log.info("База данных инициализирована")

# ── helpers ──────────────────────────────────────────────────
def _setting_get(key: str) -> str | None:
    with get_db() as con:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

def _setting_set(key: str, value: str) -> None:
    with get_db() as con:
        con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))

# ── каталог ──────────────────────────────────────────────────
def bouquets_active():
    with get_db() as con:
        return con.execute("SELECT * FROM bouquets WHERE active=1 ORDER BY id").fetchall()

def bouquets_all():
    with get_db() as con:
        return con.execute("SELECT id,name,price,active FROM bouquets ORDER BY id").fetchall()

def sales_active():
    with get_db() as con:
        return con.execute("SELECT * FROM sales WHERE active=1 ORDER BY id").fetchall()

def sales_all():
    with get_db() as con:
        return con.execute("SELECT id,name,price,old_price,active FROM sales ORDER BY id").fetchall()

def search(query: str):
    q = f"%{query}%"
    with get_db() as con:
        b = con.execute(
            "SELECT *, 'bouquet' as source FROM bouquets WHERE active=1 AND (name LIKE ? OR desc LIKE ?)", (q, q)
        ).fetchall()
        s = con.execute(
            "SELECT *, 'sale' as source FROM sales WHERE active=1 AND (name LIKE ? OR desc LIKE ?)", (q, q)
        ).fetchall()
    return list(b) + list(s)

def bouquet_add(name, desc, price, photo):
    with get_db() as con:
        con.execute("INSERT INTO bouquets (name,desc,price,photo) VALUES (?,?,?,?)", (name, desc, price, photo))

def sale_add(name, desc, price, old_price, photo):
    with get_db() as con:
        con.execute("INSERT INTO sales (name,desc,price,old_price,photo) VALUES (?,?,?,?,?)", (name, desc, price, old_price, photo))

def bouquet_deactivate(bid: int):
    with get_db() as con:
        con.execute("UPDATE bouquets SET active=0 WHERE id=?", (bid,))

def sale_deactivate(sid: int):
    with get_db() as con:
        con.execute("UPDATE sales SET active=0 WHERE id=?", (sid,))

# ── корзина ──────────────────────────────────────────────────
def cart_add(user_id, item_id, source, name, price):
    with get_db() as con:
        con.execute("INSERT INTO cart VALUES (?,?,?,?,?)", (user_id, item_id, source, name, price))

def cart_get(user_id):
    with get_db() as con:
        return con.execute("SELECT * FROM cart WHERE user_id=?", (user_id,)).fetchall()

def cart_clear(user_id):
    with get_db() as con:
        con.execute("DELETE FROM cart WHERE user_id=?", (user_id,))

# ── заказы ───────────────────────────────────────────────────
def order_save(user_id, username, bouquets_str, total, cust_name, phone, address, comment) -> int:
    with get_db() as con:
        cur = con.execute(
            "INSERT INTO orders (user_id,username,bouquets,total,cust_name,phone,address,comment) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (user_id, username, bouquets_str, total, cust_name, phone, address, comment),
        )
        return cur.lastrowid

def orders_by_user(user_id):
    with get_db() as con:
        return con.execute(
            "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (user_id,)
        ).fetchall()

def orders_all(status: str | None = None):
    with get_db() as con:
        if status:
            return con.execute(
                "SELECT * FROM orders WHERE status=? ORDER BY id DESC LIMIT 30", (status,)
            ).fetchall()
        return con.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 30").fetchall()

def order_status_set(order_id: int, status: str):
    with get_db() as con:
        con.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))

STATUS = {
    "new":       "🆕 Новый",
    "confirmed": "✅ Подтверждён",
    "delivery":  "🚚 Доставляется",
    "done":      "🎉 Выполнен",
    "cancelled": "❌ Отменён",
}

# ══════════════════════════════════════════════════════════════
#  FSM
# ══════════════════════════════════════════════════════════════
class Order(StatesGroup):
    cust_name = State()
    phone     = State()
    address   = State()
    comment   = State()
    confirm   = State()

class AddBouquet(StatesGroup):
    name  = State()
    desc  = State()
    price = State()
    photo = State()

class AddSale(StatesGroup):
    name      = State()
    desc      = State()
    price     = State()
    old_price = State()
    photo     = State()

class SearchState(StatesGroup):
    query = State()

# ══════════════════════════════════════════════════════════════
#  КЛАВИАТУРЫ
# ══════════════════════════════════════════════════════════════
def ikb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))

def btn(text: str, data: str = None, url: str = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data, url=url)

def kb_main() -> InlineKeyboardMarkup:
    return ikb(
        [btn("💐 Каталог",    "cat_0"),      btn("🔥 Акции",      "sale_0")],
        [btn("🛒 Корзина",    "cart_show"),  btn("📋 Заказы",     "my_orders")],
        [btn("🔍 Поиск",      "search"),     btn("💬 Поддержка",  "support")],
        [btn("📢 Канал", url=CHANNEL_URL)],
    )

def kb_catalog_nav(idx: int, total: int, prefix: str) -> InlineKeyboardMarkup:
    nav = []
    if idx > 0:
        nav.append(btn("◀️", f"{prefix}_{idx-1}"))
    nav.append(btn(f"{idx+1} / {total}", "noop"))
    if idx < total - 1:
        nav.append(btn("▶️", f"{prefix}_{idx+1}"))
    return ikb(
        nav,
        [btn("🛒 В корзину", f"addcart_{prefix}_{idx}"), btn("⚡️ Купить сразу", f"buynow_{prefix}_{idx}")],
        [btn("🏠 Меню", "main_menu")],
    )

def kb_cart(has_items: bool) -> InlineKeyboardMarkup:
    if has_items:
        return ikb(
            [btn("✅ Оформить заказ", "order_cart")],
            [btn("🗑 Очистить корзину", "cart_clear")],
            [btn("🏠 Меню", "main_menu")],
        )
    return ikb([btn("💐 Перейти в каталог", "cat_0")], [btn("🏠 Меню", "main_menu")])

def kb_confirm() -> InlineKeyboardMarkup:
    return ikb(
        [btn("✅ Подтвердить", "do_confirm")],
        [btn("✏️ Изменить комментарий", "edit_comment")],
        [btn("❌ Отмена", "order_cancel")],
    )

def kb_phone() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться номером", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )

def kb_skip(cb: str) -> InlineKeyboardMarkup:
    return ikb([btn("⏭ Пропустить", cb)])

def kb_admin_main() -> InlineKeyboardMarkup:
    return ikb(
        [btn("💐 Букеты",       "a_bouquets"), btn("🔥 Скидки",    "a_sales")],
        [btn("📋 Новые заказы", "a_new"),      btn("📦 Все заказы", "a_all")],
        [btn("📊 Статистика",   "a_stats")],
    )

def kb_admin_section(section: str) -> InlineKeyboardMarkup:
    return ikb(
        [btn("➕ Добавить", f"a_add_{section}"), btn("📋 Список", f"a_list_{section}")],
        [btn("🗑 Удалить",  f"a_del_{section}"), btn("◀️ Назад",  "a_back")],
    )

def kb_order_status(order_id: int) -> InlineKeyboardMarkup:
    return ikb(
        [btn("✅ Подтвердить",  f"os_{order_id}_confirmed"), btn("🚚 Доставляется", f"os_{order_id}_delivery")],
        [btn("🎉 Выполнен",     f"os_{order_id}_done"),      btn("❌ Отменить",     f"os_{order_id}_cancelled")],
    )

def kb_cancel_admin() -> InlineKeyboardMarkup:
    return ikb([btn("❌ Отмена", "a_cancel")])

# ══════════════════════════════════════════════════════════════
#  УТИЛИТЫ
# ══════════════════════════════════════════════════════════════
async def safe_delete(msg: Message) -> None:
    try:
        await msg.delete()
    except Exception:
        pass

async def edit_or_answer(msg: Message, text: str, **kwargs) -> None:
    try:
        await msg.edit_text(text, **kwargs)
    except Exception:
        await msg.answer(text, **kwargs)

# ══════════════════════════════════════════════════════════════
#  BOT / DISPATCHER
# ══════════════════════════════════════════════════════════════
bot = Bot(token=TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

def is_admin(uid: int) -> bool:
    return uid == OWNER_ID

# ══════════════════════════════════════════════════════════════
#  СТАРТ
# ══════════════════════════════════════════════════════════════
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    name = message.from_user.first_name or "друг"

    text = (
        f"👋 Привет, <b>{name}</b>!\n\n"
        f"Вас приветствует <b>CramFlow</b> 🌹\n"
        f"Оптовый и розничный магазин цветов.\n\n"
        f"Присылай бюджет, настроение или фото — соберём букет за 15 минут. Без лишних вопросов.\n\n"
        f"🕐 Доставка по Москве от 1 часа — в офис, домой, на свидание\n"
        f"🌿 Гарантия свежести 5 дней — если завянут раньше, сделаем новый бесплатно\n"
        f"📦 Везём в термосумках, букет в оазисе — приедет как из витрины\n"
        f"📅 Работаем каждый день, включая праздники\n\n"
        f"<i>Выбери раздел 👇</i>"
    )

    # Пытаемся отправить фото. Сначала проверяем кешированный file_id,
    # иначе грузим файл с диска и кешируем id на будущее.
    file_id = _setting_get("welcome_photo_id")
    photo_path = Path(WELCOME_PHOTO_PATH)

    if file_id:
        try:
            await message.answer_photo(photo=file_id, caption=text,
                reply_markup=ikb([btn("🌸 Открыть магазин", "open_shop")]),
                parse_mode="HTML")
            return
        except Exception:
            # file_id устарел — сбросим и попробуем переслать файл
            _setting_set("welcome_photo_id", "")
            file_id = None

    if photo_path.exists():
        sent = await message.answer_photo(
            photo=FSInputFile(photo_path),
            caption=text,
            reply_markup=ikb([btn("🌸 Открыть магазин", "open_shop")]),
            parse_mode="HTML",
        )
        # Сохраняем file_id чтобы не загружать файл каждый раз
        new_id = sent.photo[-1].file_id
        _setting_set("welcome_photo_id", new_id)
        log.info(f"Welcome photo uploaded, file_id cached: {new_id}")
    else:
        # Фото не найдено — шлём просто текст
        log.warning(f"Файл {WELCOME_PHOTO_PATH} не найден, отправляем текст")
        await message.answer(text, reply_markup=kb_main(), parse_mode="HTML")

@dp.callback_query(F.data == "open_shop")
async def open_shop(call: CallbackQuery) -> None:
    await call.message.edit_caption(
        caption="🌸 <b>Главное меню</b>\n\nВыбери раздел:",
        reply_markup=kb_main(), parse_mode="HTML",
    )

@dp.callback_query(F.data == "main_menu")
async def go_main(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_or_answer(call.message, "🌸 <b>Главное меню</b>",
                         reply_markup=kb_main(), parse_mode="HTML")

@dp.callback_query(F.data == "noop")
async def noop(call: CallbackQuery) -> None:
    await call.answer()

# ══════════════════════════════════════════════════════════════
#  КАТАЛОГ
# ══════════════════════════════════════════════════════════════
@dp.callback_query(F.data.startswith("cat_"))
async def show_bouquet(call: CallbackQuery) -> None:
    items = bouquets_active()
    if not items:
        await call.answer("😔 Букеты ещё не добавлены", show_alert=True)
        return
    idx = max(0, min(int(call.data.split("_")[1]), len(items) - 1))
    it  = items[idx]
    cap = f"💐 <b>{it['name']}</b>\n\n📝 {it['desc']}\n\n💰 <b>{it['price']:,} ₽</b>"
    await safe_delete(call.message)
    await call.message.answer_photo(
        photo=it["photo"], caption=cap,
        reply_markup=kb_catalog_nav(idx, len(items), "cat"),
        parse_mode="HTML",
    )
    await call.answer()

@dp.callback_query(F.data.startswith("sale_"))
async def show_sale(call: CallbackQuery) -> None:
    items = sales_active()
    if not items:
        await call.answer("😔 Акций пока нет", show_alert=True)
        return
    idx = max(0, min(int(call.data.split("_")[1]), len(items) - 1))
    it  = items[idx]
    cap = (
        f"🔥 <b>{it['name']}</b>\n\n📝 {it['desc']}\n\n"
        f"<s>{it['old_price']:,} ₽</s>  →  💰 <b>{it['price']:,} ₽</b>\n"
        f"🎁 Экономия: <b>{it['old_price'] - it['price']:,} ₽</b>"
    )
    await safe_delete(call.message)
    await call.message.answer_photo(
        photo=it["photo"], caption=cap,
        reply_markup=kb_catalog_nav(idx, len(items), "sale"),
        parse_mode="HTML",
    )
    await call.answer()

# ══════════════════════════════════════════════════════════════
#  КОРЗИНА
# ══════════════════════════════════════════════════════════════
@dp.callback_query(F.data.startswith("addcart_"))
async def add_to_cart(call: CallbackQuery) -> None:
    _, prefix, idx_s = call.data.split("_", 2)
    items = bouquets_active() if prefix == "cat" else sales_active()
    it    = items[int(idx_s)]
    cart_add(call.from_user.id, it["id"], prefix, it["name"], it["price"])
    await call.answer(f"✅ «{it['name']}» добавлен в корзину!")

@dp.callback_query(F.data == "cart_show")
async def show_cart(call: CallbackQuery) -> None:
    items = cart_get(call.from_user.id)
    if not items:
        text = "🛒 <b>Корзина пуста</b>\n\nДобавь букеты из каталога!"
    else:
        total = sum(i["price"] for i in items)
        lines = "\n".join(f"• {i['name']} — {i['price']:,} ₽" for i in items)
        text  = f"🛒 <b>Корзина:</b>\n\n{lines}\n\n💰 <b>Итого: {total:,} ₽</b>"
    await edit_or_answer(call.message, text, reply_markup=kb_cart(bool(items)), parse_mode="HTML")

@dp.callback_query(F.data == "cart_clear")
async def clear_cart(call: CallbackQuery) -> None:
    cart_clear(call.from_user.id)
    await call.message.edit_text(
        "🗑 Корзина очищена.",
        reply_markup=ikb([btn("💐 В каталог", "cat_0")], [btn("🏠 Меню", "main_menu")]),
    )

# ══════════════════════════════════════════════════════════════
#  ПОИСК
# ══════════════════════════════════════════════════════════════
@dp.callback_query(F.data == "search")
async def start_search(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SearchState.query)
    await edit_or_answer(
        call.message,
        "🔍 <b>Поиск</b>\n\nВведи название или состав:\n<i>розы, тюльпаны, свадьба...</i>",
        reply_markup=ikb([btn("◀️ Назад", "main_menu")]),
        parse_mode="HTML",
    )

@dp.message(SearchState.query)
async def do_search(message: Message, state: FSMContext) -> None:
    await state.clear()
    results = search(message.text)
    if not results:
        await message.answer(
            f"😔 По запросу «{message.text}» ничего нет.",
            reply_markup=ikb([btn("🔍 Попробовать снова", "search")], [btn("💐 Весь каталог", "cat_0")]),
        )
        return
    buttons = []
    for r in results[:8]:
        icon = "🔥" if r["source"] == "sale" else "💐"
        cb   = "sale_0" if r["source"] == "sale" else "cat_0"
        buttons.append([btn(f"{icon} {r['name']} — {r['price']:,} ₽", cb)])
    buttons.append([btn("🏠 Меню", "main_menu")])
    await message.answer(f"🔍 Результаты по «{message.text}»:", reply_markup=ikb(*buttons))

# ══════════════════════════════════════════════════════════════
#  ПОДДЕРЖКА / МОИ ЗАКАЗЫ
# ══════════════════════════════════════════════════════════════
@dp.callback_query(F.data == "support")
async def support(call: CallbackQuery) -> None:
    await edit_or_answer(
        call.message,
        f"💬 <b>Поддержка</b>\n\nМенеджер: {SUPPORT_TG}\n📞 {SHOP_PHONE}\n\n⏰ 9:00 – 21:00",
        reply_markup=ikb([btn("🏠 Меню", "main_menu")]),
        parse_mode="HTML",
    )

@dp.callback_query(F.data == "my_orders")
async def my_orders(call: CallbackQuery) -> None:
    rows = orders_by_user(call.from_user.id)
    if not rows:
        text = "📋 <b>Мои заказы</b>\n\nЗаказов пока нет 🌸"
    else:
        text = "📋 <b>Мои заказы:</b>\n\n"
        for o in rows:
            st   = STATUS.get(o["status"], o["status"])
            date = str(o["created_at"])[:10]
            text += f"#{o['id']} от {date}  |  {st}\n💐 {o['bouquets']}  |  💰 {o['total']:,} ₽\n\n"
    await edit_or_answer(call.message, text, reply_markup=ikb([btn("🏠 Меню", "main_menu")]), parse_mode="HTML")

# ══════════════════════════════════════════════════════════════
#  ОФОРМЛЕНИЕ ЗАКАЗА
# ══════════════════════════════════════════════════════════════
async def _begin_order(target: CallbackQuery | Message, state: FSMContext,
                       items_str: str, total: int) -> None:
    await state.update_data(order_items=items_str, order_total=total)
    await state.set_state(Order.cust_name)
    text = "📝 <b>Оформление заказа</b>\n\n<b>Шаг 1 из 4</b> — Как вас зовут?"
    if isinstance(target, CallbackQuery):
        await edit_or_answer(target.message, text, parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=ReplyKeyboardRemove())

@dp.callback_query(F.data == "order_cart")
async def order_from_cart(call: CallbackQuery, state: FSMContext) -> None:
    items = cart_get(call.from_user.id)
    if not items:
        await call.answer("Корзина пуста!", show_alert=True)
        return
    await _begin_order(call, state,
        ", ".join(i["name"] for i in items),
        sum(i["price"] for i in items))

@dp.callback_query(F.data.startswith("buynow_"))
async def buy_now(call: CallbackQuery, state: FSMContext) -> None:
    _, prefix, idx_s = call.data.split("_", 2)
    items = bouquets_active() if prefix == "cat" else sales_active()
    it    = items[int(idx_s)]
    await _begin_order(call, state, it["name"], it["price"])

@dp.message(Order.cust_name)
async def step_name(message: Message, state: FSMContext) -> None:
    await state.update_data(cust_name=message.text)
    await state.set_state(Order.phone)
    await message.answer(
        "📱 <b>Шаг 2 из 4</b> — Номер телефона:\n<i>Нажмите кнопку или напишите вручную</i>",
        reply_markup=kb_phone(), parse_mode="HTML",
    )

@dp.message(Order.phone, F.contact)
async def step_phone_contact(message: Message, state: FSMContext) -> None:
    await state.update_data(phone=message.contact.phone_number)
    await state.set_state(Order.address)
    await message.answer("🏠 <b>Шаг 3 из 4</b> — Адрес доставки:",
                         reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")

@dp.message(Order.phone)
async def step_phone_text(message: Message, state: FSMContext) -> None:
    await state.update_data(phone=message.text)
    await state.set_state(Order.address)
    await message.answer("🏠 <b>Шаг 3 из 4</b> — Адрес доставки:",
                         reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")

@dp.message(Order.address)
async def step_address(message: Message, state: FSMContext) -> None:
    await state.update_data(address=message.text)
    await state.set_state(Order.comment)
    await message.answer(
        "💬 <b>Шаг 4 из 4</b> — Пожелания к заказу?\n"
        "<i>Например: открытка, позвонить за час, без ленты</i>\n\n"
        "Или нажмите «Пропустить»",
        reply_markup=kb_skip("skip_comment"), parse_mode="HTML",
    )

@dp.callback_query(F.data == "skip_comment", Order.comment)
async def skip_comment(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(comment="")
    await call.answer()
    await _show_confirm(call.message, state, edit=True)

@dp.message(Order.comment)
async def step_comment(message: Message, state: FSMContext) -> None:
    await state.update_data(comment=message.text)
    await _show_confirm(message, state, edit=False)

async def _show_confirm(target: Message, state: FSMContext, edit: bool) -> None:
    data    = await state.get_data()
    await state.set_state(Order.confirm)
    comment = f"\n💬 Пожелания: {data['comment']}" if data.get("comment") else ""
    text = (
        f"📋 <b>Проверьте заказ:</b>\n\n"
        f"💐 {data.get('order_items', '—')}\n"
        f"💰 Сумма: <b>{data.get('order_total', 0):,} ₽</b>\n\n"
        f"👤 {data['cust_name']}\n"
        f"📱 {data['phone']}\n"
        f"🏠 {data['address']}"
        f"{comment}\n\n"
        f"{PAYMENT_INFO}"
    )
    kb = kb_confirm()
    if edit:
        await edit_or_answer(target, text, reply_markup=kb, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "edit_comment", Order.confirm)
async def edit_comment(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Order.comment)
    await call.message.edit_text("💬 Введите пожелания:", reply_markup=kb_skip("skip_comment"))

@dp.callback_query(F.data == "do_confirm", Order.confirm)
async def do_confirm(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    cart_clear(call.from_user.id)

    order_id = order_save(
        call.from_user.id,
        call.from_user.username or "—",
        data.get("order_items", "—"),
        data.get("order_total", 0),
        data["cust_name"], data["phone"], data["address"],
        data.get("comment", ""),
    )
    log.info(f"Новый заказ #{order_id} от @{call.from_user.username}")

    # Уведомление владельцу с кнопками статуса
    comment_line = f"\n💬 {data['comment']}" if data.get("comment") else ""
    owner_text = (
        f"🛎 <b>НОВЫЙ ЗАКАЗ #{order_id}</b>\n\n"
        f"💐 {data.get('order_items', '—')}\n"
        f"💰 {data.get('order_total', 0):,} ₽"
        f"{comment_line}\n\n"
        f"👤 {data['cust_name']}\n"
        f"📱 {data['phone']}\n"
        f"🏠 {data['address']}\n\n"
        f"TG: @{call.from_user.username or '—'} | <code>{call.from_user.id}</code>"
    )
    try:
        await bot.send_message(OWNER_ID, owner_text,
                               parse_mode="HTML", reply_markup=kb_order_status(order_id))
    except Exception as e:
        log.warning(f"Не удалось отправить уведомление владельцу: {e}")

    await call.message.edit_text(
        f"✅ <b>Заказ #{order_id} принят!</b>\n\n"
        "Свяжемся с вами в ближайшее время.\n\n"
        f"{PAYMENT_INFO}\n\n"
        "Спасибо, что выбрали нас! 🌸",
        parse_mode="HTML",
        reply_markup=ikb([btn("📋 Мои заказы", "my_orders")], [btn("🏠 Меню", "main_menu")]),
    )

@dp.callback_query(F.data == "order_cancel")
async def order_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text("❌ Заказ отменён.",
                                  reply_markup=ikb([btn("🏠 Меню", "main_menu")]))

@dp.callback_query(F.data.startswith("os_"))
async def order_status_change(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        return
    _, oid, status = call.data.split("_", 2)
    order_id = int(oid)
    order_status_set(order_id, status)
    label = STATUS.get(status, status)
    try:
        await call.message.edit_text(
            call.message.text + f"\n\n<b>Статус → {label}</b>",
            parse_mode="HTML", reply_markup=kb_order_status(order_id),
        )
    except Exception:
        pass
    await call.answer(f"Заказ #{order_id}: {label}", show_alert=True)

# ══════════════════════════════════════════════════════════════
#  АДМИН-ПАНЕЛЬ
# ══════════════════════════════════════════════════════════════
@dp.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("⛔️ Нет доступа.")
        return
    await state.clear()
    await message.answer(
        "🔧 <b>Панель управления</b>\n\n"
        "• Добавляй и удаляй позиции каталога\n"
        "• Смотри заказы и меняй статусы\n"
        "• Следи за статистикой\n\n"
        "<i>При каждом новом заказе придёт уведомление с кнопками прямо сюда.</i>",
        reply_markup=kb_admin_main(), parse_mode="HTML",
    )

@dp.callback_query(F.data == "a_back")
async def a_back(call: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(call.from_user.id): return
    await state.clear()
    await call.message.edit_text("🔧 <b>Панель управления</b>",
                                  reply_markup=kb_admin_main(), parse_mode="HTML")

@dp.callback_query(F.data == "a_cancel")
async def a_cancel(call: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(call.from_user.id): return
    await state.clear()
    await call.message.edit_text("🔧 <b>Панель управления</b>",
                                  reply_markup=kb_admin_main(), parse_mode="HTML")

@dp.callback_query(F.data == "a_bouquets")
async def a_bouquets(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id): return
    await call.message.edit_text("💐 <b>Управление букетами</b>",
                                  reply_markup=kb_admin_section("bouquet"), parse_mode="HTML")

@dp.callback_query(F.data == "a_sales")
async def a_sales(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id): return
    await call.message.edit_text("🔥 <b>Управление скидками</b>",
                                  reply_markup=kb_admin_section("sale"), parse_mode="HTML")

# ── Списки ───────────────────────────────────────────────────
@dp.callback_query(F.data == "a_list_bouquet")
async def a_list_bq(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id): return
    rows = bouquets_all()
    if not rows:
        await call.answer("Букетов нет", show_alert=True); return
    text = "📋 <b>Все букеты:</b>\n\n" + "\n".join(
        f"{'✅' if r['active'] else '❌'}  ID{r['id']} — {r['name']} — {r['price']:,} ₽" for r in rows)
    await call.message.edit_text(text, reply_markup=kb_admin_section("bouquet"), parse_mode="HTML")

@dp.callback_query(F.data == "a_list_sale")
async def a_list_sl(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id): return
    rows = sales_all()
    if not rows:
        await call.answer("Акций нет", show_alert=True); return
    text = "📋 <b>Все акции:</b>\n\n" + "\n".join(
        f"{'✅' if r['active'] else '❌'}  ID{r['id']} — {r['name']} — {r['price']:,} ₽ (было {r['old_price']:,} ₽)"
        for r in rows)
    await call.message.edit_text(text, reply_markup=kb_admin_section("sale"), parse_mode="HTML")

# ── Добавить букет ───────────────────────────────────────────
@dp.callback_query(F.data == "a_add_bouquet")
async def a_add_bq_start(call: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(call.from_user.id): return
    await state.set_state(AddBouquet.name)
    await call.message.edit_text("➕ <b>Добавить букет</b>\n\n<b>1/4</b> — Название:",
                                  reply_markup=kb_cancel_admin(), parse_mode="HTML")

@dp.message(AddBouquet.name)
async def ab_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text)
    await state.set_state(AddBouquet.desc)
    await message.answer("📝 <b>2/4</b> — Описание (состав, стебли):",
                         reply_markup=kb_cancel_admin(), parse_mode="HTML")

@dp.message(AddBouquet.desc)
async def ab_desc(message: Message, state: FSMContext) -> None:
    await state.update_data(desc=message.text)
    await state.set_state(AddBouquet.price)
    await message.answer("💰 <b>3/4</b> — Цена (только цифры, ₽):",
                         reply_markup=kb_cancel_admin(), parse_mode="HTML")

@dp.message(AddBouquet.price)
async def ab_price(message: Message, state: FSMContext) -> None:
    if not message.text.isdigit():
        await message.answer("❌ Только цифры. Например: 2500"); return
    await state.update_data(price=int(message.text))
    await state.set_state(AddBouquet.photo)
    await message.answer("📸 <b>4/4</b> — Отправь фото или ссылку (https://...):",
                         reply_markup=kb_cancel_admin(), parse_mode="HTML")

@dp.message(AddBouquet.photo)
async def ab_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if message.photo:
        photo = message.photo[-1].file_id
    elif message.text and message.text.startswith("http"):
        photo = message.text
    else:
        await message.answer("❌ Нужно фото или ссылка https://..."); return
    bouquet_add(data["name"], data["desc"], data["price"], photo)
    await state.clear()
    await message.answer(f"✅ Букет <b>«{data['name']}»</b> добавлен в каталог!",
                         reply_markup=kb_admin_section("bouquet"), parse_mode="HTML")

# ── Добавить акцию ───────────────────────────────────────────
@dp.callback_query(F.data == "a_add_sale")
async def a_add_sl_start(call: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(call.from_user.id): return
    await state.set_state(AddSale.name)
    await call.message.edit_text("➕ <b>Добавить акцию</b>\n\n<b>1/5</b> — Название:",
                                  reply_markup=kb_cancel_admin(), parse_mode="HTML")

@dp.message(AddSale.name)
async def as_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text)
    await state.set_state(AddSale.desc)
    await message.answer("📝 <b>2/5</b> — Описание:", reply_markup=kb_cancel_admin(), parse_mode="HTML")

@dp.message(AddSale.desc)
async def as_desc(message: Message, state: FSMContext) -> None:
    await state.update_data(desc=message.text)
    await state.set_state(AddSale.price)
    await message.answer("💰 <b>3/5</b> — Цена СО скидкой:",
                         reply_markup=kb_cancel_admin(), parse_mode="HTML")

@dp.message(AddSale.price)
async def as_price(message: Message, state: FSMContext) -> None:
    if not message.text.isdigit():
        await message.answer("❌ Только цифры"); return
    await state.update_data(price=int(message.text))
    await state.set_state(AddSale.old_price)
    await message.answer("💰 <b>4/5</b> — Старая цена БЕЗ скидки:\n<i>Будет зачёркнута</i>",
                         reply_markup=kb_cancel_admin(), parse_mode="HTML")

@dp.message(AddSale.old_price)
async def as_old_price(message: Message, state: FSMContext) -> None:
    if not message.text.isdigit():
        await message.answer("❌ Только цифры"); return
    await state.update_data(old_price=int(message.text))
    await state.set_state(AddSale.photo)
    await message.answer("📸 <b>5/5</b> — Фото или ссылка:",
                         reply_markup=kb_cancel_admin(), parse_mode="HTML")

@dp.message(AddSale.photo)
async def as_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if message.photo:
        photo = message.photo[-1].file_id
    elif message.text and message.text.startswith("http"):
        photo = message.text
    else:
        await message.answer("❌ Нужно фото или ссылка"); return
    sale_add(data["name"], data["desc"], data["price"], data["old_price"], photo)
    await state.clear()
    await message.answer(f"✅ Акция <b>«{data['name']}»</b> добавлена!",
                         reply_markup=kb_admin_section("sale"), parse_mode="HTML")

# ── Удалить ───────────────────────────────────────────────────
@dp.callback_query(F.data == "a_del_bouquet")
async def a_del_bq(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id): return
    rows = [r for r in bouquets_all() if r["active"]]
    if not rows:
        await call.answer("Нечего удалять", show_alert=True); return
    btns = [[btn(f"🗑 {r['name']} — {r['price']:,} ₽", f"delbq_{r['id']}")] for r in rows]
    btns.append([btn("◀️ Назад", "a_bouquets")])
    await call.message.edit_text("🗑 Выберите букет:", reply_markup=ikb(*btns))

@dp.callback_query(F.data.startswith("delbq_"))
async def do_del_bq(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id): return
    bouquet_deactivate(int(call.data.split("_")[1]))
    await call.answer("✅ Убран из каталога")
    await call.message.edit_text("💐 <b>Управление букетами</b>",
                                  reply_markup=kb_admin_section("bouquet"), parse_mode="HTML")

@dp.callback_query(F.data == "a_del_sale")
async def a_del_sl(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id): return
    rows = [r for r in sales_all() if r["active"]]
    if not rows:
        await call.answer("Нечего удалять", show_alert=True); return
    btns = [[btn(f"🗑 {r['name']} — {r['price']:,} ₽", f"delsl_{r['id']}")] for r in rows]
    btns.append([btn("◀️ Назад", "a_sales")])
    await call.message.edit_text("🗑 Выберите акцию:", reply_markup=ikb(*btns))

@dp.callback_query(F.data.startswith("delsl_"))
async def do_del_sl(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id): return
    sale_deactivate(int(call.data.split("_")[1]))
    await call.answer("✅ Убрана")
    await call.message.edit_text("🔥 <b>Управление скидками</b>",
                                  reply_markup=kb_admin_section("sale"), parse_mode="HTML")

# ── Заказы ────────────────────────────────────────────────────
async def _show_orders(call: CallbackQuery, rows, title: str) -> None:
    if not rows:
        await call.answer("Заказов нет", show_alert=True); return
    text = f"📋 <b>{title}:</b>\n\n"
    for o in rows[:20]:
        st      = STATUS.get(o["status"], o["status"])
        date    = str(o["created_at"])[:16]
        comment = f"\n   💬 {o['comment']}" if o["comment"] else ""
        text += (
            f"<b>#{o['id']}</b>  {st}  {date}\n"
            f"   💐 {o['bouquets']}  |  💰 {o['total']:,} ₽\n"
            f"   👤 {o['cust_name']}  |  📱 {o['phone']}\n"
            f"   🏠 {o['address']}"
            f"{comment}\n\n"
        )
    await edit_or_answer(call.message, text[:4000],
                         reply_markup=ikb([btn("◀️ Назад", "a_back")]), parse_mode="HTML")

@dp.callback_query(F.data == "a_new")
async def a_orders_new(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id): return
    await _show_orders(call, orders_all("new"), "Новые заказы")

@dp.callback_query(F.data == "a_all")
async def a_orders_all(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id): return
    await _show_orders(call, orders_all(), "Все заказы")

# ── Статистика ────────────────────────────────────────────────
@dp.callback_query(F.data == "a_stats")
async def a_stats(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id): return
    with get_db() as con:
        total   = con.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"]
        revenue = con.execute("SELECT COALESCE(SUM(total),0) s FROM orders").fetchone()["s"]
        new_cnt = con.execute("SELECT COUNT(*) c FROM orders WHERE status='new'").fetchone()["c"]
        done    = con.execute("SELECT COUNT(*) c FROM orders WHERE status='done'").fetchone()["c"]
        bq_cnt  = con.execute("SELECT COUNT(*) c FROM bouquets WHERE active=1").fetchone()["c"]
        sl_cnt  = con.execute("SELECT COUNT(*) c FROM sales WHERE active=1").fetchone()["c"]
    text = (
        f"📊 <b>Статистика</b>\n\n"
        f"📦 Всего заказов: <b>{total}</b>\n"
        f"🆕 Новых: <b>{new_cnt}</b>\n"
        f"🎉 Выполнено: <b>{done}</b>\n"
        f"💰 Выручка: <b>{revenue:,} ₽</b>\n\n"
        f"💐 Букетов в каталоге: <b>{bq_cnt}</b>\n"
        f"🔥 Активных акций: <b>{sl_cnt}</b>"
    )
    await call.message.edit_text(text,
                                  reply_markup=ikb([btn("◀️ Назад", "a_back")]), parse_mode="HTML")

# ══════════════════════════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════════════════════════
async def main() -> None:
    db_init()
    log.info("Запуск бота...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
