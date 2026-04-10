from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
import hmac, hashlib, json, asyncio
from urllib.parse import unquote
from pydantic import BaseModel
from typing import Optional

from config import BOT_TOKEN, PLANS, ADMIN_IDS, OWNER_ID, WEBAPP_URL
import database as db
from bot import bot, dp

app = FastAPI(title="LiliumVPN API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
async def startup():
    await db.init_db()
    asyncio.create_task(dp.start_polling(bot, skip_updates=True))

@app.post("/webhook")
async def telegram_webhook(request: Request):
    from aiogram.types import Update
    data = await request.json()
    update = Update(**data)
    await dp.feed_update(bot, update)
    return {"ok": True}

def verify_initdata(init_data: str) -> dict:
    if not init_data:
        raise HTTPException(status_code=401, detail="No initData")
    try:
        parsed = {}
        for part in init_data.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                parsed[k] = unquote(v)
        received_hash = parsed.pop("hash", "")
        if not received_hash:
            raise ValueError("No hash")
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received_hash):
            raise ValueError("Hash mismatch")
        return json.loads(parsed.get("user", "{}"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Auth: {e}")

async def get_current_user(request: Request) -> dict:
    tg_user = verify_initdata(request.headers.get("X-Telegram-Init-Data",""))
    user = await db.get_user(tg_user["id"])
    if not user:
        user, _ = await db.get_or_create_user(tg_id=tg_user["id"], username=tg_user.get("username"), first_name=tg_user.get("first_name"))
    return user

async def require_admin(user=Depends(get_current_user)):
    if user["telegram_id"] not in ADMIN_IDS: raise HTTPException(403,"Admin only")
    return user

async def require_owner(user=Depends(get_current_user)):
    if user["telegram_id"] != OWNER_ID: raise HTTPException(403,"Owner only")
    return user

def _fmt_sub(sub):
    if not sub: return None
    p = PLANS.get(sub["plan"], {})
    return {"plan":sub["plan"],"plan_name":p.get("name",sub["plan"]),"end_date":sub["end_date"].isoformat() if sub.get("end_date") else None,"traffic_limit_mb":sub.get("traffic_limit_mb",0),"traffic_used_mb":sub.get("traffic_used_mb",0),"devices":sub.get("devices",1),"active":bool(sub.get("active")),"vpn_key":sub.get("vpn_key")}

@app.get("/api/me")
async def get_me(user=Depends(get_current_user)):
    sub = await db.get_active_subscription(user["telegram_id"])
    ref = await db.get_referral_stats(user["telegram_id"])
    return {"user":{"telegram_id":user["telegram_id"],"username":user["username"],"first_name":user["first_name"],"ref_code":user["ref_code"],"balance":float(user["balance"] or 0),"role":user["role"],"created_at":user["created_at"].isoformat() if user.get("created_at") else None},"subscription":_fmt_sub(sub),"referrals":{"total":ref.get("total",0),"earned":float(ref.get("earned",0)),"ref_code":ref.get("ref_code","")},"is_admin":user["telegram_id"] in ADMIN_IDS,"is_owner":user["telegram_id"]==OWNER_ID}

@app.get("/api/balance")
async def get_balance(user=Depends(get_current_user)):
    payments = await db.get_user_payments(user["telegram_id"])
    return {"balance":float(user["balance"] or 0),"payments":[{"id":p["id"],"amount":float(p["amount"] or 0),"method":p["method"],"plan":p["plan"],"status":p["status"],"created_at":p["created_at"].isoformat() if p.get("created_at") else None} for p in payments]}

@app.get("/api/referrals")
async def get_referrals(user=Depends(get_current_user)):
    stats = await db.get_referral_stats(user["telegram_id"])
    return {**stats, "commission_percent":25, "bonus_new":50, "bonus_inviter":50, "min_withdrawal":200}

class PromoReq(BaseModel):
    code: str

@app.post("/api/promo/apply")
async def apply_promo(body: PromoReq, user=Depends(get_current_user)):
    promo, error = await db.apply_promo(user["telegram_id"], body.code)
    if error: raise HTTPException(400, error)
    return {"ok":True,"discount":float(promo["discount_rub"]),"message":f"✓ промокод применён! +{promo['discount_rub']} ₽"}

class PayBalanceReq(BaseModel):
    plan: str

@app.post("/api/pay/balance")
async def pay_with_balance(body: PayBalanceReq, user=Depends(get_current_user)):
    plan = PLANS.get(body.plan)
    if not plan: raise HTTPException(400,"Unknown plan")
    price = plan["price_rub"]
    balance = float(user["balance"] or 0)
    if balance < price: raise HTTPException(400, f"Недостаточно средств: нужно {price} ₽, баланс {balance:.2f} ₽")
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET balance=balance-$1 WHERE telegram_id=$2", price, user["telegram_id"])
    payment = await db.create_payment(user["telegram_id"], price, "balance", body.plan)
    await db.confirm_payment(payment["id"])
    await db.create_subscription(user["telegram_id"], body.plan)
    await db.process_referral_reward(user["telegram_id"], price, "balance")
    return {"ok":True,"message":f"✓ {plan['name']} активирована"}

@app.get("/api/admin/stats")
async def admin_stats(user=Depends(require_admin)):
    return await db.get_admin_stats()

@app.get("/api/admin/users")
async def admin_users(offset:int=0, limit:int=50, user=Depends(require_admin)):
    users = await db.get_all_users_paginated(offset, limit)
    return {"users":[{k:(v.isoformat() if hasattr(v,"isoformat") else v) for k,v in u.items()} for u in users]}

class GiveBalReq(BaseModel):
    target_id: int
    amount: float

@app.post("/api/admin/give-balance")
async def give_balance(body: GiveBalReq, user=Depends(require_owner)):
    await db.admin_give_balance(body.target_id, body.amount)
    return {"ok":True}

class ActivateSubReq(BaseModel):
    target_id: int
    plan: str

@app.post("/api/admin/activate-sub")
async def activate_sub(body: ActivateSubReq, user=Depends(require_owner)):
    if body.plan not in PLANS: raise HTTPException(400,"Unknown plan")
    await db.create_subscription(body.target_id, body.plan)
    return {"ok":True}

class BroadcastReq(BaseModel):
    message: str

@app.post("/api/admin/broadcast")
async def broadcast(body: BroadcastReq, user=Depends(require_owner)):
    users = await db.admin_broadcast_get_users()
    sent,failed = 0,0
    for uid in users:
        try:
            await bot.send_message(uid, body.message, parse_mode="Markdown")
            sent+=1; await asyncio.sleep(0.05)
        except: failed+=1
    return {"ok":True,"sent":sent,"failed":failed}

class CreatePromoReq(BaseModel):
    code: str
    discount_rub: float
    uses: Optional[int] = None

@app.post("/api/admin/promo")
async def create_promo(body: CreatePromoReq, user=Depends(require_owner)):
    await db.create_promo(body.code, body.discount_rub, body.uses)
    return {"ok":True}

@app.get("/api/admin/ref-tree/{admin_id}")
async def admin_ref_tree(admin_id: int, user=Depends(require_admin)):
    return await db.get_referral_stats(admin_id)

@app.get("/health")
async def health():
    return {"status":"ok"}
