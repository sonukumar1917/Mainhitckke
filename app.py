import os
import json
import threading
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import telebot

# ==================== HARDCODED DEFAULTS (change only if needed) ====================
DEFAULT_LEAKOSINT_TOKEN = "7655738256:ZOhPKOxR"   # Your leakosint API token
DEFAULT_BOT_TOKEN = "8511711035:AAG8ddIaqW2kQ8BAXJfZLssED64FxDppksE"  # Your Telegram bot token
OWNER_ID = 7655738256                            # Your Telegram user ID

CONFIG_FILE = "data.json"   # auto-created, stores changes made via bot

# ==================== CONFIG MANAGEMENT ====================
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    else:
        default = {
            "leakosint_token": DEFAULT_LEAKOSINT_TOKEN,
            "api_enabled": True,
            "default_limit": 100,
            "default_lang": "en",
            "admin_ids": [OWNER_ID]
        }
        save_config(default)
        return default

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=4)

config = load_config()
LEAKOSINT_API_URL = "https://leakosintapi.com/"

def is_admin(user_id):
    return user_id in config.get("admin_ids", [])

# ==================== FLASK API ====================
app = Flask(__name__)
CORS(app)
limiter = Limiter(get_remote_address, app=app, default_limits=["3 per second"], storage_uri="memory://")

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "name": "Leakosint API + Bot",
        "status": "enabled" if config["api_enabled"] else "disabled",
        "endpoints": {
            "GET/POST /api/search": "search leaks",
            "GET /health": "health check"
        }
    })

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "api_enabled": config["api_enabled"]})

@app.route("/api/search", methods=["GET", "POST"])
@limiter.limit("3 per second")
def search():
    if not config["api_enabled"]:
        return jsonify({"error": "API disabled by bot owner"}), 503
    try:
        if request.method == "GET":
            data = request.args.to_dict()
        else:
            data = request.get_json() or {}
        query = data.get("request")
        if not query:
            return jsonify({"error": "Missing 'request'"}), 400
        if isinstance(query, list):
            query = "\n".join(query)
        limit = int(data.get("limit", config["default_limit"]))
        limit = max(100, min(10000, limit))
        lang = data.get("lang", config["default_lang"])
        resp_type = data.get("type", "json")
        payload = {
            "token": config["leakosint_token"],
            "request": query,
            "limit": limit,
            "lang": lang,
            "type": resp_type
        }
        r = requests.post(LEAKOSINT_API_URL, json=payload, timeout=30)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==================== TELEGRAM BOT ====================
bot = telebot.TeleBot(DEFAULT_BOT_TOKEN)

def perform_search(query, limit, lang):
    payload = {
        "token": config["leakosint_token"],
        "request": query,
        "limit": limit,
        "lang": lang,
        "type": "json"
    }
    try:
        r = requests.post(LEAKOSINT_API_URL, json=payload, timeout=30)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def format_results(data, query):
    if "error" in data:
        return f"❌ Error: {data['error']}"
    if "List" not in data:
        return "❌ Invalid response"
    dbs = data["List"]
    if "No results found" in dbs:
        return f"🔍 No results for: `{query}`"
    out = f"*Results for:* `{query}`\n\n"
    count = 0
    for db_name, db_data in dbs.items():
        if db_name == "No results found":
            continue
        out += f"📁 *{db_name}*\n"
        if "Data" in db_data and db_data["Data"]:
            for entry in db_data["Data"][:3]:
                for k, v in entry.items():
                    out += f"• *{k}*: {v}\n"
                out += "\n"
        count += 1
        if count >= 5:
            out += "\n_...more results. Use API._\n"
            break
    if len(out) > 4000:
        out = out[:3970] + "\n...truncated"
    return out

@bot.message_handler(commands=['start'])
def start_cmd(msg):
    uid = msg.from_user.id
    if is_admin(uid):
        txt = (
            "🤖 *Admin Bot* – full control\n\n"
            "/status – show current settings\n"
            "/settoken <token> – change leakosint token\n"
            "/enable – enable public API\n"
            "/disable – disable public API\n"
            "/setlimit <100‑10000> – default search limit\n"
            "/setlang <en/ru> – default language\n"
            "/search <query> – search leaks\n"
            "/addadmin <id> – add admin (owner only)\n"
            "/deladmin <id> – remove admin\n"
            "/balance – check token validity\n"
            "/help – this help"
        )
    else:
        txt = "🤖 *Bot*\n/search <query> – search leaks\n/status – API status"
    bot.reply_to(msg, txt, parse_mode="Markdown")

@bot.message_handler(commands=['help'])
def help_cmd(msg):
    start_cmd(msg)

@bot.message_handler(commands=['status'])
def status_cmd(msg):
    uid = msg.from_user.id
    admin = is_admin(uid)
    txt = f"API: `{'✅ ON' if config['api_enabled'] else '❌ OFF'}`\n"
    txt += f"Limit: `{config['default_limit']}`\nLang: `{config['default_lang']}`\n"
    if admin:
        token = config["leakosint_token"]
        masked = token[:6] + "..." + token[-4:] if len(token) > 10 else "***"
        txt += f"Token: `{masked}`\nAdmins: {', '.join(map(str, config['admin_ids']))}"
    bot.reply_to(msg, txt, parse_mode="Markdown")

@bot.message_handler(commands=['settoken'])
def settoken_cmd(msg):
    if not is_admin(msg.from_user.id):
        bot.reply_to(msg, "⛔ Admin only")
        return
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(msg, "Usage: /settoken <new_token>")
        return
    config["leakosint_token"] = parts[1].strip()
    save_config(config)
    bot.reply_to(msg, "✅ Leakosint token updated (saved permanently)")

@bot.message_handler(commands=['enable'])
def enable_cmd(msg):
    if not is_admin(msg.from_user.id):
        bot.reply_to(msg, "⛔ Admin only")
        return
    config["api_enabled"] = True
    save_config(config)
    bot.reply_to(msg, "✅ Public API enabled")

@bot.message_handler(commands=['disable'])
def disable_cmd(msg):
    if not is_admin(msg.from_user.id):
        bot.reply_to(msg, "⛔ Admin only")
        return
    config["api_enabled"] = False
    save_config(config)
    bot.reply_to(msg, "🔴 Public API disabled")

@bot.message_handler(commands=['setlimit'])
def setlimit_cmd(msg):
    if not is_admin(msg.from_user.id):
        bot.reply_to(msg, "⛔ Admin only")
        return
    parts = msg.text.split()
    if len(parts) != 2:
        bot.reply_to(msg, "Usage: /setlimit 100-10000")
        return
    try:
        lim = int(parts[1])
        if lim < 100 or lim > 10000:
            raise ValueError
        config["default_limit"] = lim
        save_config(config)
        bot.reply_to(msg, f"✅ Default limit set to {lim}")
    except:
        bot.reply_to(msg, "❌ Invalid limit (100‑10000)")

@bot.message_handler(commands=['setlang'])
def setlang_cmd(msg):
    if not is_admin(msg.from_user.id):
        bot.reply_to(msg, "⛔ Admin only")
        return
    parts = msg.text.split()
    if len(parts) != 2 or parts[1] not in ["en", "ru"]:
        bot.reply_to(msg, "Usage: /setlang en or ru")
        return
    config["default_lang"] = parts[1]
    save_config(config)
    bot.reply_to(msg, f"✅ Default language set to {parts[1]}")

@bot.message_handler(commands=['search'])
def search_cmd(msg):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(msg, "Usage: /search <query>")
        return
    query = parts[1]
    if not config["api_enabled"] and not is_admin(msg.from_user.id):
        bot.reply_to(msg, "❌ API is disabled")
        return
    bot.reply_to(msg, f"🔍 Searching `{query}`...", parse_mode="Markdown")
    result = perform_search(query, config["default_limit"], config["default_lang"])
    formatted = format_results(result, query)
    bot.reply_to(msg, formatted, parse_mode="Markdown")

@bot.message_handler(commands=['addadmin'])
def addadmin_cmd(msg):
    uid = msg.from_user.id
    if not config["admin_ids"] or uid != config["admin_ids"][0]:
        bot.reply_to(msg, "⛔ Only the owner can add admins")
        return
    parts = msg.text.split()
    if len(parts) != 2:
        bot.reply_to(msg, "Usage: /addadmin <user_id>")
        return
    try:
        new_id = int(parts[1])
        if new_id in config["admin_ids"]:
            bot.reply_to(msg, "Already an admin")
            return
        config["admin_ids"].append(new_id)
        save_config(config)
        bot.reply_to(msg, f"✅ Added {new_id} as admin")
    except:
        bot.reply_to(msg, "❌ Invalid user ID")

@bot.message_handler(commands=['deladmin'])
def deladmin_cmd(msg):
    uid = msg.from_user.id
    if not config["admin_ids"] or uid != config["admin_ids"][0]:
        bot.reply_to(msg, "⛔ Only the owner can remove admins")
        return
    parts = msg.text.split()
    if len(parts) != 2:
        bot.reply_to(msg, "Usage: /deladmin <user_id>")
        return
    try:
        rem_id = int(parts[1])
        if rem_id == config["admin_ids"][0]:
            bot.reply_to(msg, "❌ Cannot remove the owner")
            return
        if rem_id not in config["admin_ids"]:
            bot.reply_to(msg, "Not an admin")
            return
        config["admin_ids"].remove(rem_id)
        save_config(config)
        bot.reply_to(msg, f"✅ Removed admin {rem_id}")
    except:
        bot.reply_to(msg, "❌ Invalid user ID")

@bot.message_handler(commands=['balance'])
def balance_cmd(msg):
    if not is_admin(msg.from_user.id):
        bot.reply_to(msg, "⛔ Admin only")
        return
    try:
        test = {
            "token": config["leakosint_token"],
            "request": "test",
            "limit": 1,
            "lang": "en",
            "type": "json"
        }
        r = requests.post(LEAKOSINT_API_URL, json=test, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if "Error code" in data:
                bot.reply_to(msg, f"⚠️ API error: {data['Error code']}\nPossible invalid token or low balance.")
            else:
                bot.reply_to(msg, "✅ Token is valid. Balance not directly available via API.")
        else:
            bot.reply_to(msg, f"❌ HTTP {r.status_code} – token may be invalid.")
    except Exception as e:
        bot.reply_to(msg, f"❌ Check failed: {str(e)}")

@bot.message_handler(func=lambda m: True)
def fallback(msg):
    bot.reply_to(msg, "Unknown command. Type /help")

def run_bot():
    print("🤖 Bot polling started...")
    bot.infinity_polling(timeout=30, long_polling_timeout=30)

# ==================== MAIN ====================
if __name__ == "__main__":
    # Start bot in background thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    # Start Flask
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)