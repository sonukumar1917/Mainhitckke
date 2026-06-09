import os
import json
import threading
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import logging
import time

# ==================== LOGGING SETUP ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== CONFIG MANAGEMENT ====================
CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "leakosint_token": "7655738256:ZOhPKOxR",
    "api_enabled": True,
    "default_limit": 100,
    "default_lang": "en",
    "bot_token": "8511711035:AAG8ddIaqW2kQ8BAXJfZLssED64FxDppksE",  # <-- YOU MUST SET YOUR TELEGRAM BOT TOKEN HERE
    "admin_ids": [7655738256]  # Your Telegram user ID
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    else:
        with open(CONFIG_FILE, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
        return DEFAULT_CONFIG.copy()

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

config = load_config()
LEAKOSINT_API_URL = "https://leakosintapi.com/"

# Helper: check if user is admin
def is_admin(user_id):
    return user_id in config.get("admin_ids", [])

# ==================== FLASK API ====================
app = Flask(__name__)
CORS(app)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["3 per second"],
    storage_uri="memory://",
)

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "name": "Leakosint API Proxy + Bot Control",
        "version": "2.0",
        "status": "enabled" if config["api_enabled"] else "disabled",
        "endpoints": {
            "GET/POST /api/search": "Perform search (respects API enable/disable)",
            "GET /health": "Health check"
        }
    })

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "api_enabled": config["api_enabled"],
        "timestamp": __import__("datetime").datetime.now().isoformat()
    })

@app.route("/api/search", methods=["GET", "POST"])
@limiter.limit("3 per second")
def search():
    if not config["api_enabled"]:
        return jsonify({"error": "API is currently disabled by bot owner"}), 503

    try:
        if request.method == "GET":
            req_data = {
                "request": request.args.get("request"),
                "limit": request.args.get("limit", config["default_limit"]),
                "lang": request.args.get("lang", config["default_lang"]),
                "type": request.args.get("type", "json"),
                "bot_name": request.args.get("bot_name")
            }
        else:
            req_data = request.get_json()
            if not req_data:
                return jsonify({"error": "Invalid or missing JSON body"}), 400

        request_query = req_data.get("request")
        if not request_query:
            return jsonify({"error": "Missing required parameter: 'request'"}), 400

        # If request is list, join with newline (batch mode)
        if isinstance(request_query, list):
            request_query = "\n".join(request_query)

        # Validate limit
        try:
            limit = int(req_data.get("limit", config["default_limit"]))
        except:
            limit = config["default_limit"]
        limit = max(100, min(10000, limit))

        lang = req_data.get("lang", config["default_lang"])
        resp_type = req_data.get("type", "json")
        bot_name = req_data.get("bot_name")

        payload = {
            "token": config["leakosint_token"],
            "request": request_query,
            "limit": limit,
            "lang": lang,
            "type": resp_type,
        }
        if bot_name:
            payload["bot_name"] = bot_name

        response = requests.post(LEAKOSINT_API_URL, json=payload, timeout=30)
        if response.status_code != 200:
            return jsonify({
                "error": "Upstream API error",
                "status_code": response.status_code,
                "details": response.text
            }), response.status_code

        return jsonify(response.json()), 200

    except requests.exceptions.Timeout:
        return jsonify({"error": "Upstream API timeout after 30 seconds"}), 504
    except requests.exceptions.RequestException as e:
        return jsonify({"error": "Upstream API request failed", "message": str(e)}), 502
    except Exception as e:
        return jsonify({"error": "Internal server error", "message": str(e)}), 500

def run_flask():
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ==================== TELEGRAM BOT ====================
bot_token = config.get("bot_token", "")
if not bot_token:
    logger.warning("Telegram bot token not set. Please edit config.json and add your bot token from @BotFather.")
    bot = None
else:
    bot = telebot.TeleBot(bot_token)
    logger.info("Telegram bot initialized.")

# Helper: perform search (used by bot)
def perform_search(query, limit, lang):
    payload = {
        "token": config["leakosint_token"],
        "request": query,
        "limit": limit,
        "lang": lang,
        "type": "json"
    }
    try:
        resp = requests.post(LEAKOSINT_API_URL, json=payload, timeout=30)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

# Format search results for Telegram (clean, readable)
def format_results(result, query):
    if "error" in result:
        return f"❌ Error: {result['error']}"
    if "List" not in result:
        return "❌ Unexpected response format."
    
    dbs = result["List"]
    if "No results found" in dbs:
        return f"🔍 No results found for: `{query}`"
    
    output = f"*Results for:* `{query}`\n\n"
    count = 0
    for db_name, db_data in dbs.items():
        if db_name == "No results found":
            continue
        output += f"📁 *{db_name}*\n"
        if "Data" in db_data and db_data["Data"]:
            for entry in db_data["Data"][:3]:  # limit to first 3 entries
                for k, v in entry.items():
                    output += f"• *{k}*: {v}\n"
                output += "\n"
        else:
            output += "_No data entries_\n"
        count += 1
        if count >= 5:  # prevent huge messages
            output += "\n_...more results. Use API for full data._\n"
            break
    if len(output) > 4000:
        output = output[:3970] + "\n..._truncated_"
    return output

# --- Bot command handlers ---
@bot.message_handler(commands=["start"])
def send_welcome(message):
    user_id = message.from_user.id
    if is_admin(user_id):
        welcome_text = (
            "🤖 *Leakosint Control Bot (Admin)*\n\n"
            "You have full control over the API.\n\n"
            "*Commands:*\n"
            "/status - Show current API status & settings\n"
            "/token - Show current leakosint token (masked)\n"
            "/settoken <new_token> - Change leakosint token\n"
            "/enable - Enable the public API\n"
            "/disable - Disable the public API\n"
            "/setlimit <100-10000> - Set default search limit\n"
            "/setlang <en/ru> - Set default language\n"
            "/search <query> - Search directly via bot\n"
            "/addadmin <user_id> - Add new admin (owner only)\n"
            "/deladmin <user_id> - Remove admin (owner only)\n"
            "/help - Show this help"
        )
    else:
        welcome_text = (
            "🤖 *Leakosint Search Bot*\n\n"
            "You can search data leaks using this bot.\n\n"
            "*Commands:*\n"
            "/search <query> - Search for leaks\n"
            "/status - Show API status\n"
            "/help - Show help"
        )
    bot.reply_to(message, welcome_text, parse_mode="Markdown")

@bot.message_handler(commands=["help"])
def help_command(message):
    send_welcome(message)  # reuse

@bot.message_handler(commands=["status"])
def status_command(message):
    user_id = message.from_user.id
    admin = is_admin(user_id)
    status_text = f"📊 *API Status*\n"
    status_text += f"• Public API: `{'✅ Enabled' if config['api_enabled'] else '❌ Disabled'}`\n"
    status_text += f"• Default limit: `{config['default_limit']}`\n"
    status_text += f"• Default language: `{config['default_lang']}`\n"
    if admin:
        token = config["leakosint_token"]
        masked = token[:6] + "..." + token[-4:] if len(token) > 10 else "***"
        status_text += f"• Leakosint token: `{masked}`\n"
    else:
        status_text += f"• Leakosint token: `(hidden)`\n"
    bot.reply_to(message, status_text, parse_mode="Markdown")

@bot.message_handler(commands=["token"])
def token_command(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ You are not authorized to view or change the token.")
        return
    token = config["leakosint_token"]
    masked = token[:6] + "..." + token[-4:] if len(token) > 10 else "***"
    bot.reply_to(message, f"Current token: `{masked}`\n\nUse `/settoken <new_token>` to change.", parse_mode="Markdown")

@bot.message_handler(commands=["settoken"])
def set_token_command(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Admin only command.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "Usage: `/settoken <new_token>`", parse_mode="Markdown")
        return
    new_token = parts[1].strip()
    config["leakosint_token"] = new_token
    save_config(config)
    bot.reply_to(message, "✅ Leakosint token updated successfully.")

@bot.message_handler(commands=["enable"])
def enable_command(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Admin only command.")
        return
    config["api_enabled"] = True
    save_config(config)
    bot.reply_to(message, "✅ Public API is now *enabled*.", parse_mode="Markdown")

@bot.message_handler(commands=["disable"])
def disable_command(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Admin only command.")
        return
    config["api_enabled"] = False
    save_config(config)
    bot.reply_to(message, "🔴 Public API is now *disabled*.", parse_mode="Markdown")

@bot.message_handler(commands=["setlimit"])
def set_limit_command(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Admin only command.")
        return
    parts = message.text.split()
    if len(parts) != 2:
        bot.reply_to(message, "Usage: `/setlimit <100-10000>`", parse_mode="Markdown")
        return
    try:
        new_limit = int(parts[1])
        if new_limit < 100 or new_limit > 10000:
            raise ValueError
        config["default_limit"] = new_limit
        save_config(config)
        bot.reply_to(message, f"✅ Default search limit set to `{new_limit}`.", parse_mode="Markdown")
    except:
        bot.reply_to(message, "❌ Invalid limit. Must be between 100 and 10000.")

@bot.message_handler(commands=["setlang"])
def set_lang_command(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Admin only command.")
        return
    parts = message.text.split()
    if len(parts) != 2:
        bot.reply_to(message, "Usage: `/setlang <en/ru>`", parse_mode="Markdown")
        return
    lang = parts[1].lower()
    if lang not in ["en", "ru"]:
        bot.reply_to(message, "Language must be `en` or `ru`.", parse_mode="Markdown")
        return
    config["default_lang"] = lang
    save_config(config)
    bot.reply_to(message, f"✅ Default language set to `{lang}`.", parse_mode="Markdown")

@bot.message_handler(commands=["addadmin"])
def add_admin_command(message):
    user_id = message.from_user.id
    # Only the owner (first admin in list) can add new admins
    if not config.get("admin_ids") or user_id != config["admin_ids"][0]:
        bot.reply_to(message, "⛔ Only the bot owner can add new admins.")
        return
    parts = message.text.split()
    if len(parts) != 2:
        bot.reply_to(message, "Usage: `/addadmin <user_id>`", parse_mode="Markdown")
        return
    try:
        new_admin_id = int(parts[1])
        if new_admin_id in config.get("admin_ids", []):
            bot.reply_to(message, f"User `{new_admin_id}` is already an admin.", parse_mode="Markdown")
            return
        config["admin_ids"].append(new_admin_id)
        save_config(config)
        bot.reply_to(message, f"✅ User `{new_admin_id}` added as admin.", parse_mode="Markdown")
    except:
        bot.reply_to(message, "❌ Invalid user ID.")

@bot.message_handler(commands=["deladmin"])
def del_admin_command(message):
    user_id = message.from_user.id
    if not config.get("admin_ids") or user_id != config["admin_ids"][0]:
        bot.reply_to(message, "⛔ Only the bot owner can remove admins.")
        return
    parts = message.text.split()
    if len(parts) != 2:
        bot.reply_to(message, "Usage: `/deladmin <user_id>`", parse_mode="Markdown")
        return
    try:
        remove_id = int(parts[1])
        if remove_id == config["admin_ids"][0]:
            bot.reply_to(message, "❌ Cannot remove the owner.")
            return
        if remove_id not in config.get("admin_ids", []):
            bot.reply_to(message, f"User `{remove_id}` is not an admin.", parse_mode="Markdown")
            return
        config["admin_ids"].remove(remove_id)
        save_config(config)
        bot.reply_to(message, f"✅ User `{remove_id}` removed from admins.", parse_mode="Markdown")
    except:
        bot.reply_to(message, "❌ Invalid user ID.")

@bot.message_handler(commands=["search"])
def search_command(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "Usage: `/search <query>`\nExample: `/search google`", parse_mode="Markdown")
        return
    query = parts[1].strip()
    # If API is disabled, only admin can search
    if not config["api_enabled"]:
        if is_admin(message.from_user.id):
            bot.reply_to(message, "⚠️ API is currently disabled, but as admin you can still search.\nSearching...", parse_mode="Markdown")
        else:
            bot.reply_to(message, "❌ API is temporarily disabled by admin. Please try later.")
            return
    else:
        bot.reply_to(message, f"🔍 Searching for: `{query}`...", parse_mode="Markdown")
    
    result = perform_search(query, config["default_limit"], config["default_lang"])
    formatted = format_results(result, query)
    # Split if too long (Telegram max 4096 chars)
    if len(formatted) > 4096:
        for i in range(0, len(formatted), 4000):
            bot.reply_to(message, formatted[i:i+4000], parse_mode="Markdown")
    else:
        bot.reply_to(message, formatted, parse_mode="Markdown")

@bot.message_handler(commands=["balance"])
def balance_command(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Admin only command.")
        return
    # Leakosint does not provide a direct balance endpoint.
    try:
        test_payload = {
            "token": config["leakosint_token"],
            "request": "test",
            "limit": 1,
            "lang": "en",
            "type": "json"
        }
        resp = requests.post(LEAKOSINT_API_URL, json=test_payload, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if "Error code" in data:
                bot.reply_to(message, f"⚠️ API returned error: {data['Error code']}\nPossible insufficient balance.")
            else:
                bot.reply_to(message, "✅ Token seems valid. Balance not directly available. Check your Leakosint account.")
        else:
            bot.reply_to(message, f"❌ API returned status {resp.status_code}. Token may be invalid or expired.")
    except Exception as e:
        bot.reply_to(message, f"❌ Balance check failed: {str(e)}")

# Fallback for unknown commands
@bot.message_handler(func=lambda message: True)
def echo_all(message):
    bot.reply_to(message, "Unknown command. Type /help for available commands.")

def run_bot():
    if bot:
        logger.info("🤖 Telegram bot started. Polling...")
        retries = 0
        max_retries = 10
        while retries < max_retries:
            try:
                bot.infinity_polling(timeout=30, long_polling_timeout=30)
                break
            except Exception as e:
                logger.error(f"Bot polling error: {e}")
                retries += 1
                time.sleep(5 * retries)
    else:
        logger.warning("Telegram bot not started (no token).")

# ==================== MAIN ENTRY POINT ====================
if __name__ == "__main__":
    # Start Flask in a background thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"✅ Flask API is running (port {os.environ.get('PORT', 3000)}).")
    
    # Start bot in main thread
    run_bot()