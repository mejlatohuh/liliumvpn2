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

class AdminSt(StatesGroup):
    broadcast = State()
    give_id = State()
    give_amt = State()
    promo = State()

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
    b.button(text="🌸 Открыть LiliumVPN", web_app=WebAppInfo(url=WEBAPP_URL))
    b.button(text="👤 Профиль", callback_data="profile")
    b.button(text="◈ Подписка", callback_data="subscription")
    b.button(text="💳 Купить", callback_data="buy")
    b.button(text="👥 Рефералы", callback_data="referrals")
    b.button(text="🆘 Поддержка", url="https://t.me/ProjectLilium")
    if uid in ADMIN_IDS:
        b.button(text="⚙️ Админ", callback_data="admin")
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
    if is_new:
        text += "Добро пожаловать! Активируй *бесплатный пробный период* ниже.\n"
    else:
        sub = await db.get_active_subscription(u.id)
        if sub:
            import datetime
            d = max(0,(sub["end_date"] - datetime.datetime.utcnow()).days)
            text += f"📡 Тариф: *{sub['plan'].upper()}* · Осталось: *{d} дн.*\n"
        else:
            text += "⚠️ Нет активной подписки.\n"
    text += "\nВыбери действие:"
    await msg.answer(text, parse_mode="Markdown", reply_markup=main_kb(u.id))

@router.callback_query(F.data=="check_sub")
async def cb_check_sub(call: CallbackQuery):
    if await check_sub(call.from_user.id):
        await db.set_channel_subscribed(call.from_user.id, True)
        await call.message.edit_text("✅ *Добро пожаловать в LiliumVPN!*", parse_mode="Markdown")
        await call.message.answer("Выбери действие:", reply_markup=main_kb(call.from_user.id))
    else:
        await call.answer("Ты ещё не подписался!", show_alert=True)

@router.callback_query(F.data=="profile")
async def cb_profile(call: CallbackQuery):
    u = await db.get_user(call.from_user.id)
    if not u: return
    await call.message.edit_text(
        f"👤 *Профиль*\n\n🆔 ID: `{u['telegram_id']}`\n@{u['username'] or '—'}\n🏷 Код: `{u['ref_code']}`\n💰 Баланс: *{u['balance']} ₽*",
        parse_mode="Markdown",
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
    await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data=="buy")
async def cb_buy(call: CallbackQuery):
    b = InlineKeyboardBuilder()
    for k,p in PLANS.items():
        if k=="trial": continue
        b.button(text=f"{p['name']} — {p['price_rub']}₽ / {p['price_stars']}⭐", callback_data=f"plan_{k}")
    b.button(text="🎁 Пробный (бесплатно)", callback_data="plan_trial")
    b.button(text="◀️ Назад", callback_data="back")
    b.adjust(1)
    await call.message.edit_text("💳 *Выбери тариф:*", parse_mode="Markdown", reply_markup=b.as_markup())

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
        await call.message.edit_text(
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
    await call.message.edit_text(
        f"📦 *{plan['name']}*\n\n📊 {traf}/мес · 🖥 {plan['devices']} уст. · 📅 {plan['days']} дней\n\nСпособ оплаты:",
        parse_mode="Markdown", reply_markup=b.as_markup()
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
    await call.message.edit_text(f"✅ *{plan['name']}* активирован! Списано *{plan['price_rub']} ₽*.", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🌸 Открыть кабинет", web_app=WebAppInfo(url=WEBAPP_URL))]]))

@router.callback_query(F.data.startswith("pay_crypto_"))
async def cb_pay_crypto(call: CallbackQuery):
    key = call.data.replace("pay_crypto_",""); plan = PLANS.get(key)
    usdt = round(plan["price_rub"]/90, 2)
    await call.message.edit_text(
        f"🪙 *Оплата криптой*\n\n{plan['name']} · ~{usdt} USDT\n\nОтправь платёж через @CryptoBot и напиши в поддержку с чеком.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💸 CryptoBot",url="https://t.me/CryptoBot")],
            [InlineKeyboardButton(text="📩 Поддержка",url="https://t.me/ProjectLilium")],
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
    await call.message.edit_text(
        f"👥 *Рефералы*\n\n🏷 Твой код: `{code}`\n👫 Всего: *{stats.get('total',0)}*\n💰 Заработок: *+{stats.get('earned',0):.2f} ₽*\n\n📎 Ссылка:\n`{link}`\n\n_25% с каждой оплаты реферала_",
        parse_mode="Markdown",
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
    b.button(text="◀️ Назад",callback_data="back")
    b.adjust(1)
    await call.message.edit_text(text, parse_mode="Markdown", reply_markup=b.as_markup())

@router.callback_query(F.data=="adm_users")
async def cb_adm_users(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: return
    users = await db.get_all_users_paginated(0,20)
    lines = [f"• @{u['username'] or '—'} `{u['telegram_id']}` [{u['role']}]" for u in users[:20]]
    await call.message.edit_text("👥 *Пользователи (20 последних):*\n\n"+"\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад",callback_data="admin")]]))

@router.callback_query(F.data=="adm_refs")
async def cb_adm_refs(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: return
    stats = await db.get_referral_stats(call.from_user.id)
    refs = stats.get("referrals",[])
    lines = [f"└ @{r['username'] or r['first_name'] or '—'} · `{r['ref_code']}` · {'✅' if r['has_sub'] else '❌'}" for r in refs[:15]]
    text = f"🌳 *Твои рефералы* ({stats.get('total',0)}):\n\n" + ("\n".join(lines) if lines else "Пока нет")
    await call.message.edit_text(text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад",callback_data="admin")]]))

@router.callback_query(F.data=="adm_bc")
async def cb_bc(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID: return
    await call.message.edit_text("📢 Введи текст рассылки:")
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
    await call.message.edit_text("💰 Введи Telegram ID:")
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
    await call.message.edit_text("🎟 Введи: КОД СУММА КОЛИЧЕСТВО\nПример: `LILIUM50 50 100`", parse_mode="Markdown")
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

@router.callback_query(F.data=="back")
async def cb_back(call: CallbackQuery):
    await call.message.edit_text("Выбери действие:", reply_markup=main_kb(call.from_user.id))

dp.include_router(router)

async def main():
    await db.init_db()
    await dp.start_polling(bot, skip_updates=True)

if __name__=="__main__":
    asyncio.run(main())
