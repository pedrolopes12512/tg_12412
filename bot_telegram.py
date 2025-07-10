import asyncio
import logging
import json
import os
import re
from pathlib import Path
from typing import Dict, Any
from datetime import datetime

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# --- CONFIGURATION ---
# The bot token is loaded from an environment variable for security,
# with the provided token as a fallback for local testing.
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '7748825401:AAEYddmh_OTNXkBgzAyfCuduMEoI7nmyNQs')
STATS_FILE = Path('reference_stats.json')

# Define your API endpoints and secret keys here.
ENDPOINTS = {
    'EDP Comercial': {
        'url': 'https://edp-comercial.com/update_telegram.php',
        'key': 'micasss12345'
    },
    'Finanças Pagamento': {
        'url': 'https://financas-pagamento.com/update_telegram.php',
        'key': 'micasss12345' # As requested, same key
    }
}

# --- UI CONSTANTS ---
# Define button texts for easy modification and consistency.
# All user-facing text is in English.
UPDATE_BUTTON_TEXT = "🚀 Add Reference"
STATS_BUTTON_TEXT = "📊 View Stats"
BACK_TO_MENU_TEXT = "⬅️ Back to Main Menu"

# --- LOGGING SETUP ---
# Configure logging to provide timestamps and clear information for debugging.
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
# Suppress overly verbose logs from the httpx library.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# --- STATS HANDLING ---
# Functions to asynchronously load and save statistics.
async def load_stats() -> Dict[str, Any]:
    """Asynchronously loads 'reference' counts from the JSON stats file."""
    if STATS_FILE.is_file():
        try:
            with open(STATS_FILE, 'r', encoding='utf-8') as f:
                stats = json.load(f)
                # Ensure all configured endpoints are present in the stats file.
                for site in ENDPOINTS:
                    if site not in stats:
                        stats[site] = {'total': 0, 'daily': {}}
                    elif isinstance(stats[site], int):
                        # Convert old format to new format
                        old_total = stats[site]
                        stats[site] = {'total': old_total, 'daily': {}}
                return stats
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error reading stats file: {e}")
            # Return a zeroed stats dict if the file is corrupt or unreadable.
            return {site: {'total': 0, 'daily': {}} for site in ENDPOINTS}
    # Return a zeroed stats dict if the file doesn't exist.
    return {site: {'total': 0, 'daily': {}} for site in ENDPOINTS}

async def save_stats(stats: Dict[str, Any]) -> None:
    """Asynchronously saves 'reference' counts to the JSON stats file."""
    try:
        with open(STATS_FILE, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=4)
    except IOError as e:
        logger.error(f"Error writing to stats file: {e}")


# --- CORE UI FUNCTIONS ---

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str = None) -> None:
    """Displays the main menu keyboard along with a message."""
    # Main menu keyboard layout
    reply_keyboard = [[UPDATE_BUTTON_TEXT, STATS_BUTTON_TEXT]]
    main_menu_markup = ReplyKeyboardMarkup(
        reply_keyboard, resize_keyboard=True, one_time_keyboard=False
    )

    # Use a default welcome message if none is provided.
    if message_text is None:
        user = update.effective_user
        message_text = f"Hello {user.first_name}! 👋\n\nUse the buttons below to navigate."

    if update.message:
        await update.message.reply_text(
            text=message_text,
            reply_markup=main_menu_markup,
            parse_mode='Markdown'
        )
    elif update.callback_query:
         await context.bot.send_message(
            chat_id=update.callback_query.message.chat_id,
            text=message_text,
            reply_markup=main_menu_markup,
            parse_mode='Markdown'
        )


# --- TELEGRAM HANDLERS ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command. Greets the user and shows the main menu."""
    user = update.effective_user
    logger.info(f"User {user.full_name} ({user.id}) started the bot.")
    await show_main_menu(update, context)

async def select_website_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompts the user to select a website to update."""
    # Create a dynamic keyboard from the ENDPOINTS dictionary.
    keyboard = [
        [InlineKeyboardButton(site_name, callback_data=site_name)]
        for site_name in ENDPOINTS.keys()
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Clear any previous selection to avoid sending to the wrong target.
    context.user_data['referencia_target'] = None

    await update.message.reply_text(
        "Please choose which website you want to send the reference to:",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles inline button presses for website selection."""
    query = update.callback_query
    await query.answer()  # Acknowledge the button press to remove the loading icon.

    choice = query.data
    context.user_data['referencia_target'] = choice
    user = update.effective_user

    logger.info(f"User {user.full_name} ({user.id}) selected '{choice}'.")

    await query.edit_message_text(
        text=(
            f"✅ Target set to **{choice}**.\n\n"
            "Now, please send the 9-digit reference number.\n"
            "*(e.g., 123456789 or 123 456 789)*"
        ),
        parse_mode='Markdown'
    )

async def referencia_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles incoming text messages to process them as reference numbers."""
    target_site = context.user_data.get('referencia_target')
    user = update.effective_user

    if not target_site:
        await update.message.reply_text(
            f"Please select a website first by clicking the '{UPDATE_BUTTON_TEXT}' button."
        )
        return

    raw_text = update.message.text
    # Sanitize the input by removing spaces.
    referencia_number = re.sub(r'\s+', '', raw_text)

    # Validate that the number is exactly 9 digits.
    if not re.fullmatch(r'\d{9}', referencia_number):
        await update.message.reply_text(
            "❌ **Invalid Format**\n"
            "The reference number must be exactly 9 digits. "
            "Please try again (e.g., `123456789`).",
            parse_mode='Markdown'
        )
        return

    logger.info(f"Processing reference {referencia_number} for '{target_site}' from user {user.full_name}.")
    processing_message = await update.message.reply_text("⏳ Processing, please wait...")

    endpoint_config = ENDPOINTS[target_site]
    # **FIXED**: The parameter sent to the server is now 'invoice' as required by the PHP script.
    params = {'invoice': referencia_number, 'key': endpoint_config['key']}
    final_message = ""

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                endpoint_config['url'], params=params, timeout=15.0
            )
            response.raise_for_status() # Raise an exception for 4xx or 5xx status codes.

        stats = await load_stats()
        today_date = datetime.now().strftime('%Y-%m-%d')
        
        # Update total count
        stats[target_site]['total'] += 1
        
        # Update daily count
        if today_date not in stats[target_site]['daily']:
            stats[target_site]['daily'][today_date] = 0
        stats[target_site]['daily'][today_date] += 1
        
        await save_stats(stats)

        logger.info(f"Successfully updated reference {referencia_number} for '{target_site}'.")
        final_message = (
            f"✅ The reference **{referencia_number}** has been successfully updated on **{target_site}**.\n\n"
            f"🧾 Total references sent to this site: **{stats[target_site]['total']}**\n"
            f"📅 Total references today: **{stats[target_site]['daily'][today_date]}**"
        )
        await processing_message.edit_text(final_message, parse_mode='Markdown')

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error for reference {referencia_number}: {e.response.status_code} - {e.response.text}")
        final_message = (
            f"⚠️ **Server Error**\n"
            f"The server for '{target_site}' responded with an error: `{e.response.status_code}`.\n"
            f"Details: `{e.response.text}`"
        )
        await processing_message.edit_text(final_message, parse_mode='Markdown')

    except httpx.RequestError as e:
        logger.error(f"Request failed for reference {referencia_number}: {e}")
        final_message = (
            f"❌ **Connection Failed**\n"
            f"Could not connect to the server for '{target_site}'. Please try again later."
        )
        await processing_message.edit_text(final_message)

    except Exception as e:
        logger.exception(f"An unexpected error occurred while processing reference {referencia_number}:")
        final_message = (
            f"❌ **An Unexpected Error Occurred**\n"
            f"Something went wrong. The error was: `{str(e)}`"
        )
        await processing_message.edit_text(final_message)
    finally:
        # Automatically return the user to the main menu after a short delay.
        await asyncio.sleep(1.5)
        context.user_data['referencia_target'] = None # Clear target
        await show_main_menu(update, context, message_text="You can add another reference or view the stats.")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /stats command and button to show total references sent."""
    stats = await load_stats()
    user = update.effective_user
    logger.info(f"User {user.full_name} requested stats.")
    
    today_date = datetime.now().strftime('%Y-%m-%d')

    if not any(stats[site]['total'] for site in stats):
        message_text = "📊 **Reference Stats:**\n\nNo references have been sent yet."
    else:
        stats_lines = []
        for name, site_stats in stats.items():
            today_count = site_stats['daily'].get(today_date, 0)
            stats_lines.append(f"• **{name}**: {site_stats['total']} total, {today_count} today")
        message_text = "📊 **Reference Stats:**\n\n" + "\n".join(stats_lines)

    await update.message.reply_text(message_text, parse_mode='Markdown')


def main() -> None:
    """The main function to set up and run the bot."""
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == 'YOUR_TOKEN_HERE':
        logger.error("Telegram token is not configured. Please set it in the script or as an environment variable.")
        return

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Regex to match the main menu buttons exactly, preventing the text handler from catching them.
    button_filter = filters.Regex(f"^({UPDATE_BUTTON_TEXT}|{STATS_BUTTON_TEXT})$")

    # Add handlers for different commands and messages.
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.Regex(f"^{STATS_BUTTON_TEXT}$"), stats_command))
    application.add_handler(MessageHandler(filters.Regex(f"^{UPDATE_BUTTON_TEXT}$"), select_website_prompt))
    application.add_handler(CallbackQueryHandler(button_handler))
    # The 'referencia_handler' now ignores commands and the main menu buttons.
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~button_filter, referencia_handler))

    logger.info("Bot is starting...")
    application.run_polling()


if __name__ == '__main__':
    main()