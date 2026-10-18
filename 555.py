import telebot
import time
import threading
import random
import json
import asyncio
import os
import sys
from datetime import datetime
from telebot import types

try:
    from telethon import TelegramClient, errors
    from telethon.tl.functions.payments import GetPaymentFormRequest, SendStarsFormRequest
    from telethon.tl.types import InputInvoiceStarGift, TextWithEntities
    TELEGRAM_IMPORT_OK = True
except ImportError:
    TELEGRAM_IMPORT_OK = False
    print("⚠️ Telethon не установлен. Установите: pip install telethon")

bot = telebot.TeleBot("8546284898:AAEBiTGLVNSAtyM8q-qKjHs4CvQA8Y03LsE")


AUTO_GAME_PAYOUT = True
GIFT_PRICE = 15
TARGET_GIFT_ID = 5170233102089322756

SESSION_NAME = "gift_sender_session"
API_ID = 37562082
API_HASH = "85c54cddefa6aed87398795d6e4c0170"

GIFT_SENDER_PHONE = None
gift_client = None
gift_client_ready = False
telethon_loop = None
pending_gifts = {}

captcha_users = {}

def generate_captcha():
    a = random.randint(1, 15)
    b = random.randint(1, 15)
    return a, b, a + b

DATA_FILE = "data.json"
ADMIN_IDS = [1965156455, 6445747495]
CHANNEL_ID = -1003666259959

data = {}
waiting_for_promocode = {}
user_task_index = {}
admin_states = {}
admin_mode = {}
game_sessions = {}
pending_withdrawals = {}  # {user_id: {"gift_price": price, "chat_id": chat_id, "message_id": message_id}}

def is_admin(user_id):
    return user_id in ADMIN_IDS

bot.set_my_commands([
    telebot.types.BotCommand("start", "🔄 Перезапустить бота"),
    telebot.types.BotCommand("myprofile", "👤 Профиль"),
    telebot.types.BotCommand("farmstars", "💸 Заработать"),
    telebot.types.BotCommand("withdraw", "🎁 Вывести"),
    telebot.types.BotCommand("game", "🧸 Мишки")
])

def load_data():
    global data, AUTO_GAME_PAYOUT
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except:
        data = {}

    defaults = {
        "balances": {}, "referred": [], "referral_times": {}, "banned": {},
        "completed_tasks": {}, "tasks": [], "promocodes": {}, "users": [],
        "referrals": {}, "used_promocodes": {}, "special_links": {},
        "user_sources": {}, "game_stats": {}, "game_history": [],
        "game_invoices": {}, "usernames": {}, "sponsor_channels": [],
        "subscribed_users": {}, "referral_bonuses": {}, "delayed_messages": [],
        "referral_reward": 2.5, "blocked_countries": [],
        "blocked_message": "🚫 К сожалению, ваш регион недоступен для использования бота.",
        "user_countries": {}, "required_clicks": {}, "user_channel_clicks": {},
        "sponsor_limits": {}, "sponsor_counts": {}, "completed_channels": {},
        "sponsor_users_per_channel": {}, "application_channels": [],
        "sponsor_application_channels": [], "selected_tickets": {}, "tournaments": [],
        "tournament_participants": {},
        "tournament_winners": {},
        "auto_gift_settings": {"enabled": True, "session_name": SESSION_NAME}
    }

    for key, val in defaults.items():
        if key not in data:
            data[key] = val

    AUTO_GAME_PAYOUT = data.get("auto_gift_settings", {}).get("enabled", True)

def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def save_gift_settings():
    data["auto_gift_settings"] = {"enabled": AUTO_GAME_PAYOUT, "session_name": SESSION_NAME}
    save_data()

# ============== ТУРНІРИ (тільки по витратах у Мишках) ==============

def get_active_tournaments():
    now = int(time.time())
    result = []
    for t in data.get("tournaments", []):
        if t.get("active", True) and t.get("end_time", 0) > now:
            result.append(t)
    return result


def get_tournament_by_id(tid):
    for t in data.get("tournaments", []):
        if str(t.get("id")) == str(tid):
            return t
    return None


def get_tournament_leaderboard(tid, limit=10):
    participants = data.get("tournament_participants", {}).get(str(tid), {})
    items = [(uid, p.get("spent", 0)) for uid, p in participants.items()]
    items.sort(key=lambda x: x[1], reverse=True)
    return items[:limit]


def register_tournament_participant(tid, user_id):
    """Реєструє учасника. baseline більше не потрібен — рахуємо ВСІ витрати за час турніру."""
    uid = str(user_id)
    tid = str(tid)
    if "tournament_participants" not in data:
        data["tournament_participants"] = {}
    if tid not in data["tournament_participants"]:
        data["tournament_participants"][tid] = {}
    if uid not in data["tournament_participants"][tid]:
        data["tournament_participants"][tid][uid] = {
            "spent": 0,
            "joined_at": int(time.time())
        }
        save_data()
        return True
    return False


def add_tournament_spent(user_id, amount):
    """Додає витрачені зірки у ВСІ активні турніри, де користувач бере участь."""
    uid = str(user_id)
    now = int(time.time())
    changed = False

    for t in data.get("tournaments", []):
        if not t.get("active", True):
            continue
        if t.get("end_time", 0) <= now:
            continue

        tid = str(t.get("id"))
        participants = data.get("tournament_participants", {}).get(tid, {})
        if uid not in participants:
            continue  # не учасник — не рахуємо

        participants[uid]["spent"] = participants[uid].get("spent", 0) + amount
        changed = True

    if changed:
        save_data()

def award_referral_bonus(user_id):
    uid = str(user_id)
    reward = data.get("referral_reward", 2.5)
    for ref_id, refs in data.get("referrals", {}).items():
        if uid in refs:
            if "referral_bonuses" not in data:
                data["referral_bonuses"] = {}
            if str(ref_id) not in data["referral_bonuses"]:
                data["referral_bonuses"][str(ref_id)] = []
            if uid not in data["referral_bonuses"][str(ref_id)]:
                add_balance(int(ref_id), reward)
                data["referral_bonuses"][str(ref_id)].append(uid)
                save_data()
                try:
                    bot.send_message(int(ref_id), f"🎉 Ваш реферал ({uid}) завершил регистрацию!\n💰 На баланс зачислено +{reward}⭐️")
                except:
                    pass
            break

def register_user(user, ref_id=None, source_link=None):
    user_id = str(user.id)
    if user_id in data["users"]:
        return
    data["users"].append(user_id)
    if user_id not in data["balances"]:
        data["balances"][user_id] = 0.0
    if "usernames" not in data:
        data["usernames"] = {}
    if user.username:
        data["usernames"][user_id] = user.username
    if ref_id:
        ref_id = str(ref_id)
        if ref_id != user_id:
            if ref_id not in data["referrals"]:
                data["referrals"][ref_id] = []
            if user_id not in data["referrals"][ref_id]:
                data["referrals"][ref_id].append(user_id)
                if "referral_times" not in data:
                    data["referral_times"] = {}
                if ref_id not in data["referral_times"]:
                    data["referral_times"][ref_id] = {}
                data["referral_times"][ref_id][user_id] = int(time.time())
    if source_link and user_id not in data["user_sources"]:
        data["user_sources"][user_id] = source_link
    save_data()

load_data()


def send_win_notification_to_channel(user_id, username, gift_price=15, auto_payout_done=False):
    current_time = datetime.now().strftime("%H:%M:%S")
    username_display = f"@{username}" if username and username != "без username" else "без username"

    if auto_payout_done:
        # Якщо автовивід спрацював - відправляємо повідомлення БЕЗ кнопок
        message_text = (
            f"🎰 НОВЫЙ ВЫИГРЫШ В ИГРЕ «МИШКИ»\n\n"
            f"👤 {username_display}\n"
            f"🆔 ID: {user_id}\n"
            f"🎁 Приз: {gift_price}⭐️\n"
            f"🕐 Время: {current_time}\n\n"
            f"✅ ПОДАРОК АВТОМАТИЧЕСКИ ОТПРАВЛЕН!"
        )

        try:
            bot.send_message(CHANNEL_ID, message_text, parse_mode="HTML")
            print(f"✅ Уведомление (с автовыводом) отправлено в канал для {user_id}")
        except Exception as e:
            print(f"❌ Ошибка отправки уведомления: {e}")

    else:
        # Якщо автовивід НЕ спрацював - відправляємо повідомлення З КНОПКАМИ
        message_text = (
            f"🎰 НОВЫЙ ВЫИГРЫШ В ИГРЕ «МИШКИ»\n\n"
            f"👤 {username_display}\n"
            f"🆔 ID: {user_id}\n"
            f"🎁 Приз: {gift_price}⭐️\n"
            f"🕐 Время: {current_time}\n\n"
            f"⚠️ АВТОВЫВОД НЕ УДАЛСЯ - ТРЕБУЕТСЯ РУЧНАЯ ВЫДАЧА"
        )

        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ Выведено", callback_data=f"game_done_{user_id}"),
            types.InlineKeyboardButton("❌ Отклонить", callback_data=f"game_reject_{user_id}"),
            types.InlineKeyboardButton("🔍 Профиль", callback_data=f"profile_from_gift_{user_id}")
        )
        markup.add(types.InlineKeyboardButton("🎁 Выдать вручную", callback_data=f"manual_gift_{user_id}"))

        try:
            bot.send_message(CHANNEL_ID, message_text, reply_markup=markup, parse_mode="HTML")
            print(f"✅ Уведомление (ошибка автовывода) отправлено в канал для {user_id}")
        except Exception as e:
            print(f"❌ Ошибка отправки уведомления: {e}")


@bot.callback_query_handler(func=lambda call: call.data.startswith("manual_gift_"))
def manual_gift_handler(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ Не админ", show_alert=True)
        return

    user_id = int(call.data.replace("manual_gift_", ""))

    if AUTO_GAME_PAYOUT and gift_client_ready:
        if send_gift_sync(user_id, TARGET_GIFT_ID, f"🎉 Администратор вручил вам подарок за победу в игре «Мишки»!"):
            bot.send_message(user_id, "🎉 Администратор вручил вам подарок за победу в игре «Мишки»! Поздравляем!")
            bot.answer_callback_query(call.id, "✅ Подарок отправлен!")

            try:
                bot.edit_message_text(
                    call.message.text + "\n\n✅ ВЫДАНО ВРУЧНУЮ АДМИНОМ",
                    call.message.chat.id,
                    call.message.message_id,
                    parse_mode="HTML"
                )
            except:
                pass

            if user_id in pending_withdrawals:
                del pending_withdrawals[user_id]
        else:
            bot.answer_callback_query(call.id, "❌ Ошибка отправки!", show_alert=True)
            bot.send_message(call.message.chat.id, "❌ Не удалось отправить подарок. Проверьте клиент.")
    else:
        bot.answer_callback_query(call.id, "❌ Автовывод недоступен", show_alert=True)


async def async_init_telethon():
    global gift_client, gift_client_ready
    session_file = f"{SESSION_NAME}.session"
    if not os.path.exists(session_file):
        print(f"[WARNING] Сессия не найдена: {session_file}. Автовывод отключён.")
        return
    try:
        gift_client = TelegramClient(session_file, API_ID, API_HASH)
        await gift_client.connect()
        if not await gift_client.is_user_authorized():
            print("[WARNING] Сессия недействительна. Автовывод отключён.")
            await gift_client.disconnect()
            return
        me = await gift_client.get_me()
        print(f"✅ Telethon: {me.first_name} (ID: {me.id})")
        global GIFT_SENDER_PHONE
        if hasattr(me, 'phone') and me.phone:
            GIFT_SENDER_PHONE = me.phone
        gift_client_ready = True
    except Exception as e:
        print(f"[WARNING] Ошибка Telethon: {e}. Автовывод отключён.")

def start_telethon_loop():
    global telethon_loop
    if not TELEGRAM_IMPORT_OK:
        print("❌ Telethon не установлен")
        return
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    telethon_loop = loop
    try:
        loop.run_until_complete(async_init_telethon())
        loop.run_forever()
    except Exception as e:
        print(f"❌ Ошибка в потоке Telethon: {e}")

async def async_send_gift(recipient_id, gift_id, message_text=""):
    global gift_client
    if not gift_client_ready or gift_client is None:
        raise Exception("Клиент Telethon не готов")
    try:
        recipient = await gift_client.get_entity(int(recipient_id))
    except ValueError:
        uid_str = str(recipient_id)
        username = data.get("usernames", {}).get(uid_str, None)
        if username:
            recipient = await gift_client.get_entity(username)
        else:
            raise ValueError("User not found")
    msg = TextWithEntities(text=message_text, entities=[]) if message_text else None
    input_peer = await gift_client.get_input_entity(recipient)
    invoice = InputInvoiceStarGift(peer=input_peer, gift_id=int(gift_id), message=msg)
    form = await gift_client(GetPaymentFormRequest(invoice=invoice))
    await gift_client(SendStarsFormRequest(form_id=form.form_id, invoice=invoice))
    return True

def send_gift_sync(recipient_id, gift_id, message_text=""):
    global gift_client_ready, telethon_loop
    if not gift_client_ready or not telethon_loop:
        return False
    try:
        future = asyncio.run_coroutine_threadsafe(async_send_gift(recipient_id, gift_id, message_text), telethon_loop)
        return future.result(timeout=30)
    except Exception as e:
        print(f"❌ Ошибка отправки подарка: {e}")
        return False

def restart_gift_client():
    global gift_client, gift_client_ready, telethon_loop
    if telethon_loop and telethon_loop.is_running():
        telethon_loop.call_soon_threadsafe(telethon_loop.stop)
        time.sleep(1)
    gift_client_ready = False
    gift_client = None
    threading.Thread(target=start_telethon_loop, daemon=True).start()
    timeout = 30
    start_time = time.time()
    while not gift_client_ready and (time.time() - start_time) < timeout:
        time.sleep(0.5)
    return gift_client_ready

def is_banned(user_id):
    return str(user_id) in data["banned"]

def add_channel_click(user_id, channel_id):
    uid = str(user_id)
    if "user_channel_clicks" not in data:
        data["user_channel_clicks"] = {}
    if uid not in data["user_channel_clicks"]:
        data["user_channel_clicks"][uid] = {}
    data["user_channel_clicks"][uid][channel_id] = data["user_channel_clicks"][uid].get(channel_id, 0) + 1
    save_data()

def is_user_registered(user_id):
    return str(user_id) in data["users"]

def get_balance(user_id):
    return data["balances"].get(str(user_id), 0.0)

def add_balance(user_id, amount):
    uid = str(user_id)
    data["balances"][uid] = get_balance(user_id) + float(amount)
    save_data()

def search_user_local(username):
    if "usernames" not in data:
        return None
    username = username.lower().replace("@", "")
    for uid, uname in data["usernames"].items():
        if uname.lower() == username:
            return uid
    return None

def check_subscriptions(user_id):
    channels = data.get("sponsor_channels", [])
    not_subscribed = []
    limits = data.get("sponsor_limits", {})
    counts = data.get("sponsor_counts", {})
    app_channels = [ch["id"] for ch in data.get("sponsor_application_channels", [])]
    for channel in channels:
        channel_id = channel["id"]
        limit = limits.get(channel_id, 0)
        if limit > 0 and counts.get(channel_id, 0) >= limit:
            continue
        try:
            member = bot.get_chat_member(channel_id, user_id)
            if channel_id in app_channels:
                if member.status not in ["restricted", "member", "administrator", "creator"]:
                    not_subscribed.append(channel)
            else:
                if member.status not in ["member", "administrator", "creator"]:
                    not_subscribed.append(channel)
        except:
            not_subscribed.append(channel)
    return not_subscribed

def is_country_blocked(user_id):
    blocked = data.get("blocked_countries", [])
    if not blocked:
        return False
    try:
        user_info = bot.get_chat(user_id)
        lang_code = getattr(user_info, 'language_code', None)
        if lang_code and lang_code in blocked:
            return True
    except:
        pass
    return False

def show_subscription_check(message, user_id):
    not_subscribed = check_subscriptions(user_id)
    if not not_subscribed:
        if str(user_id) not in data.get("subscribed_users", {}):
            data["subscribed_users"][str(user_id)] = True
            save_data()
            award_referral_bonus(user_id)
        back_to_menu(message)
        threading.Thread(target=send_tasks_after_delay, args=(message.chat.id, user_id, 3)).start()
        return
    text = "📢 <b>Для использования бота подпишитесь на каналы:</b>\n\n"
    markup = types.InlineKeyboardMarkup(row_width=1)
    for channel in not_subscribed:
        text += f"• {channel['name']}\n"
        markup.add(types.InlineKeyboardButton(channel.get('button_text', f"📢 {channel['name']}"), url=channel['link']))
    markup.add(types.InlineKeyboardButton("✅ Проверить подписку", callback_data="check_subscription"))
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")

def send_task(chat_id, user_id):
    uid = str(user_id)
    tasks = data["tasks"]
    if not tasks:
        bot.send_message(chat_id, "❌ Доступных заданий пока нет")
        return
    if uid not in user_task_index:
        user_task_index[uid] = 0
    while True:
        idx = user_task_index[uid]
        if idx >= len(tasks):
            bot.send_message(chat_id, "❌ Доступных заданий пока нет\nПроверьте через несколько часов")
            return
        task = tasks[idx]
        channel_id = task["channel_id"]
        if "completed_channels" not in data:
            data["completed_channels"] = {}
        if uid not in data["completed_channels"]:
            data["completed_channels"][uid] = []
        if channel_id in data["completed_channels"][uid] or task.get("uses", 0) >= task.get("max_uses", 999999):
            user_task_index[uid] += 1
            continue
        break
    task = tasks[idx]
    task_text = task.get('text', '⭐️ Собирай звёзды за выполнение простых заданий')
    task_link = task.get('link', '')
    text = f"{task_text}\n\n📌 Для выполнения задания нужно подписаться на канал:\n{task_link}\n\n🎁 Награда: {task['reward']}⭐️\n📊 Выполнено: {task.get('uses', 0)}/{task.get('max_uses', '∞')}"
    markup = types.InlineKeyboardMarkup()
    if task_link and (task_link.startswith("http") or task_link.startswith("t.me/")):
        if not task_link.startswith("http"):
            task_link = "https://" + task_link
        markup.row(types.InlineKeyboardButton("🔗 Перейти", url=task_link), types.InlineKeyboardButton("⏭️ Пропустить", callback_data="skip_task"))
    else:
        markup.row(types.InlineKeyboardButton("⏭️ Пропустить", callback_data="skip_task"))
    markup.row(types.InlineKeyboardButton("✅ Подтвердить", callback_data="check_task"))
    bot.send_message(chat_id, text, reply_markup=markup)

def send_tasks_after_delay(chat_id, user_id, delay=3):
    time.sleep(delay)
    send_task(chat_id, user_id)
    delayed_msgs = data.get("delayed_messages", [])
    if delayed_msgs:
        def send_delayed():
            for msg in delayed_msgs:
                try:
                    time.sleep(msg['delay'])
                    markup = None
                    if msg.get('buttons'):
                        markup = types.InlineKeyboardMarkup()
                        for btn in msg['buttons']:
                            markup.add(types.InlineKeyboardButton(btn['text'], url=btn['url']))
                    if msg.get('photo'):
                        bot.send_photo(chat_id, msg['photo'], caption=msg['text'], parse_mode="HTML", reply_markup=markup)
                    else:
                        bot.send_message(chat_id, msg['text'], parse_mode="HTML", reply_markup=markup)
                except:
                    pass
        threading.Thread(target=send_delayed).start()

# ============================================================
# КОМАНДИ
# ============================================================
@bot.message_handler(commands=['start'])
def start(message):
    user_id = str(message.from_user.id)
    if is_country_blocked(message.from_user.id):
        bot.send_message(message.chat.id, data.get("blocked_message", "🚫 Ваш регион недоступен."))
        return
    if is_banned(message.from_user.id):
        bot.send_message(message.chat.id, f"🚫 Вы заблокированы\n\nПричина: {data['banned'].get(user_id, 'без причины')}")
        return
    args = message.text.split()
    start_arg = args[1] if len(args) > 1 else None
    if user_id not in data["users"]:
        captcha_users[message.from_user.id] = {"answer": None, "start_arg": start_arg}
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("🧸 Мишки по 5 звёзд", callback_data="first_choice_game"),
            types.InlineKeyboardButton("🤖 Пройти капчу и войти", callback_data="first_choice_captcha")
        )
        bot.send_message(message.chat.id, "🎉 <b>Добро пожаловать!</b>\n\nВыберите, что хотите сделать:", reply_markup=markup, parse_mode="HTML")
        return
    if start_arg and not start_arg.isdigit() and start_arg in data.get("special_links", {}):
        data["special_links"][start_arg]["clicks"] = data["special_links"][start_arg].get("clicks", 0) + 1
        if user_id not in data["user_sources"]:
            data["user_sources"][user_id] = start_arg
        save_data()
    if data.get("sponsor_channels") and check_subscriptions(message.from_user.id):
        show_subscription_check(message, message.from_user.id)
        return
    back_to_menu(message)
    threading.Thread(target=send_tasks_after_delay, args=(message.chat.id, message.from_user.id, 3)).start()

@bot.callback_query_handler(func=lambda call: call.data == "first_choice_game")
def first_choice_game(call):
    if call.from_user.id not in captcha_users:
        bot.answer_callback_query(call.id, "❌ Сессия истекла")
        return
    start_arg = captcha_users[call.from_user.id].get("start_arg")
    del captcha_users[call.from_user.id]
    ref_id = None
    source_link = None
    if start_arg:
        if start_arg.isdigit():
            ref_id = int(start_arg)
        elif start_arg in data.get("special_links", {}):
            source_link = start_arg
    register_user(call.from_user, ref_id=ref_id, source_link=source_link)
    bot.delete_message(call.message.chat.id, call.message.message_id)
    game_menu(call.message)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "first_choice_captcha")
def first_choice_captcha(call):
    if call.from_user.id not in captcha_users:
        bot.answer_callback_query(call.id, "❌ Сессия истекла")
        return
    a, b, answer = generate_captcha()
    captcha_users[call.from_user.id]["answer"] = answer
    bot.delete_message(call.message.chat.id, call.message.message_id)
    bot.send_message(call.message.chat.id, f"🤖 Подтвердите, что вы не робот.\n\nРешите пример:\n<b>{a} + {b} = ?</b>", parse_mode="HTML")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "check_subscription")
def check_subscription_callback(call):
    user_id = call.from_user.id
    uid = str(user_id)
    not_subscribed = check_subscriptions(user_id)
    if not not_subscribed:
        for channel in data.get("sponsor_channels", []):
            channel_id = channel["id"]
            try:
                if bot.get_chat_member(channel_id, user_id).status in ["member", "administrator", "creator"]:
                    if "sponsor_counts" not in data:
                        data["sponsor_counts"] = {}
                    if "sponsor_users_per_channel" not in data:
                        data["sponsor_users_per_channel"] = {}
                    if channel_id not in data["sponsor_users_per_channel"]:
                        data["sponsor_users_per_channel"][channel_id] = []
                    if uid not in data["sponsor_users_per_channel"][channel_id]:
                        data["sponsor_counts"][channel_id] = data["sponsor_counts"].get(channel_id, 0) + 1
                        data["sponsor_users_per_channel"][channel_id].append(uid)
            except:
                pass
        if uid not in data.get("subscribed_users", {}):
            data["subscribed_users"][uid] = True
            award_referral_bonus(user_id)
        save_data()
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        bot.send_message(call.message.chat.id, "✅ Спасибо за подписку!")
        back_to_menu(call.message)
        threading.Thread(target=send_tasks_after_delay, args=(call.message.chat.id, user_id, 3)).start()
    else:
        bot.answer_callback_query(call.id, "❌ Вы подписались не на все каналы!", show_alert=True)
    try:
        bot.answer_callback_query(call.id)
    except:
        pass

@bot.message_handler(func=lambda message: message.from_user.id in captcha_users)
def captcha_check(message):
    user_id = message.from_user.id
    if not message.text or not message.text.isdigit():
        bot.reply_to(message, "❌ Введите только число.")
        return
    if captcha_users[user_id].get("answer") is None:
        bot.reply_to(message, "❌ Сначала выберите действие.")
        return
    if int(message.text) != captcha_users[user_id]["answer"]:
        a, b, answer = generate_captcha()
        captcha_users[user_id]["answer"] = answer
        bot.send_message(message.chat.id, f"❌ Неправильный ответ.\n\nПопробуйте ещё раз:\n<b>{a} + {b} = ?</b>", parse_mode="HTML")
        return
    start_arg = captcha_users[user_id]["start_arg"]
    del captcha_users[user_id]
    if is_country_blocked(user_id):
        bot.send_message(message.chat.id, data.get("blocked_message", "🚫 Ваш регион недоступен."))
        return
    ref_id = None
    source_link = None
    if start_arg:
        if start_arg.isdigit():
            ref_id = int(start_arg)
        elif start_arg in data.get("special_links", {}):
            data["special_links"][start_arg]["clicks"] = data["special_links"][start_arg].get("clicks", 0) + 1
            save_data()
            source_link = start_arg
    register_user(message.from_user, ref_id=ref_id, source_link=source_link)
    bot.send_message(message.chat.id, "✅ Капча пройдена успешно!")
    if data.get("sponsor_channels") and check_subscriptions(user_id):
        show_subscription_check(message, user_id)
    else:
        back_to_menu(message)
        threading.Thread(target=send_tasks_after_delay, args=(message.chat.id, user_id, 3)).start()

# ============================================================
# МЕНЮ
# ============================================================
def back_to_menu(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("👤 Профиль", "📋 Задания")
    markup.row("💸 Заработать ⭐️", "🎁 Вывести ⭐️")
    markup.row("🧸 Мишки")   # ← додано
    markup.row("🏆 Турниры")
    if is_admin(message.from_user.id):
        markup.row("⚙️ Админ панель")
    bot.send_message(message.chat.id, "⬅️ Главное меню", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "⬅️ Назад")
def back_button(message):
    back_to_menu(message)

@bot.message_handler(func=lambda m: m.text == "👤 Профиль")
def profile_btn(message):
    if is_banned(message.from_user.id) or not is_user_registered(message.from_user.id):
        bot.send_message(message.chat.id, "❌ Сначала пройдите регистрацию! /start")
        return
    if data.get("sponsor_channels") and check_subscriptions(message.from_user.id):
        show_subscription_check(message, message.from_user.id)
        return
    my_profile(message)

@bot.message_handler(func=lambda m: m.text == "💸 Заработать ⭐️")
def farm_btn(message):
    if is_banned(message.from_user.id) or not is_user_registered(message.from_user.id):
        bot.send_message(message.chat.id, "❌ Сначала пройдите регистрацию! /start")
        return
    if data.get("sponsor_channels") and check_subscriptions(message.from_user.id):
        show_subscription_check(message, message.from_user.id)
        return
    farm_stars(message)

@bot.message_handler(func=lambda m: m.text == "🎁 Вывести ⭐️")
def withdraw_btn(message):
    if is_banned(message.from_user.id) or not is_user_registered(message.from_user.id):
        bot.send_message(message.chat.id, "❌ Сначала пройдите регистрацию! /start")
        return
    if data.get("sponsor_channels") and check_subscriptions(message.from_user.id):
        show_subscription_check(message, message.from_user.id)
        return
    withdraw(message)

@bot.message_handler(func=lambda m: m.text == "📋 Задания")
def tasks_button(message):
    if is_banned(message.from_user.id) or not is_user_registered(message.from_user.id):
        bot.send_message(message.chat.id, "❌ Сначала пройдите регистрацию! /start")
        return
    if data.get("sponsor_channels") and check_subscriptions(message.from_user.id):
        show_subscription_check(message, message.from_user.id)
        return
    user_task_index[str(message.from_user.id)] = 0
    send_task(message.chat.id, message.from_user.id)

@bot.message_handler(commands=['myprofile'])
def myprofile_command(message):
    profile_btn(message)

@bot.message_handler(commands=['farmstars'])
def farmstars_command(message):
    farm_btn(message)

@bot.message_handler(commands=['withdraw'])
def withdraw_command(message):
    withdraw_btn(message)

@bot.message_handler(commands=['game'])
def game_command(message):
    if is_banned(message.from_user.id):
        return
    game_menu(message)

def my_profile(message):
    uid = str(message.from_user.id)
    balance = get_balance(message.from_user.id)
    ref_count = len(data.get("referrals", {}).get(uid, []))
    tasks_done = len(data.get("completed_channels", {}).get(uid, []))
    text = (
        f"👤 *Ваш профиль*\n\n"
        f"💰 Баланс: *{balance}⭐️*\n"
        f"👥 Рефералов: *{ref_count}*\n"
        f"📋 Заданий выполнено: *{tasks_done}*\n\n"
        f"📊 Здесь собрана вся информация о вас"
    )
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("🎁 Ввести промокод", callback_data="promokod"))
    markup.row(types.InlineKeyboardButton("📢 Канал проекта", url="https://t.me/TestDChenel"))
    markup.row(types.InlineKeyboardButton("👥 Где брать друзей", url="https://t.me/TestDChenel/2"))
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "promokod")
def promocode_button(call):
    waiting_for_promocode[call.from_user.id] = True
    bot.send_message(call.message.chat.id, "🎁 Введите промокод:")
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: m.from_user.id in waiting_for_promocode)
def enter_promocode(message):
    if not is_user_registered(message.from_user.id):
        bot.send_message(message.chat.id, "❌ Сначала пройдите регистрацию! /start")
        waiting_for_promocode.pop(message.from_user.id, None)
        return
    user_id = message.from_user.id
    code = message.text.upper()
    waiting_for_promocode.pop(user_id, None)
    promocodes = data["promocodes"]
    if code not in promocodes:
        bot.send_message(message.chat.id, "❌ Неверный промокод")
        return
    promo = promocodes[code]
    if promo["uses"] >= promo["max_uses"]:
        bot.send_message(message.chat.id, "❌ Лимит активаций исчерпан")
        return
    uid = str(user_id)
    if "used_promocodes" not in data:
        data["used_promocodes"] = {}
    if uid in data["used_promocodes"] and code in data["used_promocodes"][uid]:
        bot.send_message(message.chat.id, "❌ Вы уже использовали этот промокод")
        return
    add_balance(user_id, promo["reward"])
    promo["uses"] += 1
    if uid not in data["used_promocodes"]:
        data["used_promocodes"][uid] = []
    data["used_promocodes"][uid].append(code)
    save_data()
    bot.send_message(message.chat.id, f"🎉 Успех! +{promo['reward']}⭐️")

def farm_stars(message):
    uid = str(message.from_user.id)
    ref_link = f"https://t.me/{bot.get_me().username}?start={uid}"
    ref_count = len(data.get("referrals", {}).get(uid, []))
    reward = data.get("referral_reward", 2.5)
    bot.send_message(
        message.chat.id,
        f"💸 *Заработок*\n\nПриглашай друзей и получай *+{reward}⭐️* за каждого!\n\n👥 Приглашено: *{ref_count}*\n\n🔗 Твоя ссылка:\n`{ref_link}`",
        parse_mode="Markdown"
    )

def withdraw(message):
    balance = get_balance(message.from_user.id)
    text = (
        f"🎁 <b>Вывод звёзд</b>\n\n"
        f"💰 Ваш баланс: <b>{balance}⭐️</b>\n\n"
        f"⚠️ <b>Важно!</b>\nДля вывода звёзд сначала напишите администратору:\n👤 @Chipka_Ckyn\n\nПосле этого нажмите кнопку ниже:"
    )
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("👤 Написать администратору", url="https://t.me/Chipka_Ckyn"))
    markup.row(types.InlineKeyboardButton("✅ Я написал(а), продолжить", callback_data="continue_withdraw"))
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == "continue_withdraw")
def continue_withdraw(call):
    balance = get_balance(call.from_user.id)
    text = f"🎁 <b>Вывод</b>\n\n💰 Баланс: <b>{balance}⭐️</b>\n\nВыбери подарок:"
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("15⭐️💝", callback_data="gift_15"), types.InlineKeyboardButton("15⭐️🧸", callback_data="gift_15"))
    markup.row(types.InlineKeyboardButton("25⭐️🌹", callback_data="gift_25"), types.InlineKeyboardButton("25⭐️🎁", callback_data="gift_25"))
    markup.row(types.InlineKeyboardButton("50⭐️🍾", callback_data="gift_50"), types.InlineKeyboardButton("50⭐️💐", callback_data="gift_50"))
    markup.row(types.InlineKeyboardButton("50⭐️🚀", callback_data="gift_50"), types.InlineKeyboardButton("50⭐️🎂", callback_data="gift_50"))
    markup.row(types.InlineKeyboardButton("100⭐️💎", callback_data="gift_100"), types.InlineKeyboardButton("100⭐️🏆", callback_data="gift_100"))
    markup.row(types.InlineKeyboardButton("100⭐️💍", callback_data="gift_100"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="HTML")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("gift_") and not call.data.startswith("gift_client"))
def handle_gift(call):
    user_id = call.from_user.id
    username = call.from_user.username or "нет username"
    balance = get_balance(user_id)
    if "15" in call.data:
        price = 15
    elif "25" in call.data:
        price = 25
    elif "50" in call.data:
        price = 50
    elif "100" in call.data:
        price = 100
    else:
        return
    if balance < price:
        bot.answer_callback_query(call.id, "❌ Недостаточно ⭐️", show_alert=True)
        return
    add_balance(user_id, -price)
    text = f"📥 НОВЫЙ ВЫВОД\n\n👤 @{username}\n🆔 ID: <code>{user_id}</code>\n🎁 Подарок: {price}⭐️"
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("✅ Выведено", callback_data=f"done_{user_id}"), types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{user_id}"))
    markup.row(types.InlineKeyboardButton("🔍 Профиль", callback_data=f"profile_from_gift_{user_id}"))
    bot.send_message(CHANNEL_ID, text, reply_markup=markup, parse_mode="HTML")
    bot.send_message(call.message.chat.id, "✅ Ваша заявка успешно принята\nОжидайте решения модерации (до 24 часов)\nКанал заявок: @WithdrawEgik")
    bot.answer_callback_query(call.id)

# ============================================================
# ЗАДАНИЯ
# ============================================================
@bot.callback_query_handler(func=lambda call: call.data == "skip_task")
def skip_task(call):
    uid = str(call.from_user.id)
    idx = user_task_index.get(uid, 0)
    if idx < len(data["tasks"]):
        task = data["tasks"][idx]
        channel_id = task.get("channel_id")
        if channel_id:
            add_channel_click(call.from_user.id, channel_id)
    if uid in user_task_index:
        user_task_index[uid] += 1
    send_task(call.message.chat.id, call.from_user.id)

@bot.callback_query_handler(func=lambda call: call.data == "check_task")
def check_task(call):
    user_id = call.from_user.id
    uid = str(user_id)
    tasks = data["tasks"]
    if not tasks:
        bot.answer_callback_query(call.id, "Нет заданий", show_alert=True)
        return
    idx = user_task_index.get(uid, 0)
    if idx >= len(tasks):
        bot.answer_callback_query(call.id, "Нет доступных заданий", show_alert=True)
        return
    task = tasks[idx]
    channel_id = task["channel_id"]
    if "completed_channels" not in data:
        data["completed_channels"] = {}
    if uid not in data["completed_channels"]:
        data["completed_channels"][uid] = []
    if channel_id in data["completed_channels"][uid]:
        bot.answer_callback_query(call.id, "Вы уже выполнили это задание", show_alert=True)
        user_task_index[uid] += 1
        send_task(call.message.chat.id, user_id)
        return
    try:
        member = bot.get_chat_member(channel_id, user_id)
        app_channels = data.get("application_channels", [])
        is_application_channel = any(ch["id"] == channel_id for ch in app_channels)
        if is_application_channel:
            statuses = ["restricted", "member", "administrator", "creator"]
        else:
            statuses = ["member", "administrator", "creator"]
        if member.status in statuses:
            reward = task["reward"]
            add_balance(user_id, reward)
            task["uses"] = task.get("uses", 0) + 1
            if uid in data.get("user_sources", {}):
                src = data["user_sources"][uid]
                if src in data.get("special_links", {}):
                    data["special_links"][src]["task_completions"] = data["special_links"][src].get("task_completions", 0) + 1
            data["completed_channels"][uid].append(channel_id)
            save_data()
            bot.send_message(call.message.chat.id, f"🎉 Задание выполнено! +{reward}⭐️")
            user_task_index[uid] = idx + 1
            send_task(call.message.chat.id, user_id)
        else:
            msg = "Вы не подали заявку в канал" if is_application_channel else "Вы не подписаны на канал"
            bot.answer_callback_query(call.id, msg, show_alert=True)
    except Exception as e:
        bot.answer_callback_query(call.id, "Ошибка проверки. Возможно, бот не в канале.", show_alert=True)
        print(e)

# ============================================================
# ИГРА «МИШКИ»
# ============================================================
@bot.message_handler(func=lambda m: m.text == "🧸 Мишки")
def game_menu(message):
    if is_banned(message.from_user.id):
        return
    text = "🧸 Мишки по 5 звёзд(игра)"
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("🎲 Перейти к оплате", callback_data="play_game"))
    bot.send_message(message.chat.id, text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "play_game")
def start_game(call):
    user_id = call.from_user.id
    prices = [types.LabeledPrice(label="Мишки по 5 звезд(игра)", amount=5)]
    bot.send_invoice(
        chat_id=call.message.chat.id,
        title="«🧸 Мишки»",
        description="Подтверждение оплаты",
        invoice_payload=f"game_attempt_{user_id}",
        provider_token="",
        currency="XTR",
        prices=prices,
        start_parameter="game_start"
    )
    bot.answer_callback_query(call.id)

def tournaments_menu(message):
    active = get_active_tournaments()
    markup = types.InlineKeyboardMarkup(row_width=1)

    if not active:
        text = "🏆 <b>Турниры</b>\n\n😔 Сейчас нет активных турниров.\nСледите за новостями!"
        markup.add(types.InlineKeyboardButton("👥 Мои турниры", callback_data="my_tournaments"))
        bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")
        return

    text = "🏆 <b>Активные турниры</b>\n\nПобеждает тот, кто больше звёзд потратил в игре «Мишки»!\n\n"
    now = int(time.time())
    for i, t in enumerate(active, 1):
        end_in = t.get("end_time", 0) - now
        days = end_in // 86400
        hours = (end_in % 86400) // 3600
        time_left = f"{days}д {hours}ч" if days > 0 else f"{hours}ч"

        participants = data.get("tournament_participants", {}).get(str(t.get("id")), {})
        text += f"{i}. <b>{t.get('title', 'Турнир')}</b>\n"
        text += f"   🎁 Приз: {t.get('prize', 'не указан')}\n"
        text += f"   👥 Участников: {len(participants)}\n"
        text += f"   ⏰ Осталось: {time_left}\n\n"

        markup.add(types.InlineKeyboardButton(
            f"🏆 {t.get('title', 'Турнир')}",
            callback_data=f"tournament_view_{t.get('id')}"
        ))

    markup.add(types.InlineKeyboardButton("👥 Мои турниры", callback_data="my_tournaments"))
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")


def tournament_view(chat_id, tid, user_id):
    t = get_tournament_by_id(tid)
    if not t:
        bot.send_message(chat_id, "❌ Турнир не найден")
        return

    now = int(time.time())
    end_in = t.get("end_time", 0) - now
    if end_in <= 0:
        status = "⏹ Завершён"
    elif not t.get("active", True):
        status = "⏸ Остановлен"
    else:
        status = "🟢 Активен"

    days = max(0, end_in) // 86400
    hours = (max(0, end_in) % 86400) // 3600
    minutes = (max(0, end_in) % 3600) // 60
    time_left = f"{days}д {hours}ч {minutes}м"

    text = f"🏆 <b>{t.get('title', 'Турнир')}</b>\n\n"
    if t.get('description'):
        text += f"📝 {t.get('description')}\n\n"
    text += f"🎁 <b>Приз:</b> {t.get('prize', 'не указан')}\n"
    text += f"📊 <b>Статус:</b> {status}\n"
    if status == "🟢 Активен":
        text += f"⏰ <b>До конца:</b> {time_left}\n"
    text += f"📈 <b>Считается:</b> потрачено ⭐️ в игре «Мишки»\n\n"

    leaderboard = get_tournament_leaderboard(tid, limit=10)
    if leaderboard:
        text += "🏅 <b>Топ участников:</b>\n"
        for i, (uid, spent) in enumerate(leaderboard, 1):
            uname = data.get("usernames", {}).get(uid, uid)
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            text += f"{medal} @{uname} — {spent}⭐️\n"
    else:
        text += "🏅 <b>Топ участников:</b> пока никого\n"

    uid = str(user_id)
    participants = data.get("tournament_participants", {}).get(str(tid), {})
    if uid in participants:
        my_spent = participants[uid].get("spent", 0)
        text += f"\n👤 <b>Ваш результат:</b> {my_spent}⭐️"

    markup = types.InlineKeyboardMarkup(row_width=1)
    if status == "🟢 Активен":
        if uid in participants:
            markup.add(types.InlineKeyboardButton("✅ Вы участвуете", callback_data=f"tournament_already_{tid}"))
        else:
            markup.add(types.InlineKeyboardButton("🎯 Участвовать", callback_data=f"tournament_join_{tid}"))
    markup.add(types.InlineKeyboardButton("🔄 Обновить", callback_data=f"tournament_view_{tid}"))
    markup.add(types.InlineKeyboardButton("⬅ Назад", callback_data="tournament_back"))

    bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")

def admin_tournaments_menu(message):
    active = get_active_tournaments()
    total = len(data.get("tournaments", []))
    text = f"🏆 <b>Управление турнирами</b>\n\n"
    text += f"🟢 Активных: {len(active)}\n"
    text += f"📋 Всего: {total}\n\n"
    text += "📈 Метрика: потрачено ⭐️ в игре «Мишки»\n\n"
    text += "Выберите действие:"

    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("➕ Создать турнир", callback_data="admin_tour_create"),
        types.InlineKeyboardButton("📋 Список турниров", callback_data="admin_tour_list"),
        types.InlineKeyboardButton("🗑 Удалить турнир", callback_data="admin_tour_delete_menu"),
        types.InlineKeyboardButton("⏸ Остановить/Возобновить", callback_data="admin_tour_toggle_menu"),
        types.InlineKeyboardButton("🏁 Завершить и объявить победителей", callback_data="admin_tour_finish_menu"),
        types.InlineKeyboardButton("📢 Оповестить участников", callback_data="admin_tour_notify_menu"),
        types.InlineKeyboardButton("⬅ Назад", callback_data="sp_back")
    )
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")


@bot.message_handler(func=lambda m: m.text == "🏆 Турниры (админ)" and is_admin(m.from_user.id))
def admin_tournaments_btn(message):
    admin_tournaments_menu(message)


@bot.callback_query_handler(func=lambda call: call.data == "admin_tour_create")
def admin_tour_create(call):
    if not is_admin(call.from_user.id):
        return
    admin_mode[call.from_user.id] = {"step": "tour_title"}
    bot.send_message(call.message.chat.id, "🏆 Введите <b>название</b> турнира:", parse_mode="HTML")
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "admin_tour_list")
def admin_tour_list(call):
    if not is_admin(call.from_user.id):
        return
    tournaments = data.get("tournaments", [])
    if not tournaments:
        bot.answer_callback_query(call.id, "❌ Турниров нет", show_alert=True)
        return

    now = int(time.time())
    text = "📋 <b>Список турниров</b>\n\n"
    markup = types.InlineKeyboardMarkup(row_width=1)

    for t in tournaments:
        tid = str(t.get("id"))
        status_emoji = "🟢" if t.get("active", True) and t.get("end_time", 0) > now else "⏹"
        end_str = datetime.fromtimestamp(t.get("end_time", 0)).strftime("%d.%m.%Y %H:%M")
        participants = len(data.get("tournament_participants", {}).get(tid, {}))
        text += f"{status_emoji} <b>{t.get('title')}</b>\n"
        text += f"   🎁 {t.get('prize', '—')}\n"
        text += f"   ⏰ До: {end_str}\n"
        text += f"   👥 Участников: {participants}\n\n"

        markup.add(types.InlineKeyboardButton(
            f"📊 {t.get('title')}",
            callback_data=f"admin_tour_info_{tid}"
        ))

    markup.add(types.InlineKeyboardButton("⬅ Назад", callback_data="admin_tour_back"))
    bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="HTML")
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_tour_info_"))
def admin_tour_info(call):
    if not is_admin(call.from_user.id):
        return
    tid = call.data.replace("admin_tour_info_", "")
    t = get_tournament_by_id(tid)
    if not t:
        bot.answer_callback_query(call.id, "❌ Не найдено", show_alert=True)
        return

    now = int(time.time())
    end_str = datetime.fromtimestamp(t.get("end_time", 0)).strftime("%d.%m.%Y %H:%M")
    participants = data.get("tournament_participants", {}).get(str(tid), {})
    status = "🟢 Активен" if t.get("active", True) and t.get("end_time", 0) > now else "⏹ Завершён"

    text = f"📊 <b>{t.get('title')}</b>\n\n"
    text += f"📝 {t.get('description', '—')}\n"
    text += f"🎁 Приз: {t.get('prize', '—')}\n"
    text += f"📈 Метрика: потрачено ⭐️ в «Мишках»\n"
    text += f"⏰ До: {end_str}\n"
    text += f"📊 Статус: {status}\n"
    text += f"👥 Участников: {len(participants)}\n\n"

    leaderboard = get_tournament_leaderboard(tid, limit=10)
    if leaderboard:
        text += "🏅 <b>Топ-10:</b>\n"
        for i, (uid, spent) in enumerate(leaderboard, 1):
            uname = data.get("usernames", {}).get(uid, uid)
            text += f"{i}. @{uname} (<code>{uid}</code>) — {spent}⭐️\n"

    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("⬅ Назад", callback_data="admin_tour_list"))
    bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="HTML")
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "admin_tour_back")
def admin_tour_back(call):
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    admin_tournaments_menu(call.message)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "admin_tour_delete_menu")
def admin_tour_delete_menu(call):
    if not is_admin(call.from_user.id):
        return
    tournaments = data.get("tournaments", [])
    if not tournaments:
        bot.answer_callback_query(call.id, "❌ Нет турниров", show_alert=True)
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for t in tournaments:
        markup.add(types.InlineKeyboardButton(
            f"🗑 {t.get('title')}",
            callback_data=f"admin_tour_del_{t.get('id')}"
        ))
    markup.add(types.InlineKeyboardButton("⬅ Назад", callback_data="admin_tour_back"))
    bot.send_message(call.message.chat.id, "Выберите турнир для удаления:", reply_markup=markup)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_tour_del_"))
def admin_tour_del_confirm(call):
    if not is_admin(call.from_user.id):
        return
    tid = call.data.replace("admin_tour_del_", "")
    tournaments = data.get("tournaments", [])
    new_list = [t for t in tournaments if str(t.get("id")) != str(tid)]
    if len(new_list) == len(tournaments):
        bot.answer_callback_query(call.id, "❌ Не найдено", show_alert=True)
        return
    data["tournaments"] = new_list
    if "tournament_participants" in data and str(tid) in data["tournament_participants"]:
        del data["tournament_participants"][str(tid)]
    save_data()
    bot.edit_message_text("✅ Турнир удалён", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "admin_tour_toggle_menu")
def admin_tour_toggle_menu(call):
    if not is_admin(call.from_user.id):
        return
    tournaments = data.get("tournaments", [])
    if not tournaments:
        bot.answer_callback_query(call.id, "❌ Нет турниров", show_alert=True)
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for t in tournaments:
        status = "🟢" if t.get("active", True) else "⏸"
        markup.add(types.InlineKeyboardButton(
            f"{status} {t.get('title')}",
            callback_data=f"admin_tour_toggle_{t.get('id')}"
        ))
    markup.add(types.InlineKeyboardButton("⬅ Назад", callback_data="admin_tour_back"))
    bot.send_message(call.message.chat.id, "Выберите турнир:", reply_markup=markup)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_tour_toggle_"))
def admin_tour_toggle(call):
    if not is_admin(call.from_user.id):
        return
    tid = call.data.replace("admin_tour_toggle_", "")
    for t in data.get("tournaments", []):
        if str(t.get("id")) == str(tid):
            t["active"] = not t.get("active", True)
            save_data()
            status = "🟢 возобновлён" if t["active"] else "⏸ остановлен"
            bot.edit_message_text(f"✅ Турнир {status}", call.message.chat.id, call.message.message_id)
            bot.answer_callback_query(call.id)
            return
    bot.answer_callback_query(call.id, "❌ Не найдено", show_alert=True)


@bot.callback_query_handler(func=lambda call: call.data == "admin_tour_finish_menu")
def admin_tour_finish_menu(call):
    if not is_admin(call.from_user.id):
        return
    active = get_active_tournaments()
    if not active:
        bot.answer_callback_query(call.id, "❌ Нет активных турниров", show_alert=True)
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for t in active:
        markup.add(types.InlineKeyboardButton(
            f"🏁 {t.get('title')}",
            callback_data=f"admin_tour_finish_{t.get('id')}"
        ))
    markup.add(types.InlineKeyboardButton("⬅ Назад", callback_data="admin_tour_back"))
    bot.send_message(call.message.chat.id, "Какой турнир завершить?", reply_markup=markup)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_tour_finish_"))
def admin_tour_finish(call):
    if not is_admin(call.from_user.id):
        return
    tid = call.data.replace("admin_tour_finish_", "")
    t = get_tournament_by_id(tid)
    if not t:
        bot.answer_callback_query(call.id, "❌ Не найдено", show_alert=True)
        return

    t["end_time"] = int(time.time()) - 1
    t["active"] = False
    t["finished_at"] = int(time.time())
    save_data()

    leaderboard = get_tournament_leaderboard(tid, limit=3)
    winners = []
    text = f"🏁 <b>Турнир завершён!</b>\n\n"
    text += f"🏆 {t.get('title')}\n"
    text += f"🎁 Приз: {t.get('prize')}\n\n"

    if leaderboard:
        text += "🥇 <b>Победители:</b>\n"
        for i, (uid, spent) in enumerate(leaderboard, 1):
            uname = data.get("usernames", {}).get(uid, uid)
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉"
            text += f"{medal} @{uname} — {spent}⭐️\n"
            winners.append({"user_id": uid, "spent": spent, "place": i})

            try:
                bot.send_message(int(uid),
                                 f"🎉 Поздравляем! Вы заняли {medal} {i} место в турнире «{t.get('title')}»!\n\n"
                                 f"🎁 Ваш приз: {t.get('prize')}\n"
                                 f"📊 Ваш результат: {spent}⭐️\n\n"
                                 f"Свяжитесь с администратором для получения приза.")
            except:
                pass
    else:
        text += "😔 Не было участников\n"

    data.setdefault("tournament_winners", {})[tid] = winners
    save_data()

    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="HTML")
    bot.answer_callback_query(call.id, "🏁 Завершено!")


@bot.callback_query_handler(func=lambda call: call.data == "admin_tour_notify_menu")
def admin_tour_notify_menu(call):
    if not is_admin(call.from_user.id):
        return
    active = get_active_tournaments()
    if not active:
        bot.answer_callback_query(call.id, "❌ Нет активных турниров", show_alert=True)
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for t in active:
        markup.add(types.InlineKeyboardButton(
            f"📢 {t.get('title')}",
            callback_data=f"admin_tour_notify_{t.get('id')}"
        ))
    markup.add(types.InlineKeyboardButton("⬅ Назад", callback_data="admin_tour_back"))
    bot.send_message(call.message.chat.id, "Кому отправить напоминание?", reply_markup=markup)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_tour_notify_"))
def admin_tour_notify(call):
    if not is_admin(call.from_user.id):
        return
    tid = call.data.replace("admin_tour_notify_", "")
    t = get_tournament_by_id(tid)
    if not t:
        bot.answer_callback_query(call.id, "❌ Не найдено", show_alert=True)
        return

    participants = data.get("tournament_participants", {}).get(str(tid), {})
    if not participants:
        bot.answer_callback_query(call.id, "❌ Нет участников", show_alert=True)
        return

    bot.answer_callback_query(call.id, f"📢 Отправляю {len(participants)} участникам...")

    now = int(time.time())
    end_in = t.get("end_time", 0) - now
    days = max(0, end_in) // 86400
    hours = (max(0, end_in) % 86400) // 3600

    sent = 0
    failed = 0
    for uid in participants:
        try:
            bot.send_message(int(uid),
                             f"🏆 <b>Напоминание о турнире</b>\n\n"
                             f"«{t.get('title')}»\n"
                             f"🎁 Приз: {t.get('prize')}\n"
                             f"⏰ Осталось: {days}д {hours}ч\n\n"
                             f"Побеждает тот, кто потратит больше ⭐️ в «Мишках»!",
                             parse_mode="HTML")
            sent += 1
            time.sleep(0.05)
        except:
            failed += 1

    bot.send_message(call.message.chat.id, f"✅ Отправлено: {sent}\n❌ Ошибок: {failed}")

@bot.message_handler(func=lambda m: m.text == "🏆 Турниры")
def tournaments_btn(message):
    if is_banned(message.from_user.id) or not is_user_registered(message.from_user.id):
        bot.send_message(message.chat.id, "❌ Сначала пройдите регистрацию! /start")
        return
    if data.get("sponsor_channels") and check_subscriptions(message.from_user.id):
        show_subscription_check(message, message.from_user.id)
        return
    tournaments_menu(message)


@bot.callback_query_handler(func=lambda call: call.data == "tournament_back")
def tournament_back_cb(call):
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    tournaments_menu(call.message)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "tournament_all")
def tournament_all_cb(call):
    tournaments_menu(call.message)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "my_tournaments")
def my_tournaments_cb(call):
    my_tournaments(call.message.chat.id, call.from_user.id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("tournament_view_"))
def tournament_view_cb(call):
    tid = call.data.replace("tournament_view_", "")
    tournament_view(call.message.chat.id, tid, call.from_user.id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("tournament_already_"))
def tournament_already_cb(call):
    bot.answer_callback_query(call.id, "✅ Вы уже участвуете в этом турнире!", show_alert=True)


@bot.callback_query_handler(func=lambda call: call.data.startswith("tournament_join_"))
def tournament_join_cb(call):
    tid = call.data.replace("tournament_join_", "")
    t = get_tournament_by_id(tid)
    if not t:
        bot.answer_callback_query(call.id, "❌ Турнир не найден", show_alert=True)
        return
    if t.get("end_time", 0) <= int(time.time()):
        bot.answer_callback_query(call.id, "⏹ Турнир уже завершён", show_alert=True)
        return
    if not t.get("active", True):
        bot.answer_callback_query(call.id, "⏸ Турнир остановлен", show_alert=True)
        return

    added = register_tournament_participant(tid, call.from_user.id)
    if added:
        bot.answer_callback_query(call.id, "🎉 Вы участвуете в турнире!", show_alert=True)
    else:
        bot.answer_callback_query(call.id, "✅ Вы уже участвуете", show_alert=True)

    tournament_view(call.message.chat.id, tid, call.from_user.id)

def my_tournaments(chat_id, user_id):
    uid = str(user_id)
    text = "👥 <b>Мои турниры</b>\n\n"
    found = False
    now = int(time.time())

    for t in data.get("tournaments", []):
        tid = str(t.get("id"))
        participants = data.get("tournament_participants", {}).get(tid, {})
        if uid in participants:
            found = True
            status = "🟢" if t.get("end_time", 0) > now and t.get("active", True) else "⏹"
            my_spent = participants[uid].get("spent", 0)
            text += f"{status} <b>{t.get('title', 'Турнир')}</b>\n"
            text += f"   🎁 {t.get('prize', 'не указан')}\n"
            text += f"   📊 Ваш результат: <b>{my_spent}⭐️</b>\n\n"

    if not found:
        text += "Вы ещё не участвуете ни в одном турнире.\n"

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🏆 Все турниры", callback_data="tournament_all"))
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")

@bot.pre_checkout_query_handler(func=lambda query: True)
def checkout_process(pre_checkout_query):
    bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
@bot.message_handler(content_types=['successful_payment'])
def successful_payment(message):
    user_id = message.from_user.id
    uid = str(user_id)

    # ⬇️ ДОДАНО: рахуємо витрати в турнірах
    add_tournament_spent(user_id, 5)  # 1 зірка за гру

    if "selected_tickets" in data and uid in data["selected_tickets"]:
        del data["selected_tickets"][uid]
    # ... далі без змін
    payload = message.successful_payment.invoice_payload
    if uid in data.get("user_sources", {}):
        source = data["user_sources"][uid]
        if source in data.get("special_links", {}):
            data["special_links"][source]["game_plays"] = data["special_links"][source].get("game_plays", 0) + 1
            save_data()
    if payload.startswith("game_attempt_"):
        tickets = []
        for i in range(4):
            tickets.append({"id": i, "type": "win", "symbol": "🧸"})
        for i in range(4, 25):
            tickets.append({"id": i, "type": "lose", "symbol": "❌"})
        random.shuffle(tickets)
        game_sessions[uid] = tickets.copy()
        markup = types.InlineKeyboardMarkup(row_width=5)
        buttons = []
        for ticket in tickets:
            buttons.append(types.InlineKeyboardButton("🎟", callback_data=f"ticket_{ticket['id']}_{ticket['type']}"))
        markup.add(*buttons)
        markup.row(types.InlineKeyboardButton("⬅ Назад", callback_data="back_to_game_menu"))
        bot.send_message(
            message.chat.id,
            "🎲 Выберите билет!\n\nСреди 25 билетов спрятано 4 🧸 мишки!\nВыберите один билет, чтобы проверить удачу:",
            reply_markup=markup
        )

# ============================================================
# ВАЖЛИВО: ВИПРАВЛЕНА ФУНКЦІЯ handle_ticket_choice
# ============================================================
@bot.callback_query_handler(func=lambda call: call.data.startswith("ticket_"))
def handle_ticket_choice(call):
    user_id = call.from_user.id
    username = call.from_user.username or "без username"
    uid = str(user_id)
    parts = call.data.split("_")
    selected_ticket_id = int(parts[1])
    ticket_type = parts[2]

    if uid in data.get("selected_tickets", {}):
        bot.answer_callback_query(call.id, "❌ Вы уже выбрали билет!", show_alert=True)
        return

    if "selected_tickets" not in data:
        data["selected_tickets"] = {}
    data["selected_tickets"][uid] = True

    if uid not in data["game_stats"]:
        data["game_stats"][uid] = {"played": 0, "won": 0}
    data["game_stats"][uid]["played"] += 1

    if uid not in game_sessions:
        all_tickets = []
        for i in range(4):
            all_tickets.append({"id": i, "type": "win", "symbol": "🧸"})
        for i in range(4, 25):
            all_tickets.append({"id": i, "type": "lose", "symbol": "❌"})
        random.shuffle(all_tickets)
        game_sessions[uid] = all_tickets

    all_tickets = game_sessions[uid]
    markup = types.InlineKeyboardMarkup(row_width=5)
    buttons = []
    for ticket in all_tickets:
        if ticket["id"] == selected_ticket_id:
            if ticket_type == "win":
                buttons.append(types.InlineKeyboardButton("✅🧸", callback_data="none"))
            else:
                buttons.append(types.InlineKeyboardButton("❌", callback_data="none"))
        else:
            if ticket["type"] == "win":
                buttons.append(types.InlineKeyboardButton("🧸", callback_data="none"))
            else:
                buttons.append(types.InlineKeyboardButton("⬜", callback_data="none"))
    markup.add(*buttons)
    markup.row(types.InlineKeyboardButton("⬅ В меню игры", callback_data="back_to_game_menu"))
    markup.row(types.InlineKeyboardButton("🎲 Сыграть ещё", callback_data="play_game"))

    if ticket_type == "win":
        data["game_stats"][uid]["won"] += 1
        save_data()

        text = (
            f"🎉 *ПОЗДРАВЛЯЕМ! ВЫ ВЫИГРАЛИ!*\n\n"
            f"Вы нашли 🧸 мишку!\nВаш приз: Подарок за *{GIFT_PRICE}⭐️*\n\n"
            f"📦 Отправляю подарок..."
        )
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup,
                              parse_mode="Markdown")

        # Пробуем отправить подарок
        if AUTO_GAME_PAYOUT and gift_client_ready:
            if send_gift_sync(user_id, TARGET_GIFT_ID, f"🎉 Вы выиграли подарок в игре «Мишки»!"):
                bot.send_message(call.message.chat.id, "✅ Подарок успешно отправлен на ваш аккаунт Telegram!")
                send_win_notification_to_channel(user_id, username, GIFT_PRICE, True)
                bot.answer_callback_query(call.id)
                if uid in game_sessions:
                    del game_sessions[uid]
                return
            else:
                bot.send_message(call.message.chat.id,
                                 "⚠️ Не удалось автоматически отправить подарок.\n\n📩 Напишите администратору: @Chipka_Ckyn")
        else:
            bot.send_message(call.message.chat.id, "⚠️ Система автовывода недоступна.")

        # Сохраняем выигрыш
        pending_withdrawals[user_id] = {
            "gift_price": GIFT_PRICE,
            "chat_id": call.message.chat.id,
            "message_id": call.message.message_id,
            "username": username
        }

        fallback_text = (
            f"❌ Автоматическая выдача не удалась\n\n"
            f"🎁 Ваш выигрыш: подарок на {GIFT_PRICE}⭐️\n\n"
            f"📩 Что делать?\n"
            f"1️⃣ Напишите аккаунту, который выдаёт подарки:\n"
            f"   👤 @Chipka_Ckyn\n\n"
            f"2️⃣ После того как напишете, нажмите кнопку «Я написал»\n"
            f"3️⃣ Бот автоматически проверит и отправит подарок\n\n"
            f"⚠️ Если не можете написать - нажмите «Ждать админа»"
        )

        fallback_markup = types.InlineKeyboardMarkup(row_width=2)
        fallback_markup.add(
            types.InlineKeyboardButton("📩 Я написал аккаунту", callback_data=f"retry_gift_{user_id}"),
            types.InlineKeyboardButton("⏳ Ждать админа", callback_data=f"wait_admin_{user_id}")
        )

        bot.send_message(call.message.chat.id, fallback_text, reply_markup=fallback_markup)  # без parse_mode
        send_win_notification_to_channel(user_id, username, GIFT_PRICE, False)

    else:
        save_data()
        text = (
            f"😢 *К сожалению, пусто*\n\nВы выбрали пустой билет ❌\nПопробуйте ещё раз!\n\n"
            f"🍀 Удача улыбнётся вам в следующий раз!\n\n"
            f"📊 *Результаты:*\n🧸 - выигрышный билет\n⬜ - пустой билет\n❌ - ваш пустой выбор"
        )
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup,
                              parse_mode="Markdown")

    if uid in game_sessions:
        del game_sessions[uid]
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("retry_gift_"))
def retry_gift_handler(call):
    user_id = int(call.data.replace("retry_gift_", ""))

    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id, "❌ Это не ваша заявка!", show_alert=True)
        return

    if user_id not in pending_withdrawals:
        bot.answer_callback_query(call.id, "❌ Заявка не найдена или уже обработана", show_alert=True)
        return

    bot.answer_callback_query(call.id, "🔄 Проверяю...")
    bot.send_message(call.message.chat.id, "🔄 Повторная попытка отправки подарка...")

    if AUTO_GAME_PAYOUT and gift_client_ready:
        if send_gift_sync(user_id, TARGET_GIFT_ID, f"🎉 Вы выиграли подарок в игре «Мишки»!"):
            bot.send_message(call.message.chat.id, "✅ Подарок успешно отправлен! Поздравляем с выигрышем! 🎉")
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
            del pending_withdrawals[user_id]
            return
        else:
            bot.send_message(
                call.message.chat.id,
                "❌ К сожалению, автоматическая отправка снова не удалась.\n\n"
                "Пожалуйста, дождитесь выдачи от администратора. Обычно это занимает до 24 часов.\n"
                "Канал заявок: @WithdrawEgik"
            )
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
    else:
        bot.send_message(
            call.message.chat.id,
            "❌ Система автовывода недоступна.\n\n"
            "Пожалуйста, дождитесь выдачи от администратора."
        )
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass


@bot.callback_query_handler(func=lambda call: call.data.startswith("wait_admin_"))
def wait_admin_handler(call):
    user_id = int(call.data.replace("wait_admin_", ""))

    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id, "❌ Это не ваша заявка!", show_alert=True)
        return

    bot.answer_callback_query(call.id, "✅ Ожидайте")
    bot.send_message(
        call.message.chat.id,
        "✅ Ваша заявка принята!\n\n"
        "Администратор выдаст подарок в ближайшее время (до 24 часов).\n"
        "Следите за каналом @WithdrawEgik"
    )

    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass


@bot.callback_query_handler(func=lambda call: call.data == "none")
def none_callback(call):
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "back_to_game_menu")
def back_to_game_menu(call):
    text = "🧸 Мишки по 5 звёзд(игра)"
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("🎲 Перейти к оплате", callback_data="play_game"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)
    bot.answer_callback_query(call.id)

# ============================================================
# ADMIN PANEL
# ============================================================
@bot.message_handler(commands=['admin'])
def admin_panel_cmd(message):
    if not is_admin(message.from_user.id):
        return
    show_admin_panel(message)

@bot.message_handler(func=lambda m: m.text == "⚙️ Админ панель")
def open_admin_panel(message):
    if not is_admin(message.from_user.id):
        return
    show_admin_panel(message)

def show_admin_panel(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("👤 Профиль", "📋 Задания")
    markup.row("💸 Заработать ⭐️", "🎁 Вывести ⭐️")
    markup.row("🎟 Промокоды", "📝 Задачи", "📢 Рассылка")
    markup.row("📊 Статистика", "🔗 Спецссылки", "🔍 Поиск пользователя")
    markup.row("📢 Спонсоры", "⏰ Отложенные сообщения", "💰 Реферальная награда")
    markup.row("🌍 Блокировка стран", "🎁 Автовыводы", "🏆 Турниры (админ)", "⬅️ Назад")
    bot.send_message(message.chat.id, "⚙️ <b>Админ панель</b>", reply_markup=markup, parse_mode="HTML")

# ============================================================
# РАССЫЛКА
# ============================================================
@bot.message_handler(func=lambda m: m.text == "📢 Рассылка" and is_admin(m.from_user.id))
def mailing_start(message):
    admin_mode[message.from_user.id] = {"step": "mailing_text"}
    bot.send_message(
        message.chat.id,
        "✉️ <b>Рассылка</b>\n\nОтправьте текст или фото с подписью.\nПоддерживается HTML:\n<b>Жирный</b>, <i>Курсив</i>\n<code>Код</code>, <a href='https://t.me'>Ссылка</a>",
        parse_mode="HTML"
    )

@bot.message_handler(content_types=['text', 'photo'], func=lambda m: admin_mode.get(m.from_user.id, {}).get("step") == "mailing_text" and is_admin(m.from_user.id))
def mailing_get_text(message):
    state = {}
    if message.content_type == "photo":
        state["photo"] = message.photo[-1].file_id
        state["text"] = message.caption or ""
    else:
        state["text"] = message.text
    admin_mode[message.from_user.id] = {"step": "mailing_buttons", **state}
    bot.send_message(
        message.chat.id,
        "🔘 Введите кнопки (по одной на строку):\n\nФормат: <code>Текст кнопки - https://ссылка</code>\n\nИли отправьте <b>0</b> — без кнопок",
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda m: admin_mode.get(m.from_user.id, {}).get("step") == "mailing_buttons" and is_admin(m.from_user.id))
def mailing_buttons(message):
    state = admin_mode.get(message.from_user.id, {})
    markup = None
    if message.text != "0":
        markup = types.InlineKeyboardMarkup()
        for line in message.text.split("\n"):
            if " - " in line:
                btn_text, url = line.split(" - ", 1)
                markup.add(types.InlineKeyboardButton(btn_text.strip(), url=url.strip()))
    state["markup"] = markup
    state["step"] = "mailing_confirm"
    admin_mode[message.from_user.id] = state
    bot.send_message(message.chat.id, "👁 <b>Предпросмотр:</b>", parse_mode="HTML")
    if "photo" in state:
        bot.send_photo(message.chat.id, state["photo"], caption=state["text"], parse_mode="HTML", reply_markup=markup)
    else:
        bot.send_message(message.chat.id, state["text"], parse_mode="HTML", reply_markup=markup)
    confirm = types.InlineKeyboardMarkup()
    confirm.row(
        types.InlineKeyboardButton("✅ Отправить всем", callback_data="send_mail"),
        types.InlineKeyboardButton("❌ Отменить", callback_data="cancel_mail")
    )
    total = len(data["users"])
    bot.send_message(message.chat.id, f"📊 Получателей: <b>{total}</b>\n\nПодтвердить рассылку?", reply_markup=confirm, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data in ["send_mail", "cancel_mail"])
def mailing_confirm(call):
    if not is_admin(call.from_user.id):
        return
    if call.data == "cancel_mail":
        admin_mode.pop(call.from_user.id, None)
        bot.answer_callback_query(call.id, "❌ Рассылка отменена")
        bot.send_message(call.message.chat.id, "❌ Рассылка отменена")
        return
    state = admin_mode.get(call.from_user.id, {})
    bot.answer_callback_query(call.id, "⏳ Рассылка начата...")
    bot.send_message(call.message.chat.id, f"⏳ Начинаю рассылку для {len(data['users'])} пользователей...")
    success = 0
    failed = 0
    for uid in data["users"]:
        try:
            uid_int = int(uid)
            if "photo" in state:
                bot.send_photo(uid_int, state["photo"], caption=state["text"], parse_mode="HTML", reply_markup=state.get("markup"))
            else:
                bot.send_message(uid_int, state["text"], parse_mode="HTML", reply_markup=state.get("markup"))
            success += 1
            time.sleep(0.05)
        except:
            failed += 1
    bot.send_message(call.message.chat.id, f"✅ <b>Рассылка завершена!</b>\n\n✔️ Доставлено: {success}\n❌ Не доставлено: {failed}", parse_mode="HTML")
    admin_mode.pop(call.from_user.id, None)

# ============================================================
# СТАТИСТИКА
# ============================================================
@bot.message_handler(func=lambda m: m.text == "📊 Статистика" and is_admin(m.from_user.id))
def show_stats(message):
    users_count = len(data["users"])
    total_balance = sum(data["balances"].values())
    banned_count = len(data["banned"])
    tasks_count = len(data["tasks"])
    promo_count = len(data["promocodes"])
    game_history = data.get("game_history", [])
    total_wins = sum(1 for g in game_history)
    sponsor_count = len(data.get("sponsor_channels", []))
    bot.send_message(
        message.chat.id,
        f"📊 <b>Статистика бота</b>\n\n"
        f"👤 Пользователей: <b>{users_count}</b>\n"
        f"🚫 Заблокировано: <b>{banned_count}</b>\n"
        f"💰 Всего ⭐️ в системе: <b>{total_balance:.1f}</b>\n\n"
        f"📋 Заданий: <b>{tasks_count}</b>\n"
        f"🎟 Промокодов: <b>{promo_count}</b>\n"
        f"📢 Спонсоров: <b>{sponsor_count}</b>\n"
        f"🎮 Выигрышей в игре: <b>{total_wins}</b>",
        parse_mode="HTML"
    )

# ============================================================
# ПОИСК ПОЛЬЗОВАТЕЛЯ
# ============================================================
@bot.message_handler(func=lambda m: m.text == "🔍 Поиск пользователя" and is_admin(m.from_user.id))
def search_user_start(message):
    admin_mode[message.from_user.id] = {"step": "search_user"}
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⬅ Назад", callback_data="sp_back"))
    bot.send_message(
        message.chat.id,
        "🔍 *Поиск пользователя*\n\nВведите ID или @username:",
        reply_markup=markup,
        parse_mode="Markdown"
    )

def show_user_profile(chat_id, uid, call=None):
    balance = get_balance(int(uid))
    is_user_banned = is_banned(int(uid))
    tasks_completed = len(data.get("completed_channels", {}).get(uid, []))
    referrals_count = len(data.get("referrals", {}).get(uid, []))
    game_stats_data = data.get("game_stats", {}).get(uid, {"played": 0, "won": 0})
    status = "🚫 Заблокирован" if is_user_banned else "✅ Активен"
    found_username = data.get("usernames", {}).get(uid, "")
    user_info_text = f"🆔 ID: <code>{uid}</code>\n"
    if found_username:
        user_info_text = f"👤 Username: @{found_username}\n" + user_info_text
    text = (
        f"👤 <b>Информация о пользователе</b>\n\n"
        f"{user_info_text}"
        f"📊 Статус: {status}\n"
        f"💰 Баланс: <b>{balance}⭐️</b>\n\n"
        f"📋 Выполнено заданий: {tasks_completed}\n"
        f"👥 Рефералов: {referrals_count}\n"
        f"🎮 Игр сыграно: {game_stats_data['played']}\n"
        f"🎉 Выигрышей: {game_stats_data['won']}\n"
    )
    if is_user_banned:
        text += f"\n🚫 Причина: {data.get('banned', {}).get(uid, '')}"
    markup = types.InlineKeyboardMarkup(row_width=2)
    if is_user_banned:
        markup.add(types.InlineKeyboardButton("✅ Разблокировать", callback_data=f"unban_{uid}"))
    else:
        markup.add(types.InlineKeyboardButton("🚫 Заблокировать", callback_data=f"banuser_{uid}"))
    markup.add(
        types.InlineKeyboardButton("💬 Написать", callback_data=f"msguser_{uid}"),
        types.InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh_{uid}")
    )
    markup.add(
        types.InlineKeyboardButton("👥 Рефералы", callback_data=f"reflist_{uid}"),
        types.InlineKeyboardButton("➖ Снять звёзды", callback_data=f"take_stars_{uid}")
    )
    markup.add(types.InlineKeyboardButton("⬅ Назад", callback_data="sp_back"))
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")

# ============================================================
# СПОНСОРЫ
# ============================================================
@bot.message_handler(func=lambda m: m.text == "📢 Спонсоры" and is_admin(m.from_user.id))
def sponsors_menu(message):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("➕ Добавить канал", callback_data="sponsor_add"),
        types.InlineKeyboardButton("📋 Список каналов", callback_data="sponsor_list"),
        types.InlineKeyboardButton("🗑 Удалить канал", callback_data="sponsor_delete"),
        types.InlineKeyboardButton("👥 Лимит подписок", callback_data="sponsor_set_limit"),
        types.InlineKeyboardButton("📋 Каналы с заявками", callback_data="sponsor_application_menu"),
        types.InlineKeyboardButton("⬅ Назад", callback_data="sp_back")
    )
    channels_count = len(data.get("sponsor_channels", []))
    bot.send_message(
        message.chat.id,
        f"📢 <b>Управление спонсорами</b>\n\nКаналов: {channels_count}",
        reply_markup=markup,
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data == "sponsor_application_menu")
def sponsor_application_menu(call):
    if not is_admin(call.from_user.id):
        return
    app_channels = data.get("sponsor_application_channels", [])
    text = f"📋 <b>Каналы с заявками (спонсоры)</b>\n\nВсего: {len(app_channels)}\n"
    for i, ch in enumerate(app_channels, 1):
        text += f"{i}. {ch['name']} ({ch['id']})\n"
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("➕ Добавить канал с заявкой", callback_data="add_sponsor_application"),
        types.InlineKeyboardButton("🗑 Удалить канал с заявкой", callback_data="delete_sponsor_application"),
        types.InlineKeyboardButton("⬅ Назад", callback_data="sp_back")
    )
    bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="HTML")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "sponsor_set_limit")
def sponsor_set_limit_list(call):
    if not is_admin(call.from_user.id):
        return
    channels = data.get("sponsor_channels", [])
    if not channels:
        bot.answer_callback_query(call.id, "Нет добавленных каналов", show_alert=True)
        return
    limits = data.get("sponsor_limits", {})
    counts = data.get("sponsor_counts", {})
    markup = types.InlineKeyboardMarkup(row_width=1)
    for channel in channels:
        channel_id = channel["id"]
        current_limit = limits.get(channel_id, 0)
        current_count = counts.get(channel_id, 0)
        limit_text = f"{current_count}/{current_limit}" if current_limit > 0 else f"{current_count}/∞"
        markup.add(types.InlineKeyboardButton(f"👥 {channel['name']} ({limit_text})", callback_data=f"sponsor_limit_{channel_id}"))
    markup.add(types.InlineKeyboardButton("⬅ Назад", callback_data="sp_back"))
    bot.send_message(call.message.chat.id, "Выберите канал для установки лимита:", reply_markup=markup)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("sponsor_limit_"))
def sponsor_limit_start(call):
    if not is_admin(call.from_user.id):
        return
    channel_id = call.data.replace("sponsor_limit_", "")
    admin_mode[call.from_user.id] = {"step": "set_sponsor_limit", "channel_id": channel_id}
    bot.send_message(call.message.chat.id, f"Введите лимит для канала {channel_id}:\n0 - безлимит")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "sponsor_add")
def sponsor_add_start(call):
    if not is_admin(call.from_user.id):
        return
    admin_mode[call.from_user.id] = {"step": "add_sponsor_link"}
    bot.send_message(
        call.message.chat.id,
        "🔗 Введите ссылку для перехода к спонсору:\n\n• @username или https://t.me/username\n• Для приватного: https://t.me/+..."
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "sponsor_list")
def sponsor_list(call):
    if not is_admin(call.from_user.id):
        return
    channels = data.get("sponsor_channels", [])
    if not channels:
        bot.answer_callback_query(call.id, "Нет добавленных каналов", show_alert=True)
        return
    limits = data.get("sponsor_limits", {})
    counts = data.get("sponsor_counts", {})
    text = "📋 <b>Список каналов спонсоров:</b>\n\n"
    for i, channel in enumerate(channels, 1):
        channel_id = channel["id"]
        limit = limits.get(channel_id, 0)
        count = counts.get(channel_id, 0)
        count_text = f"{count}/{limit}" if limit > 0 else f"{count}/∞"
        text += f"{i}. {channel['name']}\n   ID: {channel['id']}\n   👥 Подписки: {count_text}\n\n"
    bot.send_message(call.message.chat.id, text, parse_mode="HTML")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "sponsor_delete")
def sponsor_delete_list(call):
    if not is_admin(call.from_user.id):
        return
    channels = data.get("sponsor_channels", [])
    if not channels:
        bot.answer_callback_query(call.id, "Нет каналов для удаления", show_alert=True)
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for i, channel in enumerate(channels):
        markup.add(types.InlineKeyboardButton(f"❌ {channel['name']}", callback_data=f"sponsor_del_{i}"))
    markup.add(types.InlineKeyboardButton("⬅ Назад", callback_data="sp_back"))
    bot.send_message(call.message.chat.id, "Выберите канал для удаления:", reply_markup=markup)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("sponsor_del_"))
def sponsor_confirm_delete(call):
    if not is_admin(call.from_user.id):
        return
    index = int(call.data.replace("sponsor_del_", ""))
    channels = data.get("sponsor_channels", [])
    if 0 <= index < len(channels):
        removed = channels.pop(index)
        data["sponsor_channels"] = channels
        save_data()
        bot.edit_message_text(f"✅ Канал {removed['name']} удалён", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "sp_back")
def sp_back(call):
    if not is_admin(call.from_user.id):
        return
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    show_admin_panel(call.message)
    bot.answer_callback_query(call.id)

# ============================================================
# ПРОМОКОДЫ
# ============================================================
@bot.message_handler(func=lambda m: m.text == "🎟 Промокоды" and is_admin(m.from_user.id))
def promocodes_menu(message):
    promocodes = data.get("promocodes", {})
    count = len(promocodes)
    text = f"🎟 <b>Управление промокодами</b>\n\nАктивных: {count}\n"
    if count > 0:
        text += "\n<b>Список:</b>\n"
        for code, promo in promocodes.items():
            text += f"• {code}: {promo['reward']}⭐️ ({promo['uses']}/{promo['max_uses']})\n"
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("➕ Создать промокод", callback_data="create_promo_start"),
        types.InlineKeyboardButton("🗑 Удалить промокод", callback_data="delete_promo_start"),
        types.InlineKeyboardButton("⬅ Назад", callback_data="sp_back")
    )
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == "create_promo_start")
def create_promo_callback(call):
    if not is_admin(call.from_user.id):
        return
    admin_mode[call.from_user.id] = {"step": "create_promo"}
    bot.send_message(call.message.chat.id, "Введите промокод, награду и лимит через пробел:\nПример: TEST 100 10")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "delete_promo_start")
def delete_promo_callback(call):
    if not is_admin(call.from_user.id):
        return
    promocodes = data.get("promocodes", {})
    if not promocodes:
        bot.answer_callback_query(call.id, "❌ Активных промокодов нет", show_alert=True)
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for code, promo in promocodes.items():
        markup.add(types.InlineKeyboardButton(f"{code} ({promo.get('uses',0)}/{promo.get('max_uses',1)})", callback_data=f"deletepromo_{code}"))
    markup.add(types.InlineKeyboardButton("⬅ Назад", callback_data="sp_back"))
    bot.send_message(call.message.chat.id, "🗑 Выберите промокод для удаления:", reply_markup=markup)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("deletepromo_"))
def delete_promocode_callback(call):
    if not is_admin(call.from_user.id):
        return
    code = call.data.replace("deletepromo_", "")
    if code in data["promocodes"]:
        del data["promocodes"][code]
        save_data()
        bot.edit_message_text(f"✅ Промокод <code>{code}</code> удалён", call.message.chat.id, call.message.message_id, parse_mode="HTML")
    else:
        bot.answer_callback_query(call.id, "❌ Промокод уже удалён")
    bot.answer_callback_query(call.id)

# ============================================================
# ЗАДАЧИ (ADMIN)
# ============================================================
@bot.message_handler(func=lambda m: m.text == "📝 Задачи" and is_admin(m.from_user.id))
def tasks_menu(message):
    tasks = data.get("tasks", [])
    count = len(tasks)
    app_channels = data.get("application_channels", [])
    text = f"📝 <b>Управление задачами</b>\n\nВсего задач: {count}\nКаналов с заявками: {len(app_channels)}\n"
    if count > 0:
        text += "\n<b>Список:</b>\n"
        for i, task in enumerate(tasks, 1):
            text += f"{i}. {task.get('channel_name', 'Канал')}: {task['reward']}⭐️ ({task.get('uses',0)}/{task.get('max_uses','∞')})\n"
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("➕ Добавить задание", callback_data="add_task_start"),
        types.InlineKeyboardButton("🗑 Удалить задание", callback_data="delete_task_menu"),
        types.InlineKeyboardButton("📋 Каналы с заявками", callback_data="application_channels_menu"),
        types.InlineKeyboardButton("⬅ Назад", callback_data="sp_back")
    )
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == "add_task_start")
def add_task_callback(call):
    if not is_admin(call.from_user.id):
        return
    admin_mode[call.from_user.id] = {"step": "add_task_text"}
    bot.send_message(call.message.chat.id, "📝 Введите текст сообщения для задания:")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "delete_task_menu")
def delete_task_callback(call):
    if not is_admin(call.from_user.id):
        return
    tasks = data.get("tasks", [])
    if not tasks:
        bot.answer_callback_query(call.id, "❌ Заданий нет", show_alert=True)
        return
    text = "📋 Список заданий:\n\n"
    for i, task in enumerate(tasks):
        text += f"{i+1}. {task.get('channel_name', 'Канал')}\n💰 {task['reward']}⭐️ | 📊 {task.get('uses',0)}/{task.get('max_uses','∞')}\n\n"
    text += "✏️ Введите номер задания для удаления"
    admin_mode[call.from_user.id] = {"step": "delete_task"}
    bot.send_message(call.message.chat.id, text)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "application_channels_menu")
def application_channels_menu(call):
    if not is_admin(call.from_user.id):
        return
    app_channels = data.get("application_channels", [])
    text = f"📋 <b>Каналы с заявками (задачи)</b>\n\nВсего: {len(app_channels)}\n"
    for i, ch in enumerate(app_channels, 1):
        text += f"{i}. {ch['name']} ({ch['id']})\n"
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("➕ Добавить канал с заявкой", callback_data="add_application_channel"),
        types.InlineKeyboardButton("🗑 Удалить канал с заявкой", callback_data="delete_application_channel"),
        types.InlineKeyboardButton("⬅ Назад", callback_data="sp_back")
    )
    bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="HTML")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "add_application_channel")
def add_application_channel_start(call):
    if not is_admin(call.from_user.id):
        return
    admin_mode[call.from_user.id] = {"step": "add_application_channel"}
    bot.send_message(call.message.chat.id, "📢 Введите ID или @username канала для заявки:")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "delete_application_channel")
def delete_application_channel_list(call):
    if not is_admin(call.from_user.id):
        return
    app_channels = data.get("application_channels", [])
    if not app_channels:
        bot.answer_callback_query(call.id, "Нет каналов с заявками", show_alert=True)
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for i, ch in enumerate(app_channels):
        markup.add(types.InlineKeyboardButton(f"❌ {ch['name']}", callback_data=f"app_del_{i}"))
    markup.add(types.InlineKeyboardButton("⬅ Назад", callback_data="sp_back"))
    bot.send_message(call.message.chat.id, "Выберите канал для удаления:", reply_markup=markup)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("app_del_"))
def delete_application_channel_confirm(call):
    if not is_admin(call.from_user.id):
        return
    index = int(call.data.replace("app_del_", ""))
    app_channels = data.get("application_channels", [])
    if 0 <= index < len(app_channels):
        removed = app_channels.pop(index)
        data["application_channels"] = app_channels
        save_data()
        bot.edit_message_text(f"✅ Канал {removed['name']} удалён", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)

# ============================================================
# СПЕЦССЫЛКИ
# ============================================================
@bot.message_handler(func=lambda m: m.text == "🔗 Спецссылки" and is_admin(m.from_user.id))
def special_links_menu(message):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("➕ Создать", callback_data="sp_create"),
        types.InlineKeyboardButton("📊 Статистика", callback_data="sp_stats"),
        types.InlineKeyboardButton("🗑 Удалить", callback_data="sp_delete"),
        types.InlineKeyboardButton("⬅ Назад", callback_data="sp_back")
    )
    bot.send_message(message.chat.id, "🔗 Управление специальными ссылками:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "sp_create")
def sp_create_start(call):
    if not is_admin(call.from_user.id):
        return
    admin_mode[call.from_user.id] = {"step": "create_sp_link"}
    bot.send_message(call.message.chat.id, "🔗 Введите название для специальной ссылки (латиница, без пробелов):")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "sp_stats")
def sp_stats(call):
    if not is_admin(call.from_user.id):
        return
    links = data.get("special_links", {})
    if not links:
        bot.answer_callback_query(call.id, "Нет специальных ссылок", show_alert=True)
        return
    bot_username = bot.get_me().username
    text = "📊 <b>Статистика специальных ссылок:</b>\n\n"
    for name, info in links.items():
        text += f"<b>{name}</b>\n"
        text += f"🔗 https://t.me/{bot_username}?start={name}\n"
        text += f"👥 Клики: {info.get('clicks', 0)}\n"
        text += f"📋 Выполнений задач: {info.get('task_completions', 0)}\n"
        text += f"🎮 Игр: {info.get('game_plays', 0)}\n\n"
    bot.send_message(call.message.chat.id, text, parse_mode="HTML")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "sp_delete")
def sp_delete_list(call):
    if not is_admin(call.from_user.id):
        return
    links = data.get("special_links", {})
    if not links:
        bot.answer_callback_query(call.id, "Нет ссылок для удаления", show_alert=True)
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for name in links:
        markup.add(types.InlineKeyboardButton(f"❌ {name}", callback_data=f"sp_del_{name}"))
    markup.add(types.InlineKeyboardButton("⬅ Назад", callback_data="sp_back"))
    bot.send_message(call.message.chat.id, "Выберите ссылку для удаления:", reply_markup=markup)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("sp_del_"))
def sp_confirm_delete(call):
    if not is_admin(call.from_user.id):
        return
    code = call.data[7:]
    if code in data["special_links"]:
        del data["special_links"][code]
        save_data()
        bot.edit_message_text(f"✅ Ссылка `{code}` удалена", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    else:
        bot.answer_callback_query(call.id, "Ссылка уже отсутствует")
    bot.answer_callback_query(call.id)

# ============================================================
# РЕФЕРАЛЬНАЯ НАГРАДА
# ============================================================
@bot.message_handler(func=lambda m: m.text == "💰 Реферальная награда" and is_admin(m.from_user.id))
def referral_reward_menu(message):
    current_reward = data.get("referral_reward", 2.5)
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("✏️ Изменить награду", callback_data="change_referral_reward"),
        types.InlineKeyboardButton("⬅ Назад", callback_data="sp_back")
    )
    bot.send_message(
        message.chat.id,
        f"💰 <b>Реферальная награда</b>\n\nТекущая: <b>{current_reward}⭐️</b> за реферала\n\nНаграда начисляется после того, как реферал пройдёт капчу и подпишется на спонсоров.",
        reply_markup=markup, parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data == "change_referral_reward")
def change_referral_reward_start(call):
    if not is_admin(call.from_user.id):
        return
    admin_mode[call.from_user.id] = {"step": "change_referral_reward"}
    current_reward = data.get("referral_reward", 2.5)
    bot.send_message(call.message.chat.id, f"💰 Текущая награда: <b>{current_reward}⭐️</b>\n\nВведите новую сумму:", reply_markup=None, parse_mode="HTML")
    bot.answer_callback_query(call.id)

# ============================================================
# БЛОКИРОВКА СТРАН
# ============================================================
@bot.message_handler(func=lambda m: m.text == "🌍 Блокировка стран" and is_admin(m.from_user.id))
def blocked_countries_menu(message):
    blocked = data.get("blocked_countries", [])
    COUNTRY_NAMES = {
        "ru": "Россия 🇷🇺", "ua": "Украина 🇺🇦", "by": "Беларусь 🇧🇾", "kz": "Казахстан 🇰🇿",
        "us": "США 🇺🇸", "gb": "Великобритания 🇬🇧", "de": "Германия 🇩🇪", "fr": "Франция 🇫🇷",
        "pl": "Польша 🇵🇱", "tr": "Турция 🇹🇷", "in": "Индия 🇮🇳", "ar": "Арабский 🇸🇦",
        "hi": "Хинди 🇮🇳", "bn": "Бенгальский 🇧🇩"
    }
    text = "🌍 <b>Блокировка по странам</b>\n\n"
    if blocked:
        text += "<b>Заблокированные коды:</b>\n"
        for code in blocked:
            text += f"• {COUNTRY_NAMES.get(code, code)}\n"
    else:
        text += "Нет заблокированных стран\n"
    text += f"\n📝 Сообщение при блокировке:\n<i>{data.get('blocked_message', 'Не задано')}</i>"
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("➕ Добавить страну", callback_data="block_country_add"),
        types.InlineKeyboardButton("🗑 Удалить страну", callback_data="block_country_remove"),
        types.InlineKeyboardButton("✏️ Изменить сообщение", callback_data="block_country_msg"),
        types.InlineKeyboardButton("🚫 Заблокировать арабские/индийские", callback_data="block_arabic_indian"),
        types.InlineKeyboardButton("⬅ Назад", callback_data="sp_back")
    )
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == "block_country_add")
def block_country_add_start(call):
    if not is_admin(call.from_user.id):
        return
    admin_mode[call.from_user.id] = {"step": "block_country_add"}
    bot.send_message(call.message.chat.id, "Введите код страны (ru, ua, by, us, de...)\nМожно несколько через запятую: ru, by, kz")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "block_arabic_indian")
def block_arabic_indian(call):
    if not is_admin(call.from_user.id):
        return
    languages = ["ar", "hi", "bn", "te", "mr", "ta", "ur", "gu", "kn", "ml", "pa", "or"]
    blocked = data.get("blocked_countries", [])
    added = [lang for lang in languages if lang not in blocked]
    blocked.extend(added)
    data["blocked_countries"] = blocked
    save_data()
    bot.edit_message_text(f"✅ Заблокированы языки: {', '.join(added) if added else 'уже заблокированы'}", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id, "✅ Готово!")

@bot.callback_query_handler(func=lambda call: call.data == "block_country_remove")
def block_country_remove_list(call):
    if not is_admin(call.from_user.id):
        return
    blocked = data.get("blocked_countries", [])
    if not blocked:
        bot.answer_callback_query(call.id, "Нет заблокированных стран", show_alert=True)
        return
    markup = types.InlineKeyboardMarkup(row_width=2)
    for code in blocked:
        markup.add(types.InlineKeyboardButton(f"❌ {code}", callback_data=f"block_remove_{code}"))
    markup.add(types.InlineKeyboardButton("⬅ Назад", callback_data="sp_back"))
    bot.send_message(call.message.chat.id, "Выберите страну для разблокировки:", reply_markup=markup)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("block_remove_"))
def block_country_remove_confirm(call):
    if not is_admin(call.from_user.id):
        return
    code = call.data.replace("block_remove_", "")
    blocked = data.get("blocked_countries", [])
    if code in blocked:
        blocked.remove(code)
        data["blocked_countries"] = blocked
        save_data()
        bot.edit_message_text(f"✅ Страна {code} разблокирована", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "block_country_msg")
def block_country_msg_start(call):
    if not is_admin(call.from_user.id):
        return
    admin_mode[call.from_user.id] = {"step": "block_country_msg"}
    bot.send_message(call.message.chat.id, f"Текущее:\n<i>{data.get('blocked_message', 'Не задано')}</i>\n\nВведите новое сообщение:", parse_mode="HTML")
    bot.answer_callback_query(call.id)

# ============================================================
# ОТЛОЖЕННЫЕ СООБЩЕНИЯ
# ============================================================
@bot.message_handler(func=lambda m: m.text == "⏰ Отложенные сообщения" and is_admin(m.from_user.id))
def delayed_messages_menu(message):
    messages = data.get("delayed_messages", [])
    text = f"⏰ <b>Отложенные сообщения</b>\n\nВсего: {len(messages)}\n\n"
    if messages:
        text += "<b>Список:</b>\n"
        for i, msg in enumerate(messages, 1):
            text += f"{i}. Задержка: {msg['delay']} сек | {msg['text'][:40]}...\n"
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("➕ Добавить сообщение", callback_data="delayed_add"),
        types.InlineKeyboardButton("📋 Показать список", callback_data="delayed_list"),
        types.InlineKeyboardButton("🗑 Удалить сообщение", callback_data="delayed_delete"),
        types.InlineKeyboardButton("⬅ Назад", callback_data="sp_back")
    )
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == "delayed_add")
def delayed_add_start(call):
    if not is_admin(call.from_user.id):
        return
    admin_mode[call.from_user.id] = {"step": "add_delayed_delay"}
    bot.send_message(call.message.chat.id, "⏰ Введите задержку в секундах (через сколько отправить после /start):\nНапример: 10")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "delayed_list")
def delayed_list(call):
    if not is_admin(call.from_user.id):
        return
    messages = data.get("delayed_messages", [])
    if not messages:
        bot.answer_callback_query(call.id, "Нет отложенных сообщений", show_alert=True)
        return
    text = "📋 <b>Список отложенных сообщений:</b>\n\n"
    for i, msg in enumerate(messages, 1):
        text += f"<b>#{i}</b>\n⏰ Задержка: {msg['delay']} сек\n📷 Фото: {'Да' if msg.get('photo') else 'Нет'}\n"
        preview = msg['text'][:80] + "..." if len(msg['text']) > 80 else msg['text']
        text += f"📝 {preview}\n\n"
    bot.send_message(call.message.chat.id, text, parse_mode="HTML")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "delayed_delete")
def delayed_delete_list(call):
    if not is_admin(call.from_user.id):
        return
    messages = data.get("delayed_messages", [])
    if not messages:
        bot.answer_callback_query(call.id, "Нет сообщений для удаления", show_alert=True)
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for i, msg in enumerate(messages):
        preview = msg['text'][:30] + "..." if len(msg['text']) > 30 else msg['text']
        markup.add(types.InlineKeyboardButton(f"❌ #{i+1}: {preview} ({msg['delay']}с)", callback_data=f"delayed_del_{i}"))
    markup.add(types.InlineKeyboardButton("⬅ Назад", callback_data="sp_back"))
    bot.send_message(call.message.chat.id, "Выберите сообщение для удаления:", reply_markup=markup)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("delayed_del_"))
def delayed_confirm_delete(call):
    if not is_admin(call.from_user.id):
        return
    index = int(call.data.replace("delayed_del_", ""))
    messages = data.get("delayed_messages", [])
    if 0 <= index < len(messages):
        removed = messages.pop(index)
        data["delayed_messages"] = messages
        save_data()
        bot.edit_message_text(f"✅ Сообщение удалено (задержка: {removed['delay']}с)", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)

# ============================================================
# АВТОВЫВОДЫ
# ============================================================
@bot.message_handler(func=lambda m: m.text == "🎁 Автовыводы" and is_admin(m.from_user.id))
def auto_payout_menu(message):
    status_text = "ВКЛЮЧЕНА" if AUTO_GAME_PAYOUT else "ВЫКЛЮЧЕНА"
    status_emoji = "🟢" if AUTO_GAME_PAYOUT else "🔴"
    text = f"🎁 <b>Управление автовыводом</b>\n\n{status_emoji} Статус: <b>{status_text}</b>\n💰 Стоимость подарка: <b>{GIFT_PRICE}⭐️</b>\n🎁 ID подарка: <code>{TARGET_GIFT_ID}</code>\n📁 Сессия: <code>{SESSION_NAME}.session</code>\n"
    if GIFT_SENDER_PHONE:
        text += f"📞 Телефон: <code>{GIFT_SENDER_PHONE}</code>\n"
    text += f"🔌 Клиент: {'✅ Активен' if gift_client_ready else '❌ Не активен'}"
    markup = types.InlineKeyboardMarkup(row_width=2)
    if AUTO_GAME_PAYOUT:
        markup.add(types.InlineKeyboardButton("🔴 Выключить", callback_data="auto_payout_off"))
    else:
        markup.add(types.InlineKeyboardButton("🟢 Включить", callback_data="auto_payout_on"))
    markup.add(types.InlineKeyboardButton("🎁 Сменить ID подарка", callback_data="auto_payout_change_gift_id"))
    markup.add(types.InlineKeyboardButton("💰 Сменить стоимость", callback_data="auto_payout_change_price"))
    markup.add(types.InlineKeyboardButton("🔄 Перезапустить клиент", callback_data="auto_payout_restart"))
    markup.add(types.InlineKeyboardButton("📊 Статус клиента", callback_data="auto_payout_status"))
    markup.add(types.InlineKeyboardButton("⬅ Назад", callback_data="sp_back"))
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == "auto_payout_on")
def auto_payout_on(call):
    if not is_admin(call.from_user.id):
        return
    global AUTO_GAME_PAYOUT
    AUTO_GAME_PAYOUT = True
    save_gift_settings()
    bot.answer_callback_query(call.id, "✅ Автовывод включен!")

@bot.callback_query_handler(func=lambda call: call.data == "auto_payout_off")
def auto_payout_off(call):
    if not is_admin(call.from_user.id):
        return
    global AUTO_GAME_PAYOUT
    AUTO_GAME_PAYOUT = False
    save_gift_settings()
    bot.answer_callback_query(call.id, "🔴 Автовывод выключен!")

@bot.callback_query_handler(func=lambda call: call.data == "auto_payout_change_gift_id")
def auto_payout_change_gift_id_start(call):
    if not is_admin(call.from_user.id):
        return
    admin_mode[call.from_user.id] = {"step": "change_gift_id"}
    bot.send_message(call.message.chat.id, f"🎁 Введите новый ID подарка:\nТекущий: <code>{TARGET_GIFT_ID}</code>", parse_mode="HTML")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "auto_payout_change_price")
def auto_payout_change_price_start(call):
    if not is_admin(call.from_user.id):
        return
    admin_mode[call.from_user.id] = {"step": "change_gift_price"}
    bot.send_message(call.message.chat.id, f"💰 Введите новую стоимость:\nТекущая: <b>{GIFT_PRICE}⭐️</b>", parse_mode="HTML")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "auto_payout_restart")
def auto_payout_restart(call):
    if not is_admin(call.from_user.id):
        return
    bot.send_message(call.message.chat.id, "🔄 Перезапуск клиента...")
    success = restart_gift_client()
    bot.send_message(call.message.chat.id, "✅ Клиент перезапущен!" if success else "❌ Ошибка перезапуска! Проверьте файл сессии.")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "auto_payout_status")
def auto_payout_status(call):
    if not is_admin(call.from_user.id):
        return
    if gift_client_ready:
        text = f"✅ <b>Клиент активен</b>\n\n📁 Сессия: {SESSION_NAME}.session\n🎁 ID: {TARGET_GIFT_ID}\n💰 Цена: {GIFT_PRICE}⭐️\n🟢 Автовывод: {'Вкл' if AUTO_GAME_PAYOUT else 'Выкл'}"
    else:
        text = "❌ <b>Клиент не активен</b>\n\nПроверьте файл сессии и подключение"
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="HTML")
    except:
        bot.send_message(call.message.chat.id, text, parse_mode="HTML")
    bot.answer_callback_query(call.id)

# ============================================================
# ADMIN CALLBACK: ПРОФИЛЬ / БАН / СООБЩЕНИЕ
# ============================================================
@bot.callback_query_handler(func=lambda call: call.data.startswith("profile_from_gift_"))
def show_profile_from_gift(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ Не админ")
        return
    uid = call.data.replace("profile_from_gift_", "")
    show_user_profile(call.message.chat.id, uid)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("game_done_"))
def game_done_handler(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ Не админ", show_alert=True)
        return
    user_id = int(call.data.replace("game_done_", ""))
    try:
        bot.send_message(user_id, "✅ Ваш выигрыш в игре «Мишки» подтвержден!")
    except:
        pass
    try:
        bot.edit_message_text(call.message.text + "\n\n✅ ВЫВЕДЕНО", call.message.chat.id, call.message.message_id, parse_mode="HTML")
    except:
        pass
    bot.answer_callback_query(call.id, "✅ Выведено")

@bot.callback_query_handler(func=lambda call: call.data.startswith("game_reject_"))
def game_reject_handler(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ Не админ", show_alert=True)
        return
    user_id = int(call.data.replace("game_reject_", ""))
    admin_states[call.from_user.id] = {"type": "game_reject", "user_id": user_id, "msg_id": call.message.message_id, "text": call.message.text or ""}
    bot.send_message(call.message.chat.id, "✏️ Введите причину отказа для пользователя:")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("done_") or call.data.startswith("reject_"))
def admin_actions(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ Не админ")
        return
    parts = call.data.split("_")
    action = parts[0]
    user_id = int(parts[1])
    msg_text = call.message.text or ""
    if action == "done":
        bot.send_message(user_id, "🎉 Подарок успешно выведен!")
        try:
            bot.edit_message_text(msg_text + "\n\n✅ ВЫВЕДЕНО", call.message.chat.id, call.message.message_id, parse_mode="HTML")
        except:
            pass
    elif action == "reject":
        price = 15 if "15" in msg_text else 25 if "25" in msg_text else 50 if "50" in msg_text else 100
        admin_states[call.from_user.id] = {"type": "reject", "user_id": user_id, "msg_id": call.message.message_id, "price": price, "text": msg_text}
        bot.send_message(call.message.chat.id, "✏️ Введите причину отказа:")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("banuser_"))
def ban_user_start(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ Не админ")
        return
    uid = call.data.replace("banuser_", "")
    admin_states[call.from_user.id] = {"type": "ban_user", "user_id": int(uid), "chat_id": call.message.chat.id, "msg_id": call.message.message_id}
    bot.send_message(call.message.chat.id, f"🚫 Введите причину блокировки пользователя {uid}:")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("unban_"))
def unban_user(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ Не админ")
        return
    uid = call.data.replace("unban_", "")
    if uid in data["banned"]:
        del data["banned"][uid]
        save_data()
        try:
            bot.send_message(int(uid), "✅ Вы разблокированы! Теперь вы можете пользоваться ботом.")
        except:
            pass
    bot.answer_callback_query(call.id, "✅ Разблокирован")

@bot.callback_query_handler(func=lambda call: call.data.startswith("msguser_"))
def msg_user_start(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ Не админ")
        return
    uid = call.data.replace("msguser_", "")
    admin_mode[call.from_user.id] = {"step": "send_message_to_user", "user_id": int(uid)}
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⬅ Отменить", callback_data=f"refresh_{uid}"))
    bot.send_message(call.message.chat.id, f"💬 Введите сообщение для пользователя {uid}:", reply_markup=markup)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("refresh_"))
def refresh_user_info(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ Не админ")
        return
    uid = call.data.replace("refresh_", "")
    show_user_profile(call.message.chat.id, uid)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("take_stars_"))
def take_stars_start(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ Не админ")
        return
    uid = call.data.replace("take_stars_", "")
    balance = get_balance(int(uid))
    admin_mode[call.from_user.id] = {"step": "take_stars_amount", "user_id": int(uid), "chat_id": call.message.chat.id, "msg_id": call.message.message_id}
    bot.send_message(call.message.chat.id, f"💰 Баланс {uid}: <b>{balance}⭐️</b>\n\nВведите количество звёзд для снятия:", parse_mode="HTML")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("reflist_"))
def show_referrals_list(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ Не админ")
        return
    uid = call.data.replace("reflist_", "")
    referrals = data.get("referrals", {}).get(uid, [])
    if not referrals:
        bot.send_message(call.message.chat.id, f"👤 Пользователь {uid} не имеет рефералов")
        bot.answer_callback_query(call.id)
        return
    text = f"👥 <b>Рефералы пользователя {uid}</b>\n\nВсего: {len(referrals)}\n\n"
    for i, ref_uid in enumerate(referrals[:50], 1):
        ref_username = data.get("usernames", {}).get(ref_uid, "нет username")
        subscribed = "✅" if ref_uid in data.get("subscribed_users", {}) else "❌"
        text += f"{i}. ID: <code>{ref_uid}</code> (@{ref_username}) {subscribed}\n"
    bot.send_message(call.message.chat.id, text, parse_mode="HTML")
    bot.answer_callback_query(call.id)

# ============================================================
# ОБРАБОТЧИК ВВОДА ТЕКСТА АДМИНА (ОБЩИЙ)
# ============================================================
ADMIN_MENU_BUTTONS = [
    "⚙️ Админ панель", "👤 Профиль", "📋 Задания", "💸 Заработать ⭐️",
    "🎁 Вывести ⭐️", "🧸 Мишки", "🔗 Спецссылки", "🔍 Поиск пользователя",
    "💰 Реферальная награда", "🎟 Промокоды", "📝 Задачи", "🌍 Блокировка стран",
    "📢 Спонсоры", "⏰ Отложенные сообщения", "⬅️ Назад", "📢 Рассылка",
    "📊 Статистика", "🎁 Автовыводы", "🏆 Турниры (админ)", "🏆 Турниры"
]

@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.from_user.id in admin_mode and m.text not in ADMIN_MENU_BUTTONS)
def admin_text_handler(message):
    mode = admin_mode.get(message.from_user.id, {})
    step = mode.get("step")

    if step == "change_gift_id":
        global TARGET_GIFT_ID
        try:
            TARGET_GIFT_ID = int(message.text.strip())
            save_gift_settings()
            bot.send_message(message.chat.id, f"✅ ID подарка изменён: <code>{TARGET_GIFT_ID}</code>", parse_mode="HTML")
        except:
            bot.send_message(message.chat.id, "❌ Введите число!")
        del admin_mode[message.from_user.id]

    elif step == "change_gift_price":
        global GIFT_PRICE
        try:
            new_price = int(message.text.strip())
            if new_price > 0:
                GIFT_PRICE = new_price
                save_gift_settings()
                bot.send_message(message.chat.id, f"✅ Стоимость: <b>{GIFT_PRICE}⭐️</b>", parse_mode="HTML")
            else:
                bot.send_message(message.chat.id, "❌ Стоимость должна быть > 0!")
        except:
            bot.send_message(message.chat.id, "❌ Введите число!")
        del admin_mode[message.from_user.id]

    elif step == "take_stars_amount":
        try:
            amount = float(message.text.strip())
            if amount <= 0:
                bot.send_message(message.chat.id, "❌ Введите положительное число!")
                return
            user_id = mode.get("user_id")
            if amount > get_balance(user_id):
                bot.send_message(message.chat.id, "❌ У пользователя недостаточно звёзд!")
                return
            add_balance(user_id, -amount)
            new_balance = get_balance(user_id)
            bot.send_message(message.chat.id, f"✅ Снято {amount}⭐️\n💰 Новый баланс: {new_balance}⭐️")
            try:
                bot.send_message(user_id, f"⚠️ Администратор снял {amount}⭐️\n💰 Баланс: {new_balance}⭐️")
            except:
                pass
        except:
            bot.send_message(message.chat.id, "❌ Введите число!")
        del admin_mode[message.from_user.id]

    elif step == "send_message_to_user":
        user_id = mode.get("user_id")
        try:
            bot.send_message(user_id, f"💬 <b>Сообщение от администратора:</b>\n\n{message.text}", parse_mode="HTML")
            bot.send_message(message.chat.id, f"✅ Сообщение отправлено пользователю {user_id}")
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Ошибка: {e}")
        del admin_mode[message.from_user.id]

    elif step == "create_promo":
        try:
            parts = message.text.split()
            code, amount, max_uses = parts[0], float(parts[1]), int(parts[2])
            data["promocodes"][code.upper()] = {"reward": amount, "uses": 0, "max_uses": max_uses}
            save_data()
            bot.send_message(message.chat.id, f"✅ Промокод создан\nКод: {code.upper()}\nНаграда: {amount}⭐️\nЛимит: {max_uses}")
        except:
            bot.send_message(message.chat.id, "❌ Формат: КОД СУММА ЛИМИТ\nПример: TEST 100 10")
        del admin_mode[message.from_user.id]

    elif step == "set_sponsor_limit":
        try:
            limit = int(message.text.strip())
            if limit < 0:
                bot.send_message(message.chat.id, "❌ Лимит не может быть отрицательным")
                return
            channel_id = mode.get("channel_id")
            if "sponsor_limits" not in data:
                data["sponsor_limits"] = {}
            data["sponsor_limits"][channel_id] = limit
            save_data()
            bot.send_message(message.chat.id, f"✅ Лимит установлен: {limit if limit > 0 else 'безлимит'}")
        except:
            bot.send_message(message.chat.id, "❌ Введите число")
        del admin_mode[message.from_user.id]

    elif step == "add_application_channel":
        channel_input = message.text.strip()
        if channel_input.startswith("-100") or channel_input.lstrip("-").isdigit():
            channel_id = channel_input
            try:
                chat = bot.get_chat(channel_id)
                channel_name = chat.title or "Канал"
            except:
                channel_name = "Канал"
        else:
            if "t.me/" in channel_input:
                channel_id = "@" + channel_input.split("/")[-1]
            elif channel_input.startswith("@"):
                channel_id = channel_input
            else:
                channel_id = "@" + channel_input
            try:
                chat = bot.get_chat(channel_id)
                channel_name = chat.title
            except:
                bot.send_message(message.chat.id, "❌ Канал не найден")
                del admin_mode[message.from_user.id]
                return
        if "application_channels" not in data:
            data["application_channels"] = []
        data["application_channels"].append({"id": channel_id, "name": channel_name})
        save_data()
        bot.send_message(message.chat.id, f"✅ Канал {channel_name} добавлен в заявки!")
        del admin_mode[message.from_user.id]

    elif step == "block_country_add":
        codes = message.text.strip().replace(" ", "").split(",")
        blocked = data.get("blocked_countries", [])
        added = []
        for code in codes:
            code = code.strip().lower()
            if len(code) >= 2 and code not in blocked:
                blocked.append(code)
                added.append(code)
        data["blocked_countries"] = blocked
        save_data()
        if added:
            bot.send_message(message.chat.id, f"✅ Добавлены коды: {', '.join(added)}")
        else:
            bot.send_message(message.chat.id, "❌ Все коды уже добавлены или неверный формат")
        del admin_mode[message.from_user.id]

    elif step == "block_country_msg":
        new_msg = message.text.strip()
        data["blocked_message"] = new_msg
        save_data()
        bot.send_message(message.chat.id, f"✅ Сообщение изменено:\n<i>{new_msg}</i>", parse_mode="HTML")
        del admin_mode[message.from_user.id]

    elif step == "change_referral_reward":
        try:
            new_reward = float(message.text.strip())
            if new_reward < 0:
                bot.send_message(message.chat.id, "❌ Награда не может быть отрицательной")
                del admin_mode[message.from_user.id]
                return
            old_reward = data.get("referral_reward", 2.5)
            data["referral_reward"] = new_reward
            save_data()
            bot.send_message(message.chat.id, f"✅ <b>Реферальная награда изменена!</b>\n\nСтарая: {old_reward}⭐️\nНовая: <b>{new_reward}⭐️</b>", parse_mode="HTML")
        except:
            bot.send_message(message.chat.id, "❌ Введите число")
        del admin_mode[message.from_user.id]

    elif step == "add_task_text":
        text = message.text
        admin_mode[message.from_user.id] = {"step": "add_task_max_uses", "text": text}
        bot.send_message(message.chat.id, "📊 Введите лимит выполнений:\n0 = неограниченно")

    elif step == "add_task_max_uses":
        try:
            max_uses = int(message.text.strip())
            if max_uses < 0:
                max_uses = 999999
        except:
            bot.send_message(message.chat.id, "❌ Введите число")
            return
        admin_mode[message.from_user.id] = {"step": "add_task_reward", "text": mode.get("text"), "max_uses": max_uses}
        bot.send_message(message.chat.id, "💰 Введите награду за задание (в звёздах):\nПример: 1.5")

    elif step == "add_task_reward":
        try:
            reward = float(message.text.strip())
            if reward <= 0:
                bot.send_message(message.chat.id, "❌ Награда должна быть > 0")
                return
        except:
            bot.send_message(message.chat.id, "❌ Введите число")
            return
        admin_mode[message.from_user.id] = {"step": "add_task_check_channel", "text": mode.get("text"), "max_uses": mode.get("max_uses"), "reward": reward}
        bot.send_message(message.chat.id, "🔍 Введите ID или @username канала для проверки подписки:\n• @username\n• -1001234567890")

    elif step == "add_task_check_channel":
        check_input = message.text.strip()
        if check_input.startswith("-100") or check_input.lstrip("-").isdigit():
            check_id = check_input
        elif check_input.startswith("@"):
            check_id = check_input
        elif "t.me/" in check_input:
            check_id = "@" + check_input.split("/")[-1]
        else:
            check_id = "@" + check_input
        try:
            chat = bot.get_chat(check_id)
            check_name = chat.title or check_id
        except:
            check_name = check_id
        admin_mode[message.from_user.id] = {
            "step": "add_task_link", "text": mode.get("text"), "max_uses": mode.get("max_uses"),
            "reward": mode.get("reward"), "channel_id": check_id, "channel_name": check_name
        }
        bot.send_message(message.chat.id, f"✅ Канал: {check_name} ({check_id})\n\n🔗 Введите ссылку для кнопки «Перейти»:\nhttps://t.me/channelname")

    elif step == "add_task_link":
        channel = message.text.strip()
        if channel.startswith("-100") or channel.lstrip("-").isdigit():
            channel_link = f"https://t.me/c/{channel.replace('-100', '')}"
        elif "t.me/" in channel:
            channel_link = channel
        elif channel.startswith("@"):
            channel_link = f"https://t.me/{channel.replace('@', '')}"
        else:
            channel_link = f"https://t.me/{channel}"
        if not channel_link.startswith("http"):
            channel_link = "https://" + channel_link
        max_uses = mode.get("max_uses", 0)
        data["tasks"].append({
            "channel_id": mode.get("channel_id"),
            "channel_name": mode.get("channel_name"),
            "link": channel_link,
            "text": mode.get("text"),
            "reward": mode.get("reward"),
            "max_uses": max_uses if max_uses > 0 else 999999,
            "uses": 0
        })
        save_data()
        bot.send_message(
            message.chat.id,
            f"✅ <b>Задание добавлено!</b>\n\n"
            f"🔍 Канал: {mode.get('channel_name')} ({mode.get('channel_id')})\n"
            f"🔗 Ссылка: {channel_link}\n"
            f"💰 Награда: {mode.get('reward')}⭐️\n"
            f"📊 Лимит: {max_uses if max_uses > 0 else '∞'}",
            parse_mode="HTML"
        )
        del admin_mode[message.from_user.id]

    elif step == "delete_task":
        try:
            idx = int(message.text) - 1
            if 0 <= idx < len(data["tasks"]):
                removed = data["tasks"].pop(idx)
                save_data()
                bot.send_message(message.chat.id, f"🗑 Удалено: {removed.get('channel_name', 'Задание')} | {removed['reward']}⭐️")
            else:
                bot.send_message(message.chat.id, "❌ Неверный номер")
        except:
            bot.send_message(message.chat.id, "❌ Введите число")
        del admin_mode[message.from_user.id]

    elif step == "add_delayed_delay":
        try:
            delay = int(message.text.strip())
            if delay < 1:
                bot.send_message(message.chat.id, "❌ Задержка должна быть > 0")
                del admin_mode[message.from_user.id]
                return
            admin_mode[message.from_user.id] = {"step": "add_delayed_photo", "delay": delay}
            bot.send_message(message.chat.id, "📷 Отправьте фото или напишите 0 для пропуска:")
        except:
            bot.send_message(message.chat.id, "❌ Введите число")
            del admin_mode[message.from_user.id]

    elif step == "add_delayed_photo":
        if message.content_type == "photo":
            photo_id = message.photo[-1].file_id
            admin_mode[message.from_user.id] = {"step": "add_delayed_text", "delay": mode.get("delay"), "photo": photo_id}
            bot.send_message(message.chat.id, "📝 Введите текст сообщения:")
        else:
            admin_mode[message.from_user.id] = {"step": "add_delayed_text", "delay": mode.get("delay"), "photo": None}
            bot.send_message(message.chat.id, "📝 Введите текст сообщения:")

    elif step == "add_delayed_text":
        delay = mode.get("delay")
        photo = mode.get("photo")
        text = message.text
        admin_mode[message.from_user.id] = {"step": "add_delayed_buttons", "delay": delay, "photo": photo, "text": text}
        bot.send_message(message.chat.id, "🔘 Введите кнопки (Текст - https://link) или 0:")

    elif step == "add_delayed_buttons":
        delay = mode.get("delay")
        photo = mode.get("photo")
        text = mode.get("text")
        buttons = []
        if message.text != "0":
            for line in message.text.split("\n"):
                if " - " in line:
                    btn_text, url = line.split(" - ", 1)
                    buttons.append({"text": btn_text.strip(), "url": url.strip()})
        msg_data = {"delay": delay, "text": text, "buttons": buttons if buttons else None, "photo": photo}
        if "delayed_messages" not in data:
            data["delayed_messages"] = []
        data["delayed_messages"].append(msg_data)
        save_data()
        bot.send_message(message.chat.id, f"✅ Сообщение добавлено!\n⏰ Задержка: {delay} сек\n📷 Фото: {'Да' if photo else 'Нет'}\n🔘 Кнопок: {len(buttons)}")
        del admin_mode[message.from_user.id]

    if step == "tour_title":
        title = message.text.strip()
        if len(title) < 3:
            bot.send_message(message.chat.id, "❌ Название слишком короткое (мин. 3 символа)")
            return
        admin_mode[message.from_user.id] = {"step": "tour_description", "title": title}
        bot.send_message(message.chat.id, "📝 Введите <b>описание</b> турнира:", parse_mode="HTML")
        return

    elif step == "tour_description":
        desc = message.text.strip()
        admin_mode[message.from_user.id] = {
            "step": "tour_prize",
            "title": mode.get("title"),
            "description": desc
        }
        bot.send_message(message.chat.id, "🎁 Введите <b>приз</b> (например: 🚀 Ракета):", parse_mode="HTML")
        return

    elif step == "tour_prize":
        prize = message.text.strip()
        admin_mode[message.from_user.id] = {
            "step": "tour_end_time",
            "title": mode.get("title"),
            "description": mode.get("description"),
            "prize": prize
        }
        bot.send_message(
            message.chat.id,
            "⏰ Введите <b>дату и время окончания</b> турнира:\n\n"
            "Формат: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\n"
            "Пример: <code>31.12.2026 23:59</code>",
            parse_mode="HTML"
        )
        return

    elif step == "tour_end_time":
        try:
            text = message.text.strip()
            end_dt = datetime.strptime(text, "%d.%m.%Y %H:%M")
            end_time = int(end_dt.timestamp())
            if end_time <= int(time.time()):
                bot.send_message(message.chat.id, "❌ Дата должна быть в будущем!")
                return
            tid = str(int(time.time()))
            tournament = {
                "id": tid,
                "title": mode.get("title"),
                "description": mode.get("description"),
                "prize": mode.get("prize"),
                "metric": "game_spent",  # фіксована метрика
                "end_time": end_time,
                "active": True,
                "created_by": message.from_user.id,
                "created_at": int(time.time())
            }
            data.setdefault("tournaments", []).append(tournament)
            data.setdefault("tournament_participants", {})[tid] = {}
            save_data()

            end_str = end_dt.strftime("%d.%m.%Y %H:%M")
            text_out = (
                f"✅ <b>Турнир создан!</b>\n\n"
                f"🏆 {tournament['title']}\n"
                f"📝 {tournament['description']}\n"
                f"🎁 Приз: {tournament['prize']}\n"
                f"📈 Метрика: потрачено ⭐️ в «Мишках»\n"
                f"⏰ До: {end_str}\n\n"
                f"ID: <code>{tid}</code>"
            )
            bot.send_message(message.chat.id, text_out, parse_mode="HTML")
        except ValueError:
            bot.send_message(message.chat.id,
                             "❌ Неверный формат!\n\nИспользуйте: <code>31.12.2026 23:59</code>",
                             parse_mode="HTML")
            return
        del admin_mode[message.from_user.id]
        return


    elif step == "add_sponsor_link":
        channel_input = message.text.strip()
        if channel_input.startswith("-100") or channel_input.lstrip("-").isdigit():
            channel_id = channel_input
            try:
                chat = bot.get_chat(channel_id)
                channel_name = chat.title or "Канал"
                channel_link = chat.invite_link or f"https://t.me/c/{str(channel_id)[4:]}"
            except:
                channel_link = channel_input
                channel_name = "Канал"
        elif "t.me/+" in channel_input or "joinchat" in channel_input:
            channel_link = channel_input
            try:
                chat = bot.get_chat(channel_input)
                channel_id = str(chat.id)
                channel_name = chat.title or "Канал"
            except:
                channel_id = channel_input
                channel_name = "Канал"
        elif "t.me/" in channel_input:
            channel_link = channel_input
            channel_username = channel_input.split("/")[-1].split("?")[0]
            channel_id = "@" + channel_username
            try:
                chat = bot.get_chat(channel_id)
                channel_name = chat.title or channel_username
            except:
                channel_name = channel_username
        elif channel_input.startswith("@"):
            channel_id = channel_input
            channel_link = f"https://t.me/{channel_input.replace('@', '')}"
            try:
                chat = bot.get_chat(channel_id)
                channel_name = chat.title
            except:
                channel_name = channel_id
        else:
            channel_id = "@" + channel_input
            channel_link = f"https://t.me/{channel_input}"
            try:
                chat = bot.get_chat(channel_id)
                channel_name = chat.title
            except:
                bot.send_message(message.chat.id, "❌ Не удалось найти канал")
                del admin_mode[message.from_user.id]
                return
        admin_mode[message.from_user.id] = {
            "step": "add_sponsor_button_text",
            "channel_id": channel_id, "channel_link": channel_link, "channel_name": channel_name
        }
        bot.send_message(message.chat.id, f"✅ Канал: {channel_name}\n\n📝 Введите текст для кнопки:\nПример: Подписаться на спонсора")

    elif step == "add_sponsor_button_text":
        button_text = message.text.strip()
        admin_mode[message.from_user.id] = {
            "step": "add_sponsor_check_link",
            "channel_id": mode.get("channel_id"), "channel_link": mode.get("channel_link"),
            "channel_name": mode.get("channel_name"), "button_text": button_text
        }
        bot.send_message(message.chat.id, "🔍 Введите ID или ссылку канала для ПРОВЕРКИ подписки:\n• @username\n• -1001234567890\n\nЕсли совпадает — введите то же самое.")

    elif step == "add_sponsor_check_link":
        check_input = message.text.strip()
        if check_input.startswith("-100") or check_input.lstrip("-").isdigit():
            check_id = check_input
        elif check_input.startswith("@"):
            check_id = check_input
        elif "t.me/" in check_input:
            check_id = "@" + check_input.split("/")[-1]
        else:
            check_id = "@" + check_input
        admin_mode[message.from_user.id] = {
            "step": "add_sponsor_limit",
            "channel_id": mode.get("channel_id"), "channel_link": mode.get("channel_link"),
            "channel_name": mode.get("channel_name"), "button_text": mode.get("button_text"), "check_id": check_id
        }
        bot.send_message(message.chat.id, f"✅ Канал для проверки: {check_id}\n\n👥 Введите лимит подписок:\n0 - безлимит\n50 - максимум 50")

    elif step == "add_sponsor_limit":
        try:
            limit = int(message.text.strip())
            if limit < 0:
                limit = 0
        except:
            bot.send_message(message.chat.id, "❌ Введите число")
            return
        check_id = mode.get("check_id")
        channel_info = {
            "id": check_id, "name": mode.get("channel_name"),
            "link": mode.get("channel_link"), "button_text": mode.get("button_text")
        }
        if "sponsor_channels" not in data:
            data["sponsor_channels"] = []
        if any(ch["id"] == check_id for ch in data["sponsor_channels"]):
            bot.send_message(message.chat.id, "❌ Этот канал уже добавлен!")
            del admin_mode[message.from_user.id]
            return
        data["sponsor_channels"].append(channel_info)
        if limit > 0:
            if "sponsor_limits" not in data:
                data["sponsor_limits"] = {}
            data["sponsor_limits"][check_id] = limit
        save_data()
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(mode.get("button_text"), url=mode.get("channel_link")))
        bot.send_message(
            message.chat.id,
            f"✅ <b>Спонсор добавлен!</b>\n\n📢 {mode.get('channel_name')}\n🔍 ID проверки: {check_id}\n👥 Лимит: {limit if limit > 0 else 'безлимит'}",
            parse_mode="HTML", reply_markup=markup
        )
        del admin_mode[message.from_user.id]

    elif step == "search_user":
        search_query = message.text.strip()
        searching_msg = bot.send_message(message.chat.id, "⏳ Ищу пользователя...")
        found_uid = None
        found_username = None
        if search_query.isdigit():
            uid = search_query
            if uid in data["users"]:
                found_uid = uid
        else:
            username = search_query.replace("@", "").strip()
            local_uid = search_user_local(username)
            if local_uid:
                found_uid = local_uid
                found_username = data.get("usernames", {}).get(local_uid, username)
            else:
                try:
                    user_info = bot.get_chat(f"@{username}")
                    if user_info:
                        found_uid = str(user_info.id)
                        found_username = user_info.username
                        if found_uid not in data["users"]:
                            try:
                                bot.delete_message(message.chat.id, searching_msg.message_id)
                            except:
                                pass
                            bot.send_message(message.chat.id, f"❌ @{found_username} (ID: {found_uid}) есть в Telegram, но не зарегистрирован в боте")
                            del admin_mode[message.from_user.id]
                            return
                except Exception as e:
                    try:
                        bot.delete_message(message.chat.id, searching_msg.message_id)
                    except:
                        pass
                    bot.send_message(message.chat.id, "❌ Пользователь не найден. Попробуйте ввести ID.")
                    del admin_mode[message.from_user.id]
                    return
        try:
            bot.delete_message(message.chat.id, searching_msg.message_id)
        except:
            pass
        if found_uid:
            show_user_profile(message.chat.id, found_uid)
        else:
            bot.send_message(message.chat.id, "❌ Пользователь не найден в базе данных бота")
        del admin_mode[message.from_user.id]

    elif step == "create_sp_link":
        name = message.text.strip().replace(" ", "_")
        if not name.isalnum() and "_" not in name:
            bot.send_message(message.chat.id, "❌ Используйте только латиницу и цифры")
            return
        if "special_links" not in data:
            data["special_links"] = {}
        if name in data["special_links"]:
            bot.send_message(message.chat.id, "❌ Ссылка уже существует")
            del admin_mode[message.from_user.id]
            return
        data["special_links"][name] = {"clicks": 0, "task_completions": 0, "game_plays": 0}
        save_data()
        bot_username = bot.get_me().username
        bot.send_message(
            message.chat.id,
            f"✅ Ссылка создана!\n\n🔗 https://t.me/{bot_username}?start={name}\n\nКод: <code>{name}</code>",
            parse_mode="HTML"
        )
        del admin_mode[message.from_user.id]

# ============================================================
# ОБРАБОТЧИК ПРИЧИН ОТКАЗА/БАНА (admin_states)
# ============================================================
@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.from_user.id in admin_states and m.text not in ADMIN_MENU_BUTTONS)
def admin_reason_handler(message):
    state = admin_states[message.from_user.id]
    reason = message.text
    user_id = state["user_id"]
    if state["type"] == "reject":
        add_balance(user_id, state.get("price", 15))
        try:
            bot.send_message(user_id, f"❌ Ваша заявка отклонена:\n{reason}\n\n⭐️ Баланс возвращён")
            bot.delete_message(CHANNEL_ID, state["msg_id"])
        except:
            pass
    elif state["type"] == "ban_user":
        data["banned"][str(user_id)] = reason
        save_data()
        try:
            bot.send_message(user_id, f"🚫 Вы заблокированы\n\nПричина: {reason}")
        except:
            pass
    elif state["type"] == "game_reject":
        try:
            bot.send_message(user_id, f"❌ Ваш выигрыш в игре «Мишки» отклонён\n\nПричина: {reason}")
            bot.delete_message(CHANNEL_ID, state["msg_id"])
        except:
            pass
    del admin_states[message.from_user.id]
    bot.send_message(message.chat.id, "✅ Готово!")

# ============================================================
# ОБРОБНИК ФОТО ДЛЯ ВІДКЛАДЕНИХ ПОВІДОМЛЕНЬ
# ============================================================
@bot.message_handler(content_types=['photo'], func=lambda m: is_admin(m.from_user.id) and admin_mode.get(m.from_user.id, {}).get("step") == "add_delayed_photo")
def delayed_photo_handler(message):
    mode = admin_mode.get(message.from_user.id, {})
    photo_id = message.photo[-1].file_id
    admin_mode[message.from_user.id] = {"step": "add_delayed_text", "delay": mode.get("delay"), "photo": photo_id}
    bot.send_message(message.chat.id, "📝 Введите текст сообщения:")

# ============================================================
# ЗАПУСК БОТА
# ============================================================
if __name__ == "__main__":
    print("=" * 50)
    print("🚀 ЗАПУСК БОТА")
    print("=" * 50)

    session_file = f"{SESSION_NAME}.session"
    if TELEGRAM_IMPORT_OK and os.path.exists(session_file):
        print(f"[OK] Файл сессии найден: {session_file}")
        telethon_thread = threading.Thread(target=start_telethon_loop, daemon=True)
        telethon_thread.start()
        timeout = 15
        start_wait = time.time()
        while not gift_client_ready and (time.time() - start_wait) < timeout:
            time.sleep(0.5)
        if gift_client_ready:
            print("[OK] Telethon клиент готов к работе")
        else:
            print("[WARNING] Telethon не инициализирован. Автовывод отключён.")
    else:
        if not TELEGRAM_IMPORT_OK:
            print("[WARNING] Telethon не установлен. Автовывод недоступен.")
        else:
            print(f"[WARNING] Файл сессии не найден: {session_file}. Автовывод отключён.")

    print("=" * 50)
    print("✅ БОТ ЗАПУЩЕН")
    print("=" * 50)

    bot.infinity_polling(timeout=30, long_polling_timeout=30)