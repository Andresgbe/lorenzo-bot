"""
Bot de la Comunidad Lorenzo Broker (@LorenzoBrokerBot)

- Cobra la membresía en USDT con Crypto Pay (@CryptoBot)
- Da acceso al canal privado con un link de un solo uso
- Registra a cada miembro con su fecha de vencimiento
- Recuerda la renovación, da 3 días de gracia y saca a quien no renueva
- Saca del canal a quien entre sin membresía activa
- Comandos de administrador: /admin para ver la lista

La configuración (tokens, IDs) va en variables de entorno. Ver GUIA.md.
"""

import csv
import html
import io
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# En local lee el archivo .env; en Railway no hay .env y usa sus Variables.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# ----------------------------------------------------------------------------
# Configuración
# ----------------------------------------------------------------------------

BOT_TOKEN = os.environ["BOT_TOKEN"]
CRYPTOPAY_TOKEN = os.environ["CRYPTOPAY_TOKEN"]
CRYPTOPAY_TESTNET = os.getenv("CRYPTOPAY_TESTNET", "false").strip().lower() == "true"
CHANNEL_ID = int(os.getenv("CHANNEL_ID") or os.getenv("GROUP_ID") or 0)
ADMIN_IDS = {
    int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x
}
DB_PATH = os.getenv("DB_PATH", "bot.db")

TZ = ZoneInfo("America/Caracas")
CRYPTOPAY_API = (
    "https://testnet-pay.crypt.bot/api" if CRYPTOPAY_TESTNET else "https://pay.crypt.bot/api"
)

PLANS = {
    "mensual": {"name": "Mensual", "amount": "27.77", "days": 30},
    "anual": {"name": "Anual", "amount": "297", "days": 365},
}
ASSET = "USDT"
GRACE_DAYS = 3          # días después del vencimiento antes de sacar al miembro
REMIND_DAYS = 3         # días antes del vencimiento para recordar la renovación
INVOICE_EXPIRES = 3600  # la factura vence en 1 hora (segundos)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("lorenzo-bot")

# ----------------------------------------------------------------------------
# Textos
# ----------------------------------------------------------------------------

TEXT_START = (
    "👋 ¡Hola, {name}! Soy el bot de la <b>Comunidad Lorenzo Broker</b>.\n\n"
    "Con tu membresía entras al canal privado, donde vas a recibir:\n"
    "📈 Ideas de inversión\n"
    "📊 Reportes de mercado\n"
    "🔴 Eventos en vivo, con su link para conectarte\n\n"
    "<b>Elige tu plan</b> (pago en USDT con Crypto Pay):\n"
    "• Mensual: <b>27.77 USDT</b>\n"
    "• Anual: <b>297 USDT</b> (ahorras ~36 USDT frente a 12 meses)"
)

TEXT_WELCOME = (
    "🎉 <b>¡Bienvenido a la Comunidad Lorenzo Broker!</b>\n\n"
    "Ya eres parte del canal privado. Aquí vas a recibir:\n\n"
    "📈 Ideas de inversión\n"
    "📊 Reportes de mercado\n"
    "🔴 Eventos en vivo, con su link para conectarte\n"
    "📌 Toda la información importante de la comunidad\n\n"
    "🔔 <b>Importante:</b> activa las notificaciones de Telegram para este canal "
    "y no te pierdas ninguna idea ni ningún evento en vivo.\n\n"
    "✅ Tu membresía está activa hasta el <b>{fecha}</b>.\n\n"
    "Nos vemos en el mercado.\n"
    "— Lorenzo Broker"
)

TEXT_RENEWED = (
    "✅ <b>¡Renovación confirmada!</b>\n\n"
    "Tu membresía en la Comunidad Lorenzo Broker ahora está activa hasta el "
    "<b>{fecha}</b>.\n\nGracias por seguir con nosotros. Nos vemos en el mercado."
)

TEXT_REMINDER = (
    "⏰ Tu membresía en la <b>Comunidad Lorenzo Broker</b> vence el <b>{fecha}</b>.\n\n"
    "Renueva ahora para no perder el acceso al canal. Los días que te quedan se suman "
    "a tu nueva fecha."
)

TEXT_GRACE = (
    "⚠️ Tu membresía en la <b>Comunidad Lorenzo Broker</b> venció el <b>{fecha}</b>.\n\n"
    "Tienes <b>{dias} días</b> para renovar antes de salir del canal."
)

TEXT_REMOVED = (
    "Tu membresía en la <b>Comunidad Lorenzo Broker</b> venció y saliste del canal.\n\n"
    "Cuando quieras volver, renueva aquí mismo y te envío un nuevo acceso. "
    "¡Te esperamos!"
)

TEXT_NO_ACCESS = (
    "Entraste al canal de la <b>Comunidad Lorenzo Broker</b> sin una membresía activa, "
    "así que tuve que sacarte.\n\nElige un plan aquí y te envío tu acceso personal al momento:"
)

# ----------------------------------------------------------------------------
# Base de datos (SQLite)
# ----------------------------------------------------------------------------

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript(
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id    INTEGER PRIMARY KEY,
        username   TEXT,
        full_name  TEXT,
        origin     TEXT,
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS members (
        user_id        INTEGER PRIMARY KEY,
        plan           TEXT,
        paid_at        TEXT,
        expires_at     TEXT,
        status         TEXT,      -- activo / gracia / vencido
        reminded       INTEGER DEFAULT 0,
        grace_notified INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS payments (
        ref        TEXT PRIMARY KEY,   -- cp:<invoice_id> o manual:<...>
        invoice_id INTEGER,
        user_id    INTEGER,
        plan       TEXT,
        amount     TEXT,
        method     TEXT,               -- cryptopay / manual
        status     TEXT,               -- pendiente / pagado / expirado
        created_at TEXT,
        paid_at    TEXT
    );
    """
)
# Migración: guardar el link de pago para reutilizar la factura pendiente
if "pay_url" not in {r["name"] for r in db.execute("PRAGMA table_info(payments)")}:
    db.execute("ALTER TABLE payments ADD COLUMN pay_url TEXT")
db.commit()


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat()


def parse(s: str) -> datetime:
    return datetime.fromisoformat(s)


def fecha(d: datetime) -> str:
    return d.astimezone(TZ).strftime("%d/%m/%Y")


def get_member(user_id: int):
    return db.execute("SELECT * FROM members WHERE user_id=?", (user_id,)).fetchone()


def get_user(user_id: int):
    return db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def save_user(tg_user, origin: str | None = None):
    existing = get_user(tg_user.id)
    if existing:
        db.execute(
            "UPDATE users SET username=?, full_name=?, origin=COALESCE(origin, ?) WHERE user_id=?",
            (tg_user.username, tg_user.full_name, origin, tg_user.id),
        )
    else:
        db.execute(
            "INSERT INTO users (user_id, username, full_name, origin, created_at) VALUES (?,?,?,?,?)",
            (tg_user.id, tg_user.username, tg_user.full_name, origin or "directo", iso(now())),
        )
    db.commit()


def user_label(user_id: int) -> str:
    u = get_user(user_id)
    if not u:
        return str(user_id)
    name = html.escape(u["full_name"] or "")
    uname = f" @{u['username']}" if u["username"] else ""
    return f"{name}{uname} ({user_id})"


def plans_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Mensual · 27.77 USDT", callback_data="plan:mensual")],
            [InlineKeyboardButton("Anual · 297 USDT", callback_data="plan:anual")],
        ]
    )


# ----------------------------------------------------------------------------
# Crypto Pay
# ----------------------------------------------------------------------------

async def cryptopay(method: str, params: dict) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{CRYPTOPAY_API}/{method}",
            headers={"Crypto-Pay-API-Token": CRYPTOPAY_TOKEN},
            json=params,
        )
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Crypto Pay {method} error: {data}")
    return data["result"]


# ----------------------------------------------------------------------------
# Utilidades de Telegram
# ----------------------------------------------------------------------------

async def notify_admins(bot, text: str):
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except TelegramError as e:
            log.warning("No pude avisar al admin %s: %s", admin_id, e)


async def is_in_group(bot, user_id: int) -> bool:
    try:
        cm = await bot.get_chat_member(CHANNEL_ID, user_id)
        return cm.status in ("member", "administrator", "creator", "restricted")
    except TelegramError:
        return False


async def is_group_admin(bot, user_id: int) -> bool:
    try:
        cm = await bot.get_chat_member(CHANNEL_ID, user_id)
        return cm.status in ("administrator", "creator")
    except TelegramError:
        return False


async def activate(bot, user_id: int, plan: str, source: str):
    """Activa o renueva la membresía y entrega el acceso al canal."""
    p = PLANS[plan]
    member = get_member(user_id)
    base = now()
    if member and member["expires_at"] and parse(member["expires_at"]) > base:
        base = parse(member["expires_at"])  # renovó antes de vencer: se suman los días
    new_exp = base + timedelta(days=p["days"])

    db.execute(
        """INSERT INTO members (user_id, plan, paid_at, expires_at, status, reminded, grace_notified)
           VALUES (?,?,?,?, 'activo', 0, 0)
           ON CONFLICT(user_id) DO UPDATE SET
             plan=excluded.plan, paid_at=excluded.paid_at, expires_at=excluded.expires_at,
             status='activo', reminded=0, grace_notified=0""",
        (user_id, plan, iso(now()), iso(new_exp)),
    )
    db.commit()

    if not CHANNEL_ID:
        await notify_admins(
            bot, f"⚠️ Falta configurar CHANNEL_ID. No pude dar acceso a {user_label(user_id)}."
        )
        return

    # Por si había sido sacado antes por falta de pago
    try:
        await bot.unban_chat_member(CHANNEL_ID, user_id, only_if_banned=True)
    except TelegramError as e:
        log.warning("unban %s: %s", user_id, e)

    link_text = ""
    if await is_in_group(bot, user_id):
        msg = TEXT_RENEWED.format(fecha=fecha(new_exp))
        markup = None
    else:
        msg = TEXT_WELCOME.format(fecha=fecha(new_exp))
        try:
            invite = await bot.create_chat_invite_link(
                CHANNEL_ID,
                member_limit=1,
                expire_date=now() + timedelta(days=1),
                name=f"pago {user_id}"[:32],
            )
            markup = InlineKeyboardMarkup(
                [[InlineKeyboardButton("👉 Entrar al canal", url=invite.invite_link)]]
            )
            link_text = f"\nLink de acceso: {invite.invite_link}"
        except TelegramError as e:
            # La membresía ya quedó registrada: no usar /aprobar (sumaría días otra vez)
            log.error("No pude crear el link para %s: %s", user_id, e)
            markup = None
            msg += "\n\nEn unos minutos escribe /acceso para recibir tu link al canal."
            link_text = (
                f"\n⚠️ No pude crear el link ({html.escape(str(e))}). Revisa que el bot siga "
                "siendo admin del canal y pídele que use /acceso. No uses /aprobar."
            )

    try:
        await bot.send_message(user_id, msg, parse_mode="HTML", reply_markup=markup)
        delivered = "✅ Acceso enviado." if markup or not link_text else link_text
    except TelegramError:
        delivered = (
            "⚠️ No pude escribirle (no ha iniciado el bot o lo bloqueó). "
            f"Envíale tú el acceso.{link_text}"
        )

    await notify_admins(
        bot,
        f"💰 <b>Pago confirmado</b> ({source})\n"
        f"Miembro: {user_label(user_id)}\n"
        f"Plan: {p['name']} · {p['amount']} {ASSET}\n"
        f"Vence: {fecha(new_exp)}\n{delivered}",
    )


async def remove_from_group(bot, user_id: int):
    """Saca al miembro del canal pero le permite volver a entrar si paga."""
    await bot.ban_chat_member(CHANNEL_ID, user_id)
    await bot.unban_chat_member(CHANNEL_ID, user_id, only_if_banned=True)


# ----------------------------------------------------------------------------
# Comandos de usuarios
# ----------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    user = update.effective_user
    origin = context.args[0][:30] if context.args else None
    save_user(user, origin)

    text = TEXT_START.format(name=html.escape(user.first_name or ""))
    member = get_member(user.id)
    if member and member["status"] == "activo":
        text += (
            f"\n\n✅ Tu membresía está activa hasta el <b>{fecha(parse(member['expires_at']))}</b>. "
            "Si renuevas ahora, los días se suman."
        )
    elif member and member["status"] == "gracia":
        exp = parse(member["expires_at"])
        text += (
            f"\n\n⚠️ Tu membresía venció el <b>{fecha(exp)}</b>. Renueva antes del "
            f"<b>{fecha(exp + timedelta(days=GRACE_DAYS))}</b> para no salir del canal."
        )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=plans_keyboard())


async def cmd_estado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    member = get_member(update.effective_user.id)
    if not member or member["status"] == "vencido":
        await update.message.reply_text(
            "No tienes una membresía activa. Elige un plan para unirte:",
            reply_markup=plans_keyboard(),
        )
        return
    await update.message.reply_text(
        f"Tu membresía ({PLANS[member['plan']]['name']}) vence el "
        f"<b>{fecha(parse(member['expires_at']))}</b>.",
        parse_mode="HTML",
        reply_markup=plans_keyboard(),
    )


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el ID del chat y del usuario (sirve para la configuración)."""
    await update.message.reply_text(
        f"ID de este chat: <code>{update.effective_chat.id}</code>\n"
        f"Tu ID de usuario: <code>{update.effective_user.id}</code>",
        parse_mode="HTML",
    )


async def on_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    plan = query.data.split(":", 1)[1]
    if plan not in PLANS:
        return
    user = query.from_user
    save_user(user)
    p = PLANS[plan]

    # Si ya tiene una factura pendiente de este plan con más de 10 min de vida, se reutiliza
    reusable_since = iso(now() - timedelta(seconds=INVOICE_EXPIRES - 600))
    pending = db.execute(
        "SELECT * FROM payments WHERE user_id=? AND plan=? AND method='cryptopay' "
        "AND status='pendiente' AND pay_url IS NOT NULL AND created_at>? "
        "ORDER BY created_at DESC LIMIT 1",
        (user.id, plan, reusable_since),
    ).fetchone()
    if pending:
        await send_pay_button(query, p, pending["pay_url"])
        return

    try:
        invoice = await cryptopay(
            "createInvoice",
            {
                "currency_type": "crypto",
                "asset": ASSET,
                "amount": p["amount"],
                "description": f"Comunidad Lorenzo Broker · Membresía {p['name']}",
                "payload": f"{user.id}:{plan}",
                "expires_in": INVOICE_EXPIRES,
                "allow_comments": False,
            },
        )
    except Exception as e:  # noqa: BLE001
        log.error("Error creando factura: %s", e)
        await query.message.reply_text(
            "Hubo un problema generando tu pago. Intenta de nuevo en unos minutos."
        )
        await notify_admins(context.bot, f"⚠️ Error creando factura de Crypto Pay: {html.escape(str(e))}")
        return

    invoice_id = invoice["invoice_id"]
    pay_url = invoice.get("bot_invoice_url") or invoice.get("pay_url")
    db.execute(
        """INSERT INTO payments (ref, invoice_id, user_id, plan, amount, method, status, created_at, pay_url)
           VALUES (?,?,?,?,?, 'cryptopay', 'pendiente', ?, ?)""",
        (f"cp:{invoice_id}", invoice_id, user.id, plan, p["amount"], iso(now()), pay_url),
    )
    db.commit()
    await send_pay_button(query, p, pay_url)


async def send_pay_button(query, p: dict, pay_url: str):
    await query.message.reply_text(
        f"🧾 <b>Membresía {p['name']}: {p['amount']} {ASSET}</b>\n\n"
        "Toca el botón para pagar con Crypto Pay. Cuando el pago se confirme, "
        "te envío tu acceso aquí mismo (puede tardar hasta un minuto).\n\n"
        "La factura vence 1 hora después de creada.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(f"💳 Pagar {p['amount']} {ASSET}", url=pay_url)]]
        ),
    )


# ----------------------------------------------------------------------------
# Tareas automáticas
# ----------------------------------------------------------------------------

async def job_check_payments(context: ContextTypes.DEFAULT_TYPE):
    """Cada 20 segundos revisa en Crypto Pay si se pagaron las facturas pendientes."""
    rows = db.execute(
        "SELECT * FROM payments WHERE method='cryptopay' AND status='pendiente' "
        "ORDER BY created_at"
    ).fetchall()
    # Se revisan todas las pendientes, de 100 en 100 (límite de getInvoices)
    for i in range(0, len(rows), 100):
        await check_invoice_chunk(context, rows[i : i + 100])


async def check_invoice_chunk(context: ContextTypes.DEFAULT_TYPE, rows):
    ids = ",".join(str(r["invoice_id"]) for r in rows)
    try:
        result = await cryptopay("getInvoices", {"invoice_ids": ids, "count": 100})
    except Exception as e:  # noqa: BLE001
        log.error("Error consultando facturas: %s", e)
        return

    by_id = {r["invoice_id"]: r for r in rows}
    seen = set()
    for inv in result.get("items", []):
        row = by_id.get(inv["invoice_id"])
        if not row:
            continue
        seen.add(inv["invoice_id"])
        if inv["status"] == "paid":
            cur = db.execute(
                "UPDATE payments SET status='pagado', paid_at=? WHERE ref=? AND status='pendiente'",
                (iso(now()), row["ref"]),
            )
            db.commit()
            if cur.rowcount:  # evita activar dos veces el mismo pago
                try:
                    await activate(context.bot, row["user_id"], row["plan"], "Crypto Pay")
                except Exception as e:  # noqa: BLE001
                    log.error("Error activando %s: %s", row["user_id"], e)
                    await notify_admins(
                        context.bot,
                        f"⚠️ Pago recibido de {user_label(row['user_id'])} pero falló el acceso: "
                        f"{html.escape(str(e))}. Revisa con /buscar. Si su membresía ya aparece "
                        "activa, pídele que use /acceso (no uses /aprobar: sumaría días otra vez).",
                    )
        elif inv["status"] == "expired":
            db.execute("UPDATE payments SET status='expirado' WHERE ref=?", (row["ref"],))
            db.commit()

    # Facturas que Crypto Pay ya no devuelve (borradas) y llevan mucho tiempo pendientes
    stale_before = now() - timedelta(seconds=INVOICE_EXPIRES * 2)
    for invoice_id, row in by_id.items():
        if invoice_id not in seen and parse(row["created_at"]) < stale_before:
            db.execute("UPDATE payments SET status='expirado' WHERE ref=?", (row["ref"],))
    db.commit()


async def job_check_expirations(context: ContextTypes.DEFAULT_TYPE):
    """Cada hora: recordatorios, periodo de gracia y salida del canal."""
    bot = context.bot
    t = now()
    members = db.execute(
        "SELECT * FROM members WHERE status IN ('activo','gracia')"
    ).fetchall()

    for m in members:
        uid = m["user_id"]
        exp = parse(m["expires_at"])

        # 1) Recordatorio antes de vencer
        if m["status"] == "activo" and not m["reminded"] and exp - timedelta(days=REMIND_DAYS) <= t < exp:
            try:
                await bot.send_message(
                    uid, TEXT_REMINDER.format(fecha=fecha(exp)),
                    parse_mode="HTML", reply_markup=plans_keyboard(),
                )
            except TelegramError:
                pass
            db.execute("UPDATE members SET reminded=1 WHERE user_id=?", (uid,))
            db.commit()

        # 2) Venció: entra en periodo de gracia
        if t >= exp and m["status"] == "activo":
            db.execute(
                "UPDATE members SET status='gracia', grace_notified=1 WHERE user_id=?", (uid,)
            )
            db.commit()
            try:
                await bot.send_message(
                    uid, TEXT_GRACE.format(fecha=fecha(exp), dias=GRACE_DAYS),
                    parse_mode="HTML", reply_markup=plans_keyboard(),
                )
            except TelegramError:
                pass
            await notify_admins(bot, f"⏳ Venció la membresía de {user_label(uid)} (en gracia).")

        # 3) Terminó la gracia: sale del canal
        if t >= exp + timedelta(days=GRACE_DAYS):
            if CHANNEL_ID and not await is_group_admin(bot, uid):
                try:
                    await remove_from_group(bot, uid)
                except TelegramError as e:
                    # No se marca como vencido: se reintenta en la próxima revisión (1 h)
                    log.warning("No pude sacar a %s: %s", uid, e)
                    await notify_admins(
                        bot,
                        f"⚠️ No pude sacar del canal a {user_label(uid)}: {html.escape(str(e))}\n"
                        "Revisa que el bot siga siendo admin con permiso para sacar miembros. "
                        "Lo vuelvo a intentar en 1 hora.",
                    )
                    continue
            db.execute("UPDATE members SET status='vencido' WHERE user_id=?", (uid,))
            db.commit()
            try:
                await bot.send_message(
                    uid, TEXT_REMOVED, parse_mode="HTML", reply_markup=plans_keyboard()
                )
            except TelegramError:
                pass
            await notify_admins(bot, f"🚪 Salió del canal por falta de pago: {user_label(uid)}")



async def send_invite(bot, user_id: int, text: str):
    invite = await bot.create_chat_invite_link(
        CHANNEL_ID, member_limit=1, expire_date=now() + timedelta(days=1),
        name=f"acceso {user_id}"[:32],
    )
    await bot.send_message(
        user_id, text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("👉 Entrar al canal", url=invite.invite_link)]]
        ),
    )


async def cmd_acceso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """El miembro pide un link nuevo (por ejemplo, si el anterior venció)."""
    if update.effective_chat.type != "private":
        return
    uid = update.effective_user.id
    member = get_member(uid)
    if not member or member["status"] == "vencido":
        await update.message.reply_text(
            "Tu membresía no está activa. Elige un plan y te envío el acceso de inmediato:",
            reply_markup=plans_keyboard(),
        )
        return
    if await is_in_group(context.bot, uid):
        await update.message.reply_text("Ya estás dentro del canal. ✅")
        return
    await send_invite(
        context.bot, uid,
        "Este es tu acceso personal al canal (un solo uso, vence en 24 h):",
    )


async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mientras falte CHANNEL_ID, avisa al admin el ID del canal cuando se publica algo."""
    if CHANNEL_ID:
        return
    chat = update.effective_chat
    log.info("Canal detectado: %s (%s)", chat.title, chat.id)
    await notify_admins(
        context.bot,
        f"📢 ID del canal «{html.escape(chat.title or '')}»: <code>{chat.id}</code>\n"
        "Ponlo en la variable CHANNEL_ID del servidor.",
    )


async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Si alguien entra al canal sin membresía activa (link ajeno, etc.), lo saca."""
    cmu = update.chat_member
    if not cmu or not CHANNEL_ID or cmu.chat.id != CHANNEL_ID:
        return
    old, new = cmu.old_chat_member.status, cmu.new_chat_member.status
    if not (new in ("member", "restricted") and old in ("left", "kicked")):
        return
    user = cmu.new_chat_member.user
    if user.is_bot or user.id in ADMIN_IDS:
        return
    member = get_member(user.id)
    if member and member["status"] in ("activo", "gracia"):
        log.info("Entró al canal: %s", user.id)
        return
    save_user(user)
    try:
        await remove_from_group(context.bot, user.id)
    except TelegramError as e:
        log.warning("No pude sacar a %s: %s", user.id, e)
    try:
        await context.bot.send_message(
            user.id, TEXT_NO_ACCESS, parse_mode="HTML", reply_markup=plans_keyboard()
        )
    except TelegramError:
        pass
    await notify_admins(
        context.bot,
        f"🚫 Saqué del canal a {user_label(user.id)}: entró sin membresía activa.\n"
        "Si pagó por otra vía, usa /aprobar.",
    )

# ----------------------------------------------------------------------------
# Comandos de administrador
# ----------------------------------------------------------------------------

def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_IDS:
            return
        return await func(update, context)
    return wrapper


def find_user_id(arg: str) -> int | None:
    arg = arg.strip()
    if arg.lstrip("-").isdigit():
        return int(arg)
    row = db.execute(
        "SELECT user_id FROM users WHERE lower(username)=lower(?)", (arg.lstrip("@"),)
    ).fetchone()
    return row["user_id"] if row else None


@admin_only
async def cmd_miembros(update: Update, context: ContextTypes.DEFAULT_TYPE):
    counts = {
        r["status"]: r["n"]
        for r in db.execute("SELECT status, COUNT(*) n FROM members GROUP BY status")
    }
    plans = {
        r["plan"]: r["n"]
        for r in db.execute(
            "SELECT plan, COUNT(*) n FROM members WHERE status='activo' GROUP BY plan"
        )
    }
    await update.message.reply_text(
        "👥 <b>Miembros</b>\n"
        f"Activos: <b>{counts.get('activo', 0)}</b> "
        f"(mensual {plans.get('mensual', 0)} · anual {plans.get('anual', 0)})\n"
        f"En gracia: {counts.get('gracia', 0)}\n"
        f"Vencidos: {counts.get('vencido', 0)}",
        parse_mode="HTML",
    )


@admin_only
async def cmd_vencen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    limit = iso(now() + timedelta(days=7))
    rows = db.execute(
        "SELECT * FROM members WHERE status IN ('activo','gracia') AND expires_at<=? "
        "ORDER BY expires_at",
        (limit,),
    ).fetchall()
    if not rows:
        await update.message.reply_text("Nadie vence en los próximos 7 días. 👌")
        return
    lines = [
        f"• {user_label(r['user_id'])} · {fecha(parse(r['expires_at']))}"
        + (" (en gracia)" if r["status"] == "gracia" else "")
        for r in rows
    ]
    await update.message.reply_text(
        "📅 <b>Vencen en los próximos 7 días</b>\n" + "\n".join(lines), parse_mode="HTML"
    )


@admin_only
async def cmd_buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /buscar @usuario  o  /buscar 123456789")
        return
    uid = find_user_id(context.args[0])
    if not uid:
        await update.message.reply_text("No encontré a esa persona (debe haber iniciado el bot).")
        return
    u = get_user(uid)
    m = get_member(uid)
    text = f"👤 {user_label(uid)}\nOrigen: {u['origin'] if u else '-'}\n"
    if m:
        text += (
            f"Plan: {PLANS[m['plan']]['name']}\nEstado: <b>{m['status']}</b>\n"
            f"Último pago: {fecha(parse(m['paid_at']))}\nVence: {fecha(parse(m['expires_at']))}"
        )
    else:
        text += "Sin membresía."
    await update.message.reply_text(text, parse_mode="HTML")


@admin_only
async def cmd_aprobar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2 or context.args[1].lower() not in PLANS:
        await update.message.reply_text(
            "Uso: /aprobar @usuario mensual\n     /aprobar 123456789 anual\n\n"
            "La persona debe haber iniciado el bot antes (/start)."
        )
        return
    uid = find_user_id(context.args[0])
    if not uid:
        await update.message.reply_text(
            "No encontré a esa persona. Pídele que le escriba /start al bot y vuelve a intentarlo."
        )
        return
    plan = context.args[1].lower()
    db.execute(
        """INSERT INTO payments (ref, user_id, plan, amount, method, status, created_at, paid_at)
           VALUES (?,?,?,?, 'manual', 'pagado', ?, ?)""",
        (f"manual:{uid}:{now().timestamp()}", uid, plan, PLANS[plan]["amount"], iso(now()), iso(now())),
    )
    db.commit()
    await activate(context.bot, uid, plan, f"aprobado por {update.effective_user.first_name}")


@admin_only
async def cmd_exportar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.execute(
        """SELECT m.user_id, u.full_name, u.username, u.origin, m.plan, m.status,
                  m.paid_at, m.expires_at
           FROM members m LEFT JOIN users u ON u.user_id = m.user_id
           ORDER BY m.expires_at"""
    ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ID Telegram", "Nombre", "Usuario", "Origen", "Plan", "Estado",
                "Último pago", "Vence"])
    for r in rows:
        w.writerow([
            r["user_id"], r["full_name"], f"@{r['username']}" if r["username"] else "",
            r["origin"], r["plan"], r["status"],
            fecha(parse(r["paid_at"])), fecha(parse(r["expires_at"])),
        ])
    data = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
    await update.message.reply_document(
        document=data, filename=f"miembros_{now().astimezone(TZ):%Y-%m-%d}.csv",
        caption="Lista de miembros (se abre en Excel o Google Sheets).",
    )


@admin_only
async def cmd_probar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["🔧 <b>Prueba de conexión</b>"]
    me = await context.bot.get_me()
    lines.append(f"Bot: @{me.username} ✅")
    if not CHANNEL_ID:
        lines.append("Canal: ❌ falta CHANNEL_ID (publica algo en el canal y te llega el ID)")
    else:
        try:
            cm = await context.bot.get_chat_member(CHANNEL_ID, me.id)
            is_admin = cm.status == "administrator"
            invite = getattr(cm, "can_invite_users", False)
            ban = getattr(cm, "can_restrict_members", False)
            lines.append(f"Administrador del canal: {'✅' if is_admin else '❌'}")
            lines.append(f"Puede invitar (crear links): {'✅' if invite else '❌'}")
            lines.append(f"Puede sacar miembros: {'✅' if ban else '⚠️ verificar con la prueba de vencimiento'}")
        except TelegramError as e:
            lines.append(f"Canal: ❌ {html.escape(str(e))}")
    try:
        app_info = await cryptopay("getMe", {})
        lines.append(
            f"Crypto Pay: ✅ app «{html.escape(str(app_info.get('name', '')))}» "
            f"({'PRUEBA' if CRYPTOPAY_TESTNET else 'REAL'})"
        )
    except Exception as e:  # noqa: BLE001
        lines.append(f"Crypto Pay: ❌ {html.escape(str(e))[:200]}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


@admin_only
async def cmd_sacar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /sacar @usuario  o  /sacar 123456789")
        return
    uid = find_user_id(context.args[0])
    if not uid:
        await update.message.reply_text("No encontré a esa persona.")
        return
    try:
        await remove_from_group(context.bot, uid)
    except TelegramError as e:
        await update.message.reply_text(f"No pude sacarlo: {e}")
        return
    db.execute("UPDATE members SET status='vencido' WHERE user_id=?", (uid,))
    db.commit()
    await update.message.reply_text(f"🚪 Listo, saqué del canal a {user_label(uid)}.", parse_mode="HTML")


@admin_only
async def cmd_forzar_vencimiento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """SOLO PARA PRUEBAS: simula que la membresía ya venció y pasó la gracia."""
    if not CRYPTOPAY_TESTNET:
        await update.message.reply_text("Este comando solo funciona en modo PRUEBA (testnet).")
        return
    if not context.args:
        await update.message.reply_text("Uso: /forzar_vencimiento @usuario")
        return
    uid = find_user_id(context.args[0])
    if not uid or not get_member(uid):
        await update.message.reply_text("Esa persona no tiene membresía registrada.")
        return
    past = now() - timedelta(days=GRACE_DAYS, hours=1)
    db.execute(
        "UPDATE members SET expires_at=?, status='activo', reminded=1 WHERE user_id=?",
        (iso(past), uid),
    )
    db.commit()
    await job_check_expirations(context)  # 1ª pasada: entra en gracia
    await job_check_expirations(context)  # 2ª pasada: sale del canal
    await update.message.reply_text(
        f"Simulado. Estado de {user_label(uid)}: <b>{get_member(uid)['status']}</b>",
        parse_mode="HTML",
    )


@admin_only
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛠 <b>Comandos de administrador</b>\n"
        "/miembros – resumen de miembros\n"
        "/vencen – quién vence en los próximos 7 días\n"
        "/buscar @usuario – estado de una persona\n"
        "/aprobar @usuario mensual|anual – dar acceso manual\n"
        "/exportar – descargar la lista en Excel/CSV\n"
        "/probar – verificar conexión con el canal y Crypto Pay\n"
        "/sacar @usuario – sacarlo del canal ahora\n"
        + ("/forzar_vencimiento @usuario – SOLO PRUEBAS: simula que se venció\n" if CRYPTOPAY_TESTNET else "")
        + "/id – ver tu ID de usuario\n\n"
        f"Modo de pagos: <b>{'PRUEBA (testnet)' if CRYPTOPAY_TESTNET else 'REAL'}</b>",
        parse_mode="HTML",
    )


# ----------------------------------------------------------------------------
# Arranque
# ----------------------------------------------------------------------------

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("estado", cmd_estado))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("acceso", cmd_acceso))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, on_channel_post))
    app.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(on_plan, pattern=r"^plan:"))

    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("miembros", cmd_miembros))
    app.add_handler(CommandHandler("vencen", cmd_vencen))
    app.add_handler(CommandHandler("buscar", cmd_buscar))
    app.add_handler(CommandHandler("aprobar", cmd_aprobar))
    app.add_handler(CommandHandler("exportar", cmd_exportar))
    app.add_handler(CommandHandler("probar", cmd_probar))
    app.add_handler(CommandHandler("sacar", cmd_sacar))
    app.add_handler(CommandHandler("forzar_vencimiento", cmd_forzar_vencimiento))

    app.job_queue.run_repeating(job_check_payments, interval=20, first=5)
    app.job_queue.run_repeating(job_check_expirations, interval=3600, first=30)

    log.info("Bot iniciado. Pagos en modo %s.", "PRUEBA" if CRYPTOPAY_TESTNET else "REAL")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
