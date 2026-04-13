import asyncio, logging
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo,
    LabeledPrice, PreCheckoutQuery, Message, CallbackQuery
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import BOT_TOKEN, WEBAPP_URL, CHANNEL_ID, ADMIN_IDS, OWNER_ID, PLANS
import database as db

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()

async def safe_edit_or_answer(call: CallbackQuery, text: str, parse_mode: str = "Markdown", reply_markup=None):
    try:
        if call.message.photo:
            await call.message.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
        else:
            await call.message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except:
        await call.message.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)

BANNER_SECTIONS = {
    "home": "🏠 Главная",
    "profile": "👤 Профиль",
    "subscription": "◈ Подписка",
    "buy": "💳 Покупка",
    "referrals": "👥 Рефералы",
}

class AdminSt(StatesGroup):
    broadcast = State()
    give_id = State()
    give_amt = State()
    promo = State()

class BannerSt(StatesGroup):
    waiting_media = State()

async def check_sub(user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(CHANNEL_ID, user_id)
        return m.status not in ["left","kicked","banned"]
    except:
        return False

def channel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться на канал", url=f"https://t.me/{CHANNEL_ID.lstrip('@')}")],
        [InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub")]
    ])

def main_kb(uid: int):
    b = InlineKeyboardBuilder()
    b.button(text="🌸 LiliumVPN", web_app=WebAppInfo(url=WEBAPP_URL))
    b.button(text="👤 Профиль", callback_data="profile")
    b.button(text="🆘 Поддержка", url="https://t.me/LiliumVPNsupport")
    if uid in ADMIN_IDS:
        b.button(text="⚙️ Админ-панель", callback_data="admin")
    b.adjust(1)
    return b.as_markup()

@router.message(CommandStart())
async def start(msg: Message):
    u = msg.from_user
    args = msg.text.split(maxsplit=1)
    ref = args[1].replace("ref_","") if len(args)>1 and args[1].startswith("ref_") else (args[1] if len(args)>1 else None)

    user_data, is_new = await db.get_or_create_user(tg_id=u.id, username=u.username, first_name=u.first_name, ref_code=ref)

    subscribed = await check_sub(u.id)
    await db.set_channel_subscribed(u.id, subscribed)

    if not subscribed:
        await msg.answer(
            "🌸 *LiliumVPN* — свобода в сети\n\nДля использования подпишись на наш канал.",
            parse_mode="Markdown", reply_markup=channel_kb()
        )
        return

    if is_new and ref:
        await db.add_balance(u.id, 50)
        await msg.answer("🎁 *+50 ₽* начислено за переход по реферальной ссылке!", parse_mode="Markdown")

    text = "🌸 *LiliumVPN*\n\n"
    banner = await db.get_banner("home")
    
    if is_new:
        text = "Добро пожаловать! Активируй *бесплатный пробный период* ниже."
    else:
        sub = await db.get_active_subscription(u.id)
        if sub:
            import datetime
            d = max(0,(sub["end_date"] - datetime.datetime.utcnow()).days)
            text = f"📡 Тариф: *{sub['plan'].upper()}* · Осталось: *{d} дн.*\nВыбери действие:"
        else:
            text = "⚠️ Нет активной подписки.\n\nВыбери действие:"
    
    if banner:
        await msg.answer_photo(photo=banner["file_id"], caption=text, parse_mode="Markdown", reply_markup=main_kb(u.id))
    else:
        await msg.answer(text, parse_mode="Markdown", reply_markup=main_kb(u.id))

@router.callback_query(F.data=="check_sub")
async def cb_check_sub(call: CallbackQuery):
    if await check_sub(call.from_user.id):
        await db.set_channel_subscribed(call.from_user.id, True)
        banner = await db.get_banner("home")
        text = "✅ *Добро пожаловать в LiliumVPN!*\n\nВыбери действие:"
        if banner:
            await call.message.answer_photo(photo=banner["file_id"], caption=text, parse_mode="Markdown", reply_markup=main_kb(call.from_user.id))
        else:
            await call.message.answer(text, parse_mode="Markdown", reply_markup=main_kb(call.from_user.id))
    else:
        await call.answer("Ты ещё не подписался!", show_alert=True)

@router.callback_query(F.data=="profile")
async def cb_profile(call: CallbackQuery):
    u = await db.get_user(call.from_user.id)
    if not u: return
    await safe_edit_or_answer(call,
        f"👤 *Профиль*\n\n🆔 ID: `{u['telegram_id']}`\n@{u['username'] or '—'}\n🏷 Код: `{u['ref_code']}`\n💰 Баланс: *{u['balance']} ₽*",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад",callback_data="back")]])
    )

@router.callback_query(F.data=="subscription")
async def cb_subscription(call: CallbackQuery):
    sub = await db.get_active_subscription(call.from_user.id)
    if not sub:
        text = "❌ Нет активной подписки."
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💳 Купить",callback_data="buy")],[InlineKeyboardButton(text="◀️ Назад",callback_data="back")]])
    else:
        import datetime
        d = max(0,(sub["end_date"]-datetime.datetime.utcnow()).days)
        limit = sub['traffic_limit_mb']
        used = sub['traffic_used_mb']
        tr = f"{used//1024}ГБ / ∞" if limit==-1 else f"{used//1024}ГБ / {limit//1024}ГБ"
        text = f"📡 *Подписка*\n\n📦 *{sub['plan'].upper()}*\n📅 До: {sub['end_date'].strftime('%d.%m.%Y')}\n⏳ Осталось: *{d} дн.*\n📊 Трафик: {tr}\n🖥 Устройств: {sub['devices']}"
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Продлить",callback_data="buy")],[InlineKeyboardButton(text="◀️ Назад",callback_data="back")]])
    await safe_edit_or_answer(call, text, reply_markup=kb)

@router.callback_query(F.data=="buy")
async def cb_buy(call: CallbackQuery):
    b = InlineKeyboardBuilder()
    for k,p in PLANS.items():
        if k=="trial": continue
        b.button(text=f"{p['name']} — {p['price_rub']}₽ / {p['price_stars']}⭐", callback_data=f"plan_{k}")
    b.button(text="🎁 Пробный (бесплатно)", callback_data="plan_trial")
    b.button(text="◀️ Назад", callback_data="back")
    b.adjust(1)
    await safe_edit_or_answer(call, "💳 *Выбери тариф:*", reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("plan_"))
async def cb_plan(call: CallbackQuery):
    key = call.data.replace("plan_","")
    plan = PLANS.get(key)
    if not plan: return

    if key == "trial":
        sub = await db.get_active_subscription(call.from_user.id)
        if sub:
            await call.answer("У тебя уже есть подписка!", show_alert=True); return
        await db.create_subscription(call.from_user.id, "trial")
        await call.message.answer(
            "✅ *Пробный период активирован!*\n3 дня · 10 ГБ · 1 устройство\n\nОткрой кабинет для получения ключа.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🌸 Открыть кабинет", web_app=WebAppInfo(url=WEBAPP_URL))],
                [InlineKeyboardButton(text="◀️ Назад",callback_data="back")]
            ])
        )
        return

    b = InlineKeyboardBuilder()
    b.button(text=f"⭐ {plan['price_stars']} Stars", callback_data=f"pay_stars_{key}")
    b.button(text="🪙 Крипто", callback_data=f"pay_crypto_{key}")
    b.button(text="💳 CKassa", callback_data=f"pay_ckassa_{key}")
    b.button(text="💰 С баланса", callback_data=f"pay_bal_{key}")
    b.button(text="◀️ Назад", callback_data="buy")
    b.adjust(1)
    traf = "∞" if plan['traffic_gb']==-1 else f"{plan['traffic_gb']} ГБ"
    await safe_edit_or_answer(call,
        f"📦 *{plan['name']}*\n\n📊 {traf}/мес · 🖥 {plan['devices']} уст. · 📅 {plan['days']} дней\n\nСпособ оплаты:",
        reply_markup=b.as_markup()
    )

@router.callback_query(F.data.startswith("pay_stars_"))
async def cb_pay_stars(call: CallbackQuery):
    key = call.data.replace("pay_stars_",""); plan = PLANS.get(key)
    await call.message.answer_invoice(
        title=f"LiliumVPN — {plan['name']}",
        description=f"{plan['days']} дней · {'∞' if plan['traffic_gb']==-1 else plan['traffic_gb']} ГБ · {plan['devices']} уст.",
        payload=f"vpn_{key}_{call.from_user.id}",
        currency="XTR",
        prices=[LabeledPrice(label=plan['name'], amount=plan['price_stars'])],
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"⭐ Оплатить {plan['price_stars']} Stars", pay=True)],
            [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"plan_{key}")]
        ])
    )

@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)

@router.message(F.successful_payment)
async def on_payment(msg: Message):
    parts = msg.successful_payment.invoice_payload.split("_")
    key, uid = parts[1], int(parts[2])
    payment = await db.create_payment(uid, PLANS[key]["price_rub"], "stars", key, msg.successful_payment.invoice_payload)
    await db.confirm_payment(payment["id"])
    await db.create_subscription(uid, key)
    await db.process_referral_reward(uid, PLANS[key]["price_rub"], "stars")
    await msg.answer(
        f"✅ *Оплата прошла!*\n\n📦 *{PLANS[key]['name']}* активирован.\nОткрой кабинет для ключа.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🌸 Открыть кабинет", web_app=WebAppInfo(url=WEBAPP_URL))]])
    )

@router.callback_query(F.data.startswith("pay_bal_"))
async def cb_pay_bal(call: CallbackQuery):
    key = call.data.replace("pay_bal_",""); plan = PLANS.get(key)
    u = await db.get_user(call.from_user.id)
    if float(u["balance"] or 0) < plan["price_rub"]:
        await call.answer(f"Недостаточно средств: нужно {plan['price_rub']} ₽", show_alert=True); return
    p = await db.get_pool()
    async with p.acquire() as conn:
        await conn.execute("UPDATE users SET balance=balance-$1 WHERE telegram_id=$2", plan["price_rub"], call.from_user.id)
    payment = await db.create_payment(call.from_user.id, plan["price_rub"], "balance", key)
    await db.confirm_payment(payment["id"])
    await db.create_subscription(call.from_user.id, key)
    await db.process_referral_reward(call.from_user.id, plan["price_rub"], "balance")
    await safe_edit_or_answer(call, f"✅ *{plan['name']}* активирован! Списано *{plan['price_rub']} ₽*.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🌸 Открыть кабинет", web_app=WebAppInfo(url=WEBAPP_URL))]]))

@router.callback_query(F.data.startswith("pay_crypto_"))
async def cb_pay_crypto(call: CallbackQuery):
    key = call.data.replace("pay_crypto_",""); plan = PLANS.get(key)
    usdt = round(plan["price_rub"]/90, 2)
    await safe_edit_or_answer(call,
        f"🪙 *Оплата криптой*\n\n{plan['name']} · ~{usdt} USDT\n\nОтправь платёж через @CryptoBot и напиши в поддержку с чеком.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💸 CryptoBot",url="https://t.me/CryptoBot")],
            [InlineKeyboardButton(text="📩 Поддержка",url="https://t.me/LiliumVPNsupport")],
            [InlineKeyboardButton(text="◀️ Назад",callback_data=f"plan_{key}")]
        ])
    )

@router.callback_query(F.data.startswith("pay_ckassa_"))
async def cb_pay_ckassa(call: CallbackQuery):
    key = call.data.replace("pay_ckassa_","")
    plan = PLANS.get(key)
    await call.answer(f"CKassa: напиши в поддержку для оплаты {plan['price_rub']} ₽", show_alert=True)

@router.callback_query(F.data=="referrals")
async def cb_ref(call: CallbackQuery):
    stats = await db.get_referral_stats(call.from_user.id)
    code = stats.get("ref_code","")
    link = f"https://t.me/LiliumVPNBot?start=ref_{code}"
    await safe_edit_or_answer(call,
        f"👥 *Рефералы*\n\n🏷 Твой код: `{code}`\n👫 Всего: *{stats.get('total',0)}*\n💰 Заработок: *+{stats.get('earned',0):.2f} ₽*\n\n📎 Ссылка:\n`{link}`\n\n_10% с каждой оплаты реферала_",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Поделиться",url=f"https://t.me/share/url?url={link}")],
            [InlineKeyboardButton(text="◀️ Назад",callback_data="back")]
        ])
    )

@router.callback_query(F.data=="admin")
async def cb_admin(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: return
    stats = await db.get_admin_stats()
    is_owner = call.from_user.id == OWNER_ID
    text = (f"⚙️ *Admin Panel*\n\n👥 Пользователей: *{stats['total_users']}*\n"
            f"📡 Подписок: *{stats['active_subs']}*\n💰 Сегодня: *{stats['revenue_today']} ₽*\n"
            f"📈 Месяц: *{stats['revenue_month']} ₽*")
    b = InlineKeyboardBuilder()
    b.button(text="👥 Пользователи",callback_data="adm_users")
    b.button(text="🌳 Мои рефералы",callback_data="adm_refs")
    if is_owner:
        b.button(text="📢 Рассылка",callback_data="adm_bc")
        b.button(text="💰 Начислить",callback_data="adm_gb")
        b.button(text="🎟 Промокод",callback_data="adm_promo")
        b.button(text="🎨 Баннеры",callback_data="adm_banners")
    b.button(text="◀️ Назад",callback_data="back")
    b.adjust(1)
    await safe_edit_or_answer(call, text, reply_markup=b.as_markup())

@router.callback_query(F.data=="adm_users")
async def cb_adm_users(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: return
    users = await db.get_all_users_paginated(0,20)
    lines = [f"• @{u['username'] or '—'} `{u['telegram_id']}` [{u['role']}]" for u in users[:20]]
    await safe_edit_or_answer(call, "👥 *Пользователи (20 последних):*\n\n"+"\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад",callback_data="admin")]]))

@router.callback_query(F.data=="adm_refs")
async def cb_adm_refs(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: return
    stats = await db.get_referral_stats(call.from_user.id)
    refs = stats.get("referrals",[])
    lines = [f"└ @{r['username'] or r['first_name'] or '—'} · `{r['ref_code']}` · {'✅' if r['has_sub'] else '❌'}" for r in refs[:15]]
    text = f"🌳 *Твои рефералы* ({stats.get('total',0)}):\n\n" + ("\n".join(lines) if lines else "Пока нет")
    await safe_edit_or_answer(call, text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад",callback_data="admin")]]))

@router.callback_query(F.data=="adm_bc")
async def cb_bc(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID: return
    await safe_edit_or_answer(call, "📢 Введи текст рассылки:")
    await state.set_state(AdminSt.broadcast)

@router.message(AdminSt.broadcast)
async def do_bc(msg: Message, state: FSMContext):
    await state.clear()
    users = await db.admin_broadcast_get_users()
    sent=failed=0
    for uid in users:
        try: await bot.send_message(uid,msg.text,parse_mode="Markdown"); sent+=1; await asyncio.sleep(0.05)
        except: failed+=1
    await msg.answer(f"✅ Рассылка: {sent} отправлено, {failed} ошибок")

@router.callback_query(F.data=="adm_gb")
async def cb_gb(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID: return
    await safe_edit_or_answer(call, "💰 Введи Telegram ID:")
    await state.set_state(AdminSt.give_id)

@router.message(AdminSt.give_id)
async def do_gb_id(msg: Message, state: FSMContext):
    await state.update_data(tid=int(msg.text.strip()))
    await msg.answer("Введи сумму (₽):")
    await state.set_state(AdminSt.give_amt)

@router.message(AdminSt.give_amt)
async def do_gb_amt(msg: Message, state: FSMContext):
    data = await state.get_data(); amt = float(msg.text.strip())
    await db.admin_give_balance(data["tid"], amt); await state.clear()
    await msg.answer(f"✅ Начислено {amt} ₽ → {data['tid']}")

@router.callback_query(F.data=="adm_promo")
async def cb_promo(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID: return
    await safe_edit_or_answer(call, "🎟 Введи: КОД СУММА КОЛИЧЕСТВО\nПример: `LILIUM50 50 100`")
    await state.set_state(AdminSt.promo)

@router.message(AdminSt.promo)
async def do_promo(msg: Message, state: FSMContext):
    await state.clear()
    parts = msg.text.strip().split()
    if len(parts)<2: await msg.answer("Неверный формат"); return
    code,amt = parts[0],float(parts[1])
    uses = int(parts[2]) if len(parts)>2 else None
    await db.create_promo(code,amt,uses)
    await msg.answer(f"✅ Промокод `{code}` создан: +{amt}₽", parse_mode="Markdown")

@router.callback_query(F.data=="adm_banners")
async def cb_banners(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    b = InlineKeyboardBuilder()
    for sid, name in BANNER_SECTIONS.items():
        banner = await db.get_banner(sid)
        status = "✅" if banner else "⬜"
        b.button(text=f"{status} {name}", callback_data=f"banner_set_{sid}")
    b.button(text="🗑 Удалить баннер", callback_data="banner_del")
    b.button(text="◀️ Назад", callback_data="admin")
    b.adjust(1)
    await safe_edit_or_answer(call, "🎨 *Управление баннерами*\n\nВыбери секцию для изменения:", reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("banner_set_"))
async def cb_banner_set(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID: return
    section_id = call.data.replace("banner_set_", "")
    section_name = BANNER_SECTIONS.get(section_id, section_id)
    await state.update_data(banner_section=section_id)
    await safe_edit_or_answer(call,
        f"📤 Отправь фото, гиф или видео для секции *{section_name}*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="adm_banners")]
        ])
    )
    await state.set_state(BannerSt.waiting_media)

@router.callback_query(F.data=="banner_del")
async def cb_banner_del(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    b = InlineKeyboardBuilder()
    for sid, name in BANNER_SECTIONS.items():
        b.button(text=name, callback_data=f"banner_rm_{sid}")
    b.button(text="◀️ Назад", callback_data="adm_banners")
    b.adjust(1)
    await safe_edit_or_answer(call, "🗑 *Удаление баннера*\n\nВыбери секцию:", reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("banner_rm_"))
async def cb_banner_rm(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    section_id = call.data.replace("banner_rm_", "")
    section_name = BANNER_SECTIONS.get(section_id, section_id)
    await db.delete_banner(section_id)
    await call.answer(f"✅ Баннер для {section_name} удалён", show_alert=True)
    await cb_banners(call)

@router.message(F.photo)
async def banner_photo(msg: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state != "BannerSt:waiting_media":
        return
    data = await state.get_data()
    section_id = data.get("banner_section")
    if not section_id:
        return
    section_name = BANNER_SECTIONS.get(section_id, section_id)
    await db.set_banner(section_id, msg.photo[-1].file_id, "photo")
    await state.clear()
    await msg.answer(f"✅ Баннер для *{section_name}* установлен!", parse_mode="Markdown")

@router.message(F.video)
async def banner_video(msg: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state != "BannerSt:waiting_media":
        return
    data = await state.get_data()
    section_id = data.get("banner_section")
    if not section_id:
        return
    section_name = BANNER_SECTIONS.get(section_id, section_id)
    await db.set_banner(section_id, msg.video.file_id, "video")
    await state.clear()
    await msg.answer(f"✅ Баннер для *{section_name}* установлен!", parse_mode="Markdown")

@router.message(F.document)
async def banner_animation(msg: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state != "BannerSt:waiting_media":
        return
    data = await state.get_data()
    section_id = data.get("banner_section")
    if not section_id:
        return
    section_name = BANNER_SECTIONS.get(section_id, section_id)
    if msg.document.mime_type and msg.document.mime_type.startswith("image/gif"):
        await db.set_banner(section_id, msg.document.file_id, "animation")
        await state.clear()
        await msg.answer(f"✅ Баннер для *{section_name}* установлен!", parse_mode="Markdown")
    else:
        await msg.answer("❌ Поддерживаются только фото, видео или GIF. Отправь другое медиа.")

@router.callback_query(F.data=="back")
async def cb_back(call: CallbackQuery):
    banner = await db.get_banner("home")
    if banner:
        await call.message.answer_photo(photo=banner["file_id"], caption="Выбери действие:", reply_markup=main_kb(call.from_user.id))
    else:
        await call.message.answer("Выбери действие:", reply_markup=main_kb(call.from_user.id))

dp.include_router(router)

DEFAULT_BANNER = "AgACAgIAAxkDAAIBWGnc_nop2a-y2Ietm3j35jZT4IxtAAJjEWsbZJHoSjjT4-oCU58pAQADAgADdwADOwQ"

async def main():
    await db.init_db()
    
    # Set default home banner if not exists
    banner = await db.get_banner("home")
    if not banner:
        await db.set_banner("home", DEFAULT_BANNER, "photo")
    
    await dp.start_polling(bot, skip_updates=True)

if __name__=="__main__":
    asyncio.run(main())
