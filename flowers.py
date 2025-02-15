import asyncio
import hashlib
from bs4 import BeautifulSoup
import aiohttp
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, CallbackContext

# ============================
# Database Setup
# ============================
DB_PATH = "tracked_links.db"

def init_db():
    """Инициализация базы данных и создание нужной таблицы."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tracked_links (
            chat_id INTEGER,
            url TEXT,
            title TEXT,
            PRIMARY KEY (chat_id, url)
        )
    """)
    conn.commit()
    conn.close()

def add_link_to_db(chat_id, url, title):
    """Добавляет ссылку в базу данных."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO tracked_links (chat_id, url, title) VALUES (?, ?, ?)", (chat_id, url, title))
    conn.commit()
    conn.close()

def remove_link_from_db(chat_id, url):
    """Удаляет ссылку из базы данных."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tracked_links WHERE chat_id = ? AND url = ?", (chat_id, url))
    conn.commit()
    conn.close()

def get_links_from_db(chat_id):
    """Получает все отслеживаемые ссылки для пользователя."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT url, title FROM tracked_links WHERE chat_id = ?", (chat_id,))
    links = cursor.fetchall()
    conn.close()
    return links

# ============================
# Global Variables
# ============================
USER_STOP_FLAG = {}  # {chat_id: {url: True}} - флаг остановки отслеживания для каждого пользователя
TOKEN = '7432735809:AAHqF5L4oCuLQ4M7eezywP-GvbPZcrySAuQ'  # Укажите токен вашего бота
CHECK_INTERVAL = 3  # Интервал проверки в секундах

# ============================
# Helper Functions
# ============================

def generate_unique_id(url):
    """Генерирует уникальный ID для URL."""
    return hashlib.md5(url.encode('utf-8')).hexdigest()[:10]

async def check_product_status(url):
    """Парсит страницу товара и проверяет его доступность."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                response.raise_for_status()
                soup = BeautifulSoup(await response.text(), 'html.parser')

                title_tag = soup.find('h1', class_='nom-name is-hidden-sm', itemprop='name')
                title = title_tag.text.strip() if title_tag else 'Title not found'

                # Ищем цену в теге <meta itemprop="price">
                price_meta = soup.find('meta', itemprop='price')
                price = price_meta['content'] if price_meta else 'Цена не найдена'

                status_tag = soup.find('td', itemprop='offers')
                if status_tag:
                    availability = status_tag.find('meta', itemprop='availability')
                    if availability and availability['content'] == 'InStock':
                        return title, price, True, 'InStock'
                    elif availability and availability['content'] == 'OutOfStock':
                        return title, price, False, 'OutOfStock'

                return title, price, False, None
    except Exception as e:
        return 'Error loading page', 'Ошибка цены', False, None


async def monitor_product(url, chat_id, bot, track_status):
    """Отслеживает товар и уведомляет пользователя, когда достигнут нужный статус."""
    try:
        while True:
            if USER_STOP_FLAG.get(chat_id, {}).get(url):
                break

            title, price, status, status_check = await check_product_status(url)

            if status_check == track_status:
                message = f'🌺 Продукт "{title}" доступен по цене {price}.'
                keyboard = [
                    [InlineKeyboardButton(f"Посмотреть продукт: {title}", url=url)],
                    [InlineKeyboardButton("Удалить из отслеживания", callback_data=f"remove_{generate_unique_id(url)}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await bot.send_message(chat_id=chat_id, text=message, reply_markup=reply_markup)

            await asyncio.sleep(CHECK_INTERVAL)

    except asyncio.CancelledError:
        print(f"Задача отслеживания для {url} отменена.")
        pass

async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("✅ Бот запущен! Используйте команду /add {URL}, чтобы отслеживать продукт.")

async def add_link(update: Update, context: CallbackContext):
    if len(context.args) != 1:
        await update.message.reply_text("⚠️ Пожалуйста, укажите только один URL после команды /add.")
        return

    url = context.args[0]
    chat_id = update.effective_chat.id

    title, price, status, _ = await check_product_status(url)
    add_link_to_db(chat_id, url, title)

    await update.message.reply_text(f"Продукт отслеживается: {title}. Статус будет проверяться каждые {CHECK_INTERVAL} секунд.")
    asyncio.create_task(monitor_product(url, chat_id, context.bot, 'InStock'))

async def remove_link(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id

    if update.callback_query:  # Обработка колбэка от кнопки
        unique_id = update.callback_query.data.split('_')[1]
        links = get_links_from_db(chat_id)
        url_to_remove = next((url for url, title in links if generate_unique_id(url) == unique_id), None)
        if url_to_remove:
            remove_link_from_db(chat_id, url_to_remove)
            await update.callback_query.edit_message_text(f"Ссылка \"{url_to_remove}\" удалена из отслеживания.")
            USER_STOP_FLAG.setdefault(chat_id, {})[url_to_remove] = True
        return

    if len(context.args) != 1:
        await update.message.reply_text("⚠️ Пожалуйста, укажите URL для удаления.")
        return

    url = context.args[0]
    remove_link_from_db(chat_id, url)
    await update.message.reply_text(f"Ссылка удалена из отслеживания: {url}.")
    USER_STOP_FLAG.setdefault(chat_id, {})[url] = True

async def list_links(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    links = get_links_from_db(chat_id)

    if not links:
        await update.message.reply_text("У вас нет отслеживаемых ссылок.")
        return

    message = "Ваши отслеживаемые ссылки:\n" + "\n".join([f"- {title} ({url})" for url, title in links])
    await update.message.reply_text(message)

# ============================
# Main Function
# ============================

async def restore_tracking(application):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id, url FROM tracked_links")
    links = cursor.fetchall()
    conn.close()

    for chat_id, url in links:
        asyncio.create_task(monitor_product(url, chat_id, application.bot, 'InStock'))

def main():
    init_db()

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add", add_link))
    application.add_handler(CommandHandler("remove", remove_link))
    application.add_handler(CommandHandler("list", list_links))
    application.add_handler(CallbackQueryHandler(remove_link, pattern="^remove_"))

    application.job_queue.run_once(lambda context: asyncio.create_task(restore_tracking(application)), 0)

    application.run_polling()

if __name__ == "__main__":
    main()
