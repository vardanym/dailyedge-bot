import logging
import asyncio
import aiohttp
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)

# ─── НАСТРОЙКИ ───────────────────────────────────────────────
TELEGRAM_TOKEN = ""           # @BotFather
WEATHER_API_KEY = ""          # openweathermap.org (бесплатно)
DEFAULT_CITY = "Yerevan"      # твой город по умолчанию
MORNING_HOUR = 8              # время утреннего дайджеста (UTC)
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


def get_store(chat_id: int) -> dict:
    if chat_id not in user_data_store:
        user_data_store[chat_id] = {"reminders": [], "city": DEFAULT_CITY}
    return user_data_store[chat_id]


# ─── ПОГОДА ──────────────────────────────────────────────────
async def fetch_weather(city: str) -> str:
    url = (
        f"https://api.openweathermap.org/data/2.5/weather"
        f"?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 404:
                    return f"❌ Город «{city}» не найден."
                if resp.status != 200:
                    return "❌ Ошибка получения погоды."
                data = await resp.json()

        desc = data["weather"][0]["description"].capitalize()
        temp = data["main"]["temp"]
        feels = data["main"]["feels_like"]
        humidity = data["main"]["humidity"]
        wind = data["wind"]["speed"]
        city_name = data["name"]

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
    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=bitcoin,ethereum,toncoin,solana&vs_currencies=usd&include_24hr_change=true"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()

        def fmt(coin, symbol):
            if coin not in data:
                return f"— {symbol}: недоступно"
            price = data[coin]["usd"]
            change = data[coin].get("usd_24h_change", 0)
            arrow = "📈" if change >= 0 else "📉"
            return f"{arrow} {symbol}: ${price:,.2f} ({change:+.1f}%)"

        lines = ["₿ Курс криптовалют\n", fmt("bitcoin", "BTC"), fmt("ethereum", "ETH"), fmt("solana", "SOL"), fmt("toncoin", "TON")]
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
        await update.message.reply_text(
            f"🔔 Готово! Каждое утро в {MORNING_HOUR:02d}:{MORNING_MINUTE:02d} UTC "
            f"буду присылать погоду и курсы."
        )


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
            "/city Город — сменить город"
        )


# ─── ПЛАНИРОВЩИК ─────────────────────────────────────────────
async def send_morning_digest(app: Application):
    if not subscribed_users:
        return
    weather = await fetch_weather(DEFAULT_CITY)
    rates = await fetch_rates()
    crypto = await fetch_crypto()
    text = f"☀️ Доброе утро!\n\n{weather}\n\n{rates}\n\n{crypto}"
    for chat_id in list(subscribed_users):
        try:
            await app.bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            logger.error("Morning digest error for %s: %s", chat_id, e)


async def check_reminders(app: Application):
    now = datetime.utcnow()
    for chat_id, store in user_data_store.items():
        for reminder in store.get("reminders", []):
            if reminder["hour"] == now.hour and reminder["minute"] == now.minute:
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
    if not WEATHER_API_KEY:
        raise ValueError("WEATHER_API_KEY не задан!")

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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
