import asyncio
import logging
import json
import requests
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
import google.generativeai as genai

# === 1. НАСТРОЙКИ ===
API_TOKEN = '8343139252:AAFSsBJNTpEerQnVincehL25Cwg2EnrDgtw'
GEMINI_KEY = 'AIzaSyC4OW3i6r9D4zXpzJ1mU1m8-Qs3d1UfJrs'
FIREBASE_URL = "https://ashana-29903-default-rtdb.firebaseio.com"

# Секретные коды для входа
ACCESS_CODES = {
    "teacher": "TEACHER777",
    "5ә": "5A123", "6ә": "6A123", "7ә": "7A123", 
    "8ә": "8A123", "9ә": "9A123", "10ә": "10A123", "11ә": "11A123"
}

# Инициализация Gemini
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

bot = Bot(token=API_TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# Локальная база пользователей (в продакшене лучше Firebase)
user_data = {}

# === 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===

def is_canteen_open():
    """Проверяет статус столовой в Firebase (кнопка Закрыть/Открыть)"""
    try:
        res = requests.get(f"{FIREBASE_URL}/settings/isClosed.json")
        return not res.json() # Если True (закрыто), вернет False
    except:
        return True # По умолчанию открыто

async def get_ai_response(text, context=""):
    """Простой чат с ИИ"""
    try:
        prompt = f"Ты помощник в школьной столовой GastronomIQ. Контекст: {context}. Вопрос: {text}. Отвечай коротко. и работай по базому"
        response = model.generate_content(prompt)
        return response.text
    except:
        return "🤖 Извини, я немного завис. Давай лучше сделаем заказ!"

# === 3. ОБРАБОТКА КОМАНД ===

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Я Ученик 🎒", callback_data="role_student"))
    builder.row(InlineKeyboardButton(text="Я Учитель 👩‍🏫", callback_data="role_teacher"))
    
    await message.answer("Приятного аппетита! Выбери свою роль:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("role_"))
async def set_role(callback: types.CallbackQuery):
    role = callback.data.split("_")[1]
    user_data[callback.from_user.id] = {"role": role, "auth": False}
    
    if role == "teacher":
        await callback.message.answer("Введите секретный код учителя:")
    else:
        builder = InlineKeyboardBuilder()
        classes = ["5ә", "6ә", "7ә", "8ә", "9ә", "10ә", "11ә"]
        for cl in classes:
            builder.add(InlineKeyboardButton(text=cl, callback_data=f"class_{cl}"))
        builder.adjust(2)
        await callback.message.answer("Выбери свой класс:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("class_"))
async def set_class(callback: types.CallbackQuery):
    u_class = callback.data.split("_")[1]
    user_data[callback.from_user.id]["class"] = u_class
    await callback.message.answer(f"Введите код доступа для {u_class}:")

# === 4. ОБРАБОТКА ТЕКСТА И ФОТО ===

@dp.message(F.photo)
async def handle_menu_photo(message: types.Message):
    """Распознавание меню по фотографии (только для админов/учителей)"""
    if user_data.get(message.from_user.id, {}).get("role") != "teacher":
        return

    msg = await message.answer("🔍 ИИ анализирует фото меню...")
    
    # Скачиваем фото
    photo = await bot.get_file(message.photo[-1].file_id)
    photo_bytes = await bot.download_file(photo.file_path)
    
    try:
        # Отправляем в Gemini
        img = {"mime_type": "image/jpeg", "data": photo_bytes.getvalue()}
        response = model.generate_content([
            "Найди на фото блюда и цены. Верни только JSON список типа: "
            "[{\"name\": \"Плов\", \"price\": 500}]", img
        ])
        
        # Чистим ответ и сохраняем в Firebase
        menu_json = response.text.replace("```json", "").replace("```", "").strip()
        requests.put(f"{FIREBASE_URL}/menu_today.json", data=menu_json)
        
        await msg.edit_text("✅ Меню успешно обновлено и доступно ученикам!")
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка распознавания: {e}")

@dp.message()
async def main_handler(message: types.Message):
    uid = message.from_user.id
    if uid not in user_data: return

    # Проверка кода доступа
    if not user_data[uid]["auth"]:
        correct_code = ACCESS_CODES.get("teacher") if user_data[uid]["role"] == "teacher" else ACCESS_CODES.get(user_data[uid].get("class"))
        if message.text == correct_code:
            user_data[uid]["auth"] = True
            await message.answer("✅ Доступ открыт!")
            await show_main_menu(message)
        else:
            await message.answer("❌ Неверный код.")
        return

    # Если авторизован — общаемся с ИИ
    response = await get_ai_response(message.text, f"Роль: {user_data[uid].get('class', 'Учитель')}")
    await message.answer(response)

async def show_main_menu(message):
    web_app = WebAppInfo(url="https://clck.ru/3ErM6B") # Твоя ссылка на интерфейс меню
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍱 Открыть Меню", web_app=web_app)]
    ])
    await message.answer("Нажми кнопку ниже, чтобы выбрать еду:", reply_markup=kb)

# === 5. ПРИЕМ ЗАКАЗА ИЗ WEB APP ===

@dp.message(F.web_app_data)
async def process_order(message: types.Message):
    # 1. Сначала проверяем, не закрыта ли столовая в админке
    if not is_canteen_open():
        await message.answer("🛑 Столовая уже закрыта! Заказы больше не принимаются.")
        return

    try:
        data = json.loads(message.web_app_data.data)
        uid = message.from_user.id
        
        order = {
            "user": message.from_user.full_name,
            "class": user_data[uid].get("class", "Учитель"),
            "item": data["item"],
            "price": data["price"],
            "time": datetime.now().strftime("%H:%M")
        }
        
        # Отправляем в Firebase
        requests.post(f"{FIREBASE_URL}/orders.json", json=order)
        await message.answer(f"✅ Заказ принят: {data['item']} ({data['price']} тг). Оплатите через Kaspi!")
        
    except Exception as e:
        await message.answer("❌ Ошибка при оформлении заказа.")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())