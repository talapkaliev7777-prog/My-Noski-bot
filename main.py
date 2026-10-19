import asyncio
import http.server
import logging
import os
import socketserver
import sqlite3
import threading
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "8820849034:AAGM2Ms4tx7zIeeH8ar_pP1iy7GP6t-gqY4"
ADMIN_IDS = [1392043117]  # Твой ID
# ====================================================

# 1. Запускаем мини-веб-сервер для Render, чтобы он не отключал бесплатный тариф
PORT = int(os.environ.get("PORT", 10000))


class HealthCheckHandler(http.server.SimpleHTTPRequestHandler):

  def do_GET(self):
    self.send_response(200)
    self.end_headers()
    self.wfile.write(b"Bot is running 24/7!")


def run_web_server():
  with socketserver.TCPServer(("", PORT), HealthCheckHandler) as httpd:
    httpd.serve_forever()


web_thread = threading.Thread(target=run_web_server, daemon=True)
web_thread.start()

# 2. Запуск Telegram-бота
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class AddProductStates(StatesGroup):
  waiting_for_title = State()
  waiting_for_description = State()
  waiting_for_photo = State()
  waiting_for_sizes = State()
  waiting_for_stock = State()


def init_db():
  conn = sqlite3.connect("shop.db")
  cursor = conn.cursor()
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            description TEXT,
            photo_url TEXT,
            sizes TEXT,
            stock INTEGER
        )
    """)
  cursor.execute("SELECT COUNT(*) FROM products")
  if cursor.fetchone()[0] == 0:
    cursor.execute(
        """
            INSERT INTO products (title, description, photo_url, sizes, stock)
            VALUES (?, ?, ?, ?, ?)
        """,
        (
            "Носки Nike Classic",
            "Плотные белые носки из качественного хлопка.",
            "https://images.unsplash.com/photo-1585487000160-6ebcfceb0d03",
            "39-42, 43-45",
            5,
        ),
    )
  conn.commit()
  conn.close()


init_db()


def get_goods():
  conn = sqlite3.connect("shop.db")
  conn.row_factory = sqlite3.Row
  cursor = conn.cursor()
  cursor.execute("SELECT * FROM products")
  rows = cursor.fetchall()
  conn.close()
  return [dict(row) for row in rows]


def update_stock(item_id, new_stock):
  conn = sqlite3.connect("shop.db")
  cursor = conn.cursor()
  cursor.execute(
      "UPDATE products SET stock = ? WHERE id = ?", (new_stock, item_id)
  )
  conn.commit()
  conn.close()


def add_product_to_db(title, desc, photo, sizes, stock):
  conn = sqlite3.connect("shop.db")
  cursor = conn.cursor()
  cursor.execute(
      """
        INSERT INTO products (title, description, photo_url, sizes, stock)
        VALUES (?, ?, ?, ?, ?)
    """,
      (title, desc, photo, sizes, stock),
  )
  conn.commit()
  conn.close()


def delete_product_from_db(item_id):
  conn = sqlite3.connect("shop.db")
  cursor = conn.cursor()
  cursor.execute("DELETE FROM products WHERE id = ?", (item_id,))
  conn.commit()
  conn.close()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
  kb = InlineKeyboardBuilder()
  kb.button(text="🧦 Каталог носков", callback_data="open_catalog")
  if message.from_user.id in ADMIN_IDS:
    kb.button(text="⚙️ Админ-панель", callback_data="admin_menu")
  kb.adjust(1)
  await message.answer(
      f"Привет, {message.from_user.first_name}! Добро пожаловать в наш магазин"
      " носков!",
      reply_markup=kb.as_markup(),
  )


@dp.callback_query(F.data == "open_catalog")
async def show_catalog(callback: types.CallbackQuery):
  goods = get_goods()
  kb = InlineKeyboardBuilder()
  has_items = False
  for item in goods:
    if item["stock"] > 0:
      has_items = True
      kb.button(
          text=f"{item['title']} — {item['stock']} шт.",
          callback_data=f"item_{item['id']}",
      )
  kb.adjust(1)
  if not has_items:
    await callback.message.answer("К сожалению, сейчас все товары раскуплены!")
    await callback.answer()
    return
  await callback.message.answer(
      "Выберите позицию из каталога:", reply_markup=kb.as_markup()
  )
  await callback.answer()


@dp.callback_query(F.data.startswith("item_"))
async def show_item(callback: types.CallbackQuery):
  item_id = int(callback.data.split("_")[1])
  goods = get_goods()
  item = next((g for g in goods if g["id"] == item_id), None)
  if not item or item["stock"] <= 0:
    await callback.message.answer("Товар закончился!")
    await callback.answer()
    return
  text = (
      f"<b>{item['title']}</b>\n\n{item['description']}\n\n📏 Размеры:"
      f" {item['sizes']}\n📦 В наличии: {item['stock']} шт."
  )
  kb = InlineKeyboardBuilder()
  for sz in str(item["sizes"]).split(","):
    sz_clean = sz.strip()
    kb.button(
        text=f"Купить (Размер {sz_clean})",
        callback_data=f"buy_{item_id}_{sz_clean}",
    )
  kb.button(text="⬅️ Назад в каталог", callback_data="open_catalog")
  kb.adjust(1)
  if item.get("photo_url"):
    await callback.message.answer_photo(
        photo=item["photo_url"],
        caption=text,
        reply_markup=kb.as_markup(),
        parse_mode="HTML",
    )
  else:
    await callback.message.answer(
        text, reply_markup=kb.as_markup(), parse_mode="HTML"
    )
  await callback.answer()


@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: types.CallbackQuery):
  _, item_id, size = callback.data.split("_")
  item_id = int(item_id)
  goods = get_goods()
  item = next((g for g in goods if g["id"] == item_id), None)
  if not item or item["stock"] <= 0:
    await callback.message.answer("Извините, этот товар только что забрали!")
    await callback.answer()
    return
  current_stock = item["stock"]
  new_stock = current_stock - 1
  update_stock(item_id, new_stock)
  user = callback.from_user
  admin_text = (
      f"🎉 <b>НОВЫЙ ЗАКАЗ!</b>\n\n🛒 Товар: {item['title']}\n📏 Размер:"
      f" {size}\n👤 Покупатель: @{user.username or 'без_юзернейма'}"
      f" ({user.full_name})\n📉 Остаток уменьшен: с {current_stock} до"
      f" {new_stock} шт."
  )

  for admin_id in ADMIN_IDS:
    try:
      await bot.send_message(
          chat_id=admin_id, text=admin_text, parse_mode="HTML"
      )
    except Exception:
      pass

  await callback.message.answer(
      f"✅ Заказ принят!\n\nТовар: <b>{item['title']}</b> (Размер: {size})\nМенеджер"
      f" скоро свяжется с вами.",
      parse_mode="HTML",
  )
  await callback.answer()


@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
  if message.from_user.id not in ADMIN_IDS:
    return
  await show_admin_panel(message)


@dp.callback_query(F.data == "admin_menu")
async def callback_admin(callback: types.CallbackQuery):
  if callback.from_user.id not in ADMIN_IDS:
    await callback.answer("У вас нет доступа!", show_alert=True)
    return
  await show_admin_panel(callback.message, edit=True)
  await callback.answer()


async def show_admin_panel(message: types.Message, edit=False):
  kb = InlineKeyboardBuilder()
  kb.button(text="➕ Добавить товар", callback_data="admin_add")
  kb.button(text="📋 Управление товарами", callback_data="admin_list")
  kb.button(text="🏠 В главное меню", callback_data="open_catalog")
  kb.adjust(1)

  text = "⚙️ <b>Панель администратора</b>\n\nВыберите действие:"
  if edit:
    await message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
  else:
    await message.answer(text, reply_markup=kb.as_markup(), parse_mode="HTML")


@dp.callback_query(F.data == "admin_add")
async def admin_add_start(callback: types.CallbackQuery, state: FSMContext):
  if callback.from_user.id not in ADMIN_IDS:
    return
  await callback.message.answer(
      "📝 Введите <b>название</b> нового товара (например: <i>Носки Adidas</i>):",
      parse_mode="HTML",
  )
  await state.set_state(AddProductStates.waiting_for_title)
  await callback.answer()


@dp.message(AddProductStates.waiting_for_title)
async def process_title(message: types.Message, state: FSMContext):
  if message.from_user.id not in ADMIN_IDS:
    return
  await state.update_data(title=message.text)
  await message.answer(
      "📄 Теперь введите <b>описание</b> товара:", parse_mode="HTML"
  )
  await state.set_state(AddProductStates.waiting_for_description)


@dp.message(AddProductStates.waiting_for_description)
async def process_desc(message: types.Message, state: FSMContext):
  if message.from_user.id not in ADMIN_IDS:
    return
  await state.update_data(description=message.text)
  await message.answer(
      "🖼 Пришлите <b>ссылку на картинку</b> (или отправьте точку/пробел, если"
      " фото нет):",
      parse_mode="HTML",
  )
  await state.set_state(AddProductStates.waiting_for_photo)


@dp.message(AddProductStates.waiting_for_photo)
async def process_photo(message: types.Message, state: FSMContext):
  if message.from_user.id not in ADMIN_IDS:
    return
  photo = message.text.strip()
  if photo in [".", "-", "нет"]:
    photo = ""
  await state.update_data(photo_url=photo)
  await message.answer(
      "📏 Введите <b>размеры</b> через запятую (например: <i>38-40, 41-43</i>):",
      parse_mode="HTML",
  )
  await state.set_state(AddProductStates.waiting_for_sizes)


@dp.message(AddProductStates.waiting_for_sizes)
async def process_sizes(message: types.Message, state: FSMContext):
  if message.from_user.id not in ADMIN_IDS:
    return
  await state.update_data(sizes=message.text)
  await message.answer(
      "📦 Введите <b>количество штук</b> на складе (число, например: <i>10</i>):",
      parse_mode="HTML",
  )
  await state.set_state(AddProductStates.waiting_for_stock)


@dp.message(AddProductStates.waiting_for_stock)
async def process_stock(message: types.Message, state: FSMContext):
  if message.from_user.id not in ADMIN_IDS:
    return
  try:
    stock = int(message.text)
  except ValueError:
    await message.answer("⚠️ Введите целое число для количества!")
    return

  data = await state.get_data()
  add_product_to_db(
      data["title"],
      data["description"],
      data["photo_url"],
      data["sizes"],
      stock,
  )
  await state.clear()

  kb = InlineKeyboardBuilder()
  kb.button(text="⚙️ В админ-панель", callback_data="admin_menu")
  await message.answer(
      "✅ <b>Товар успешно добавлен в магазин!</b>",
      reply_markup=kb.as_markup(),
      parse_mode="HTML",
  )


@dp.callback_query(F.data == "admin_list")
async def admin_list_goods(callback: types.CallbackQuery):
  if callback.from_user.id not in ADMIN_IDS:
    return
  goods = get_goods()
  if not goods:
    await callback.message.edit_text(
        "📦 Список товаров пуст.",
        reply_markup=InlineKeyboardBuilder()
        .button(text="⬅️ Назад", callback_data="admin_menu")
        .as_markup(),
    )
    await callback.answer()
    return

  kb = InlineKeyboardBuilder()
  for item in goods:
    kb.button(
        text=f"🗑 Удалить: {item['title']} ({item['stock']} шт.)",
        callback_data=f"del_{item['id']}",
    )
  kb.button(text="⬅️ Назад в админку", callback_data="admin_menu")
  kb.adjust(1)
  await callback.message.edit_text(
      "📋 <b>Список товаров в базе:</b>\nНажмите на товар, чтобы удалить его:",
      reply_markup=kb.as_markup(),
      parse_mode="HTML",
  )
  await callback.answer()


@dp.callback_query(F.data.startswith("del_"))
async def admin_delete_item(callback: types.CallbackQuery):
  if callback.from_user.id not in ADMIN_IDS:
    return
  item_id = int(callback.data.split("_")[1])
  delete_product_from_db(item_id)
  await callback.answer("Товар удален!", show_alert=True)
  await admin_list_goods(callback)


async def main():
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
