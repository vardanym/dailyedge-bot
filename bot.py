import logging
import os
import aiohttp
from datetime import datetime
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)

# ─── НАСТРОЙКИ ───────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8956099025:AAEPr69Cc7wxGjXV6XFR5GH4agOMGp8QP64")
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY", "")
DEFAULT_CITY = "Yerevan"
DEFAULT_TIMEZONE = "Asia/Yerevan"
MORNING_HOUR = 8
MORNING_MINUTE = 0
# ─────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

user_data_store: dict = {}
subscribed_users: set = set()

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["🌤 Погода", "💰 Курсы валют"],
        ["₿ Крипта", "⏰ Напоминалки"],
        ["📋 Мои напоминалки", "🔔 Подписка на утро"],
    ],
    resize_keyboard=True
)

ARMENIA_CITIES_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["🏙 Ереван", "🏔 Гюмри"],
        ["🌿 Ванадзор", "🏛 Вагаршапат"],
        ["🌊 Севан", "🌄 Дилижан"],
        ["🔙 Назад"],
    ],
    resize_keyboard=True
)

CITY_MAP = {
    "🏙 Ереван": "Yerevan",
    "🏔 Гюмри": "Gyumri",
    "🌿 Ванадзор": "Vanadzor",
    "🏛 Вагаршапат": "Vagharshapat",
    "🌊 Севан": "Sevan",
    "🌄 Дилижан": "Dilijan",
}


def get_store(chat_id: int) -> dict:
    if chat_id not in user_data_store:
        user_data_store[chat_id] = {"reminders": [], "city": DEFAULT_CITY, "timezone": DEFAULT_TIMEZONE}
    return user_data_store[chat_id]


# ─── ПОГОДА ──────────────────────────────────────────────────
WMO_CODES = {
    0: "Ясно ☀️", 1: "Преимущественно ясно 🌤", 2: "Переменная облачность ⛅",
    3: "Пасмурно ☁️", 45: "Туман 🌫", 48: "Туман с инеем 🌫",
    51: "Лёгкая морось 🌦", 53: "Морось 🌦", 55: "Сильная морось 🌧",
    61: "Небольшой дождь 🌧", 63: "Дождь 🌧", 65: "Сильный дождь 🌧",
    71: "Небольшой снег 🌨", 73: "Снег 🌨", 75: "Сильный снег ❄️",
    80: "Ливень 🌦", 81: "Сильный ливень 🌧", 82: "Очень сильный ливень ⛈",
    95: "Гроза ⛈", 96: "Гроза с градом ⛈", 99: "Сильная гроза с градом ⛈",
}

async def fetch_weather(city: str) -> str:
    try:
        async with aiohttp.ClientSession() as session:
            geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1&language=ru"
            async with session.get(geo_url) as resp:
                geo = await resp.json()

            if not geo.get("results"):
                return f"❌ Город «{city}» не найден."

            result = geo["results"][0]
            lat = result["latitude"]
            lon = result["longitude"]
            city_name = result.get("name", city)

            weather_url = (
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}"
                f"&current=temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weathercode"
                f"&wind_speed_unit=ms"
            )
            async with session.get(weather_url) as resp:
                data = await resp.json()

        current = data["current"]
        temp = current["temperature_2m"]
        feels = current["apparent_temperature"]
        humidity = current["relative_humidity_2m"]
        wind = current["wind_speed_10m"]
        code = current["weathercode"]
        desc = WMO_CODES.get(code, "Нет данных")

        return (
            f"🌤 Погода в {city_name}\n\n"
            f"🌡 Температура: {temp:.0f}°C (ощущается {feels:.0f}°C)\n"
            f"☁️ {desc}\n"
            f"💧 Влажность: {humidity}%\n"
            f"💨 Ветер: {wind} м/с"
        )
    except Exception as e:
        logger.error("Weather error: %s", e)
        return "❌ Не удалось получить погоду."


# ─── КУРСЫ ВАЛЮТ ─────────────────────────────────────────────
async def fetch_rates() -> str:
    url = "https://api.exchangerate-api.com/v4/latest/USD"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()

        rates = data["rates"]
        amd = rates.get("AMD", 0)
        rub = rates.get("RUB", 0)
        eur = rates.get("EUR", 1)
        uah = rates.get("UAH", 0)

        return (
            f"💰 Курсы валют (к USD)\n\n"
            f"🇦🇲 AMD: {amd:.0f} ֏\n"
            f"🇷🇺 RUB: {rub:.1f} ₽\n"
            f"🇪🇺 EUR: {1/eur:.4f} $\n"
            f"🇺🇦 UAH: {uah:.1f} ₴"
        )
    except Exception as e:
        logger.error("Rates error: %s", e)
        return "❌ Не удалось получить курсы валют."


# ─── КРИПТА ──────────────────────────────────────────────────
async def fetch_crypto() -> str:
    symbols = [("BTCUSDT", "BTC"), ("ETHUSDT", "ETH"), ("SOLUSDT", "SOL"), ("TONUSDT", "TON")]
    url = "https://api.binance.com/api/v3/ticker/24hr?symbols=" + \
          str([s[0] for s in symbols]).replace("'", '"')
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()

        lines = ["₿ Курс криптовалют\n"]
        for item, (_, symbol) in zip(data, symbols):
            price = float(item["lastPrice"])
            change = float(item["priceChangePercent"])
            arrow = "📈" if change >= 0 else "📉"
            lines.append(f"{arrow} {symbol}: ${price:,.2f} ({change:+.1f}%)")
        return "\n".join(lines)
    except Exception as e:
        logger.error("Crypto error: %s", e)
        return "❌ Не удалось получить курс крипты."


# ─── ХЕНДЛЕРЫ ────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я твой личный помощник.\n\nВыбери что тебя интересует:",
        reply_markup=MAIN_KEYBOARD
    )


async def handle_weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store = get_store(update.effective_chat.id)
    city = store.get("city", DEFAULT_CITY)
    await update.message.reply_text("⏳ Получаю погоду...")
    await update.message.reply_text(await fetch_weather(city))


async def handle_rates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Получаю курсы...")
    await update.message.reply_text(await fetch_rates())


async def handle_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Получаю курс крипты...")
    await update.message.reply_text(await fetch_crypto())


async def handle_reminders_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⏰ Напоминалки\n\n"
        "Добавить: /remind HH:MM Текст\n"
        "Пример: /remind 14:30 Выпить воду\n\n"
        "Список: /reminders\n"
        "Удалить все: /clear"
    )


async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text("❌ Формат: /remind HH:MM Текст\nПример: /remind 09:00 Зарядка")
        return

    time_str = args[0]
    text = " ".join(args[1:])

    try:
        hour, minute = map(int, time_str.split(":"))
        assert 0 <= hour <= 23 and 0 <= minute <= 59
    except Exception:
        await update.message.reply_text("❌ Неверное время. Формат: HH:MM (например 14:30)")
        return

    store = get_store(update.effective_chat.id)
    store["reminders"].append({"hour": hour, "minute": minute, "text": text})
    await update.message.reply_text(f"✅ Напоминание установлено!\n⏰ {time_str} — {text}")


async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store = get_store(update.effective_chat.id)
    reminders = store.get("reminders", [])
    if not reminders:
        await update.message.reply_text("📋 У тебя нет напоминалок.\n\nДобавь через /remind HH:MM Текст")
        return
    lines = [f"⏰ {r['hour']:02d}:{r['minute']:02d} — {r['text']}" for r in reminders]
    await update.message.reply_text("📋 Твои напоминалки:\n\n" + "\n".join(lines))


async def clear_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_store(update.effective_chat.id)["reminders"] = []
    await update.message.reply_text("🗑 Все напоминалки удалены.")


async def handle_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in subscribed_users:
        subscribed_users.discard(chat_id)
        await update.message.reply_text("🔕 Утренний дайджест отключён.")
    else:
        subscribed_users.add(chat_id)
        store = get_store(chat_id)
        city = store.get("city", DEFAULT_CITY)
        await update.message.reply_text(
            f"🔔 Готово! Каждое утро в {MORNING_HOUR:02d}:{MORNING_MINUTE:02d} UTC "
            f"буду присылать погоду для {city} и курсы.\n\n"
            f"Сменить город: /city Название"
        )


async def set_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "Формат: /timezone Зона\n\n"
            "Примеры:\n"
            "/timezone Asia/Yerevan\n"
            "/timezone Europe/Moscow\n"
            "/timezone Europe/Kiev\n"
            "/timezone Asia/Tbilisi\n"
            "/timezone Europe/Berlin"
        )
        return
    tz_name = args[0]
    try:
        pytz.timezone(tz_name)
    except Exception:
        await update.message.reply_text(
            f"❌ Часовой пояс «{tz_name}» не найден.\n\n"
            "Используй формат: Continent/City\n"
            "Например: Asia/Yerevan, Europe/Moscow"
        )
        return
    get_store(update.effective_chat.id)["timezone"] = tz_name
    await update.message.reply_text(f"✅ Часовой пояс установлен: {tz_name}")


async def set_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Формат: /city Название\nПример: /city Moscow")
        return
    city = " ".join(args)
    get_store(update.effective_chat.id)["city"] = city
    await update.message.reply_text(f"✅ Город изменён на: {city}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    handlers = {
        "🌤 Погода": handle_weather,
        "💰 Курсы валют": handle_rates,
        "₿ Крипта": handle_crypto,
        "⏰ Напоминалки": handle_reminders_menu,
        "📋 Мои напоминалки": list_reminders,
        "🔔 Подписка на утро": handle_subscription,
    }
    if text in handlers:
        await handlers[text](update, context)
    else:
        await update.message.reply_text(
            "Используй кнопки меню или команды:\n"
            "/remind HH:MM Текст — напоминание\n"
            "/reminders — список\n"
            "/clear — удалить все\n"
            "/city Город — сменить город\n"
            "/timezone Зона — часовой пояс (напр. Asia/Yerevan)"
        )


# ─── ПЛАНИРОВЩИК ─────────────────────────────────────────────
async def send_morning_digest(app: Application):
    if not subscribed_users:
        return
    rates = await fetch_rates()
    crypto = await fetch_crypto()
    for chat_id in list(subscribed_users):
        try:
            store = get_store(chat_id)
            city = store.get("city", DEFAULT_CITY)
            weather = await fetch_weather(city)
            text = f"☀️ Доброе утро!\n\n{weather}\n\n{rates}\n\n{crypto}"
            await app.bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            logger.error("Morning digest error for %s: %s", chat_id, e)


async def check_reminders(app: Application):
    for chat_id, store in user_data_store.items():
        tz_name = store.get("timezone", DEFAULT_TIMEZONE)
        try:
            tz = pytz.timezone(tz_name)
        except Exception:
            tz = pytz.timezone(DEFAULT_TIMEZONE)
        now = datetime.now(tz)

        for reminder in store.get("reminders", []):
            if reminder["hour"] == now.hour and reminder["minute"] == now.minute:
                if reminder.get("fired_at") == f"{now.date()} {now.hour}:{now.minute}":
                    continue
                reminder["fired_at"] = f"{now.date()} {now.hour}:{now.minute}"
                try:
                    await app.bot.send_message(
                        chat_id=chat_id,
                        text=f"⏰ Напоминание: {reminder['text']}"
                    )
                except Exception as e:
                    logger.error("Reminder error: %s", e)


# ─── MAIN ─────────────────────────────────────────────────────
def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN не задан!")

    scheduler = AsyncIOScheduler()

    async def on_startup(app: Application):
        scheduler.add_job(send_morning_digest, "cron",
                          hour=MORNING_HOUR, minute=MORNING_MINUTE, args=[app])
        scheduler.add_job(check_reminders, "cron", minute="*", args=[app])
        scheduler.start()
        logger.info("Планировщик запущен!")

    async def on_shutdown(app: Application):
        scheduler.shutdown()

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("remind", remind_command))
    app.add_handler(CommandHandler("reminders", list_reminders))
    app.add_handler(CommandHandler("clear", clear_reminders))
    app.add_handler(CommandHandler("city", set_city))
    app.add_handler(CommandHandler("timezone", set_timezone))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
