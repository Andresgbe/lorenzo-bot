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

import asyncio
import csv
import html
import io
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import httpx
from telegram import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import BadRequest, RetryAfter, TelegramError
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
# Acceso a los cursos de la web (Edge Function bot-access de Supabase). Sin estas dos
# variables el bot funciona igual, solo con el canal.
WEB_ACCESS_URL = os.getenv("WEB_ACCESS_URL", "").strip()
WEB_ACCESS_SECRET = os.getenv("WEB_ACCESS_SECRET", "").strip()
WEB_ENABLED = bool(WEB_ACCESS_URL and WEB_ACCESS_SECRET)
# Opcionales. Comunidad: link al grupo de Telegram que se entrega a quien paga.
# Soporte: link para escribirle al equipo (por ejemplo https://t.me/usuario).
COMMUNITY_URL = os.getenv("COMMUNITY_URL", "").strip()
SUPPORT_URL = os.getenv("SUPPORT_URL", "").strip()
for _name, _url in (("COMMUNITY_URL", COMMUNITY_URL), ("SUPPORT_URL", SUPPORT_URL)):
    if _url and not _url.startswith("https://"):
        raise SystemExit(f"{_name} debe ser un link que empiece con https://, por ejemplo https://t.me/...")

TZ = ZoneInfo("America/Caracas")
CRYPTOPAY_API = (
    "https://testnet-pay.crypt.bot/api" if CRYPTOPAY_TESTNET else "https://pay.crypt.bot/api"
)

# Moneda de cobro. PAYMENT_ASSET solo para pruebas (p. ej. TRX en testnet); en real, USDT.
ASSET = (os.getenv("PAYMENT_ASSET") or "USDT").strip().upper()
if ASSET not in {"USDT", "TON", "BTC", "ETH", "LTC", "BNB", "TRX", "USDC"}:
    raise SystemExit(f"PAYMENT_ASSET no es una moneda de Crypto Pay: {ASSET}")


def price_from_env(name: str, default: str) -> str:
    """Precio como texto, en la moneda de ASSET. PRICE_MENSUAL / PRICE_ANUAL solo para pruebas."""
    value = (os.getenv(name) or default).strip()
    try:
        if Decimal(value) <= 0:
            raise InvalidOperation
    except InvalidOperation:
        raise SystemExit(f"{name} debe ser un número mayor que cero, por ejemplo 27.77")
    return value


PLANS = {
    "mensual": {"name": "Mensual", "amount": price_from_env("PRICE_MENSUAL", "27.77"), "days": 30},
    "anual": {"name": "Anual", "amount": price_from_env("PRICE_ANUAL", "277.77"), "days": 365},
}
# Lo que se ahorra el anual frente a 12 meses, redondeado: "~55" con 27.77 y 277.77.
ANNUAL_SAVINGS = (
    Decimal(PLANS["mensual"]["amount"]) * 12 - Decimal(PLANS["anual"]["amount"])
).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
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
    "<b>Elige tu plan</b> (pago en {asset} con Crypto Pay):\n"
    "• Mensual: <b>{mensual} {asset}</b>\n"
    "• Anual: <b>{anual} {asset}</b>{ahorro}\n\n"
    "Abajo también puedes ver tu membresía y resolver tus dudas. 👇"
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

TEXT_ASK_EMAIL = (
    "🎓 Tu membresía también incluye los <b>cursos en la web</b>.\n\n"
    "Escríbeme tu <b>correo electrónico</b> y te creo el acceso al momento."
)

TEXT_WEB_READY = (
    "🎓 <b>Tu acceso a los cursos está listo.</b>\n\n"
    "Toca el botón para entrar. El enlace sirve una sola vez y vence en unos minutos: "
    "si se te pasa, escribe /web y te mando otro.\n\n"
    "Ya dentro, crea tu contraseña en <b>Mi cuenta → Seguridad</b> para entrar después con tu correo."
)

TEXT_WEB_RENEWED = (
    "🎓 Tu acceso a los cursos en la web también se renovó. "
    "Entra con tu correo y tu contraseña."
)

TEXT_NO_ACCESS = (
    "Entraste al canal de la <b>Comunidad Lorenzo Broker</b> sin una membresía activa, "
    "así que tuve que sacarte.\n\nElige un plan aquí y te envío tu acceso personal al momento:"
)

TEXT_COMMUNITY = (
    "\n\n👥 Únete también a la <b>comunidad de Telegram</b> con el botón de abajo."
)


def faq_items() -> list[tuple[str, str]]:
    """Preguntas frecuentes del menú: (pregunta, respuesta en HTML). Se editan aquí.

    Ojo con el orden: el botón «¿Cómo pago?» del mensaje de pago abre la tercera (go:faq:2).
    """
    support = (
        "\n\nSi sigue sin llegar, toca «💬 Hablar con soporte»."
        if SUPPORT_URL
        else "\n\nSi sigue sin llegar, escríbele al equipo de Lorenzo Broker."
    )
    items = [
        (
            "¿Qué incluye la membresía?",
            "Acceso al canal privado de la <b>Comunidad Lorenzo Broker</b>, donde recibes:\n"
            "📈 Ideas de inversión\n📊 Reportes de mercado\n"
            "🔴 Eventos en vivo, con su link para conectarte"
            + ("\n🎓 Además, acceso completo a los <b>cursos en la web</b>." if WEB_ENABLED else "")
            + ("\n👥 Y entrada a la comunidad de Telegram." if COMMUNITY_URL else ""),
        ),
        (
            "¿Cuánto cuesta?",
            f"• Mensual: <b>{PLANS['mensual']['amount']} {ASSET}</b> ({PLANS['mensual']['days']} días)\n"
            f"• Anual: <b>{PLANS['anual']['amount']} {ASSET}</b> ({PLANS['anual']['days']} días)"
            + (f"\n\nCon el anual ahorras ~{ANNUAL_SAVINGS} {ASSET} frente a 12 meses." if ANNUAL_SAVINGS > 0 else ""),
        ),
        (
            "¿Cómo pago?",
            f"Con <b>{ASSET}</b> a través de Crypto Pay (@CryptoBot), sin salir de Telegram:\n"
            "1. Elige tu plan en el menú.\n"
            "2. Toca «Pagar» y confirma el pago en @CryptoBot.\n"
            "3. En menos de un minuto te llega tu acceso aquí mismo.\n\n"
            f"Necesitas tener {ASSET} en tu cuenta de @CryptoBot. Si no tienes, puedes "
            "depositarlos ahí desde otra wallet o un exchange.",
        ),
        (
            "Ya pagué y no me llegó el acceso",
            "El pago se confirma en menos de un minuto. Si ya pasaron unos minutos:\n"
            "• Revisa en @CryptoBot que el pago aparezca como completado.\n"
            "• La factura vence 1 hora después de creada: si se venció, elige el plan otra vez.\n"
            "• Escribe /estado para ver si tu membresía ya está activa."
            + support,
        ),
        (
            "Mi link al canal no funciona",
            "Cada link es personal, sirve una sola vez y vence en 24 horas. Si se venció o ya "
            "lo usaste, entra a «🔑 Mis accesos» en el menú, o escribe /acceso, y te mando uno nuevo.",
        ),
    ]
    if WEB_ENABLED:
        items.append(
            (
                "¿Cómo entro a los cursos de la web?",
                "Después de pagar te pido tu correo (solo la primera vez) y te mando un enlace para "
                "entrar. El enlace sirve una sola vez y vence en unos minutos.\n\n"
                "Ya dentro, crea tu contraseña en <b>Mi cuenta → Seguridad</b> para entrar después "
                "con tu correo. Si necesitas otro enlace, escribe /web.",
            )
        )
    items += [
        (
            "¿Qué pasa cuando vence?",
            f"{REMIND_DAYS} días antes te aviso para que renueves. Si vence, tienes "
            f"<b>{GRACE_DAYS} días de gracia</b> para renovar sin perder el acceso. Después sales "
            "del canal, y puedes volver cuando quieras renovando aquí mismo.",
        ),
        (
            "¿Se cobra automáticamente?",
            "No. No hay cobros automáticos: solo pagas cuando tú decides renovar. Si renuevas "
            "antes de que venza, los días que te quedan se suman a la nueva fecha.",
        ),
    ]
    return items

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
# Migración: el correo que el usuario está dando para su acceso a la web
_user_columns = {r["name"] for r in db.execute("PRAGMA table_info(users)")}
if "awaiting_email" not in _user_columns:
    db.execute("ALTER TABLE users ADD COLUMN awaiting_email INTEGER DEFAULT 0")
if "pending_email" not in _user_columns:
    db.execute("ALTER TABLE users ADD COLUMN pending_email TEXT")
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


# ----------------------------------------------------------------------------
# Menú y navegación
#
# Botones "nav:<pantalla>" reemplazan el mensaje donde se tocan (para moverse por el menú).
# Botones "go:<pantalla>" mandan un mensaje nuevo, para no borrar mensajes importantes
# (bienvenida, links de acceso, recordatorios).
# ----------------------------------------------------------------------------

def btn_menu(edit: bool = True) -> InlineKeyboardButton:
    return InlineKeyboardButton("🏠 Menú principal", callback_data="nav:menu" if edit else "go:menu")


def plan_rows() -> list[list[InlineKeyboardButton]]:
    return [
        [InlineKeyboardButton(f"💳 Mensual · {PLANS['mensual']['amount']} {ASSET}", callback_data="plan:mensual")],
        [InlineKeyboardButton(f"💳 Anual · {PLANS['anual']['amount']} {ASSET}", callback_data="plan:anual")],
    ]


def plans_keyboard() -> InlineKeyboardMarkup:
    """Planes y vuelta al menú, para mensajes sueltos (recordatorios, avisos)."""
    return InlineKeyboardMarkup(plan_rows() + [[btn_menu(edit=False)]])


def menu_button(edit: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[btn_menu(edit)]])


def is_active(member) -> bool:
    return bool(member) and member["status"] in ("activo", "gracia")


def screen_menu(user) -> tuple[str, InlineKeyboardMarkup]:
    text = TEXT_START.format(
        name=html.escape(user.first_name or ""),
        mensual=PLANS["mensual"]["amount"],
        anual=PLANS["anual"]["amount"],
        asset=ASSET,
        ahorro=f" (ahorras ~{ANNUAL_SAVINGS} {ASSET} frente a 12 meses)" if ANNUAL_SAVINGS > 0 else "",
    )
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
    rows = plan_rows()
    rows.append([
        InlineKeyboardButton("👤 Mi membresía", callback_data="nav:cuenta"),
        InlineKeyboardButton("❓ Preguntas", callback_data="nav:faq"),
    ])
    if is_active(member):
        rows.append([InlineKeyboardButton("🔑 Mis accesos", callback_data="nav:accesos")])
    if SUPPORT_URL:
        rows.append([InlineKeyboardButton("💬 Hablar con soporte", url=SUPPORT_URL)])
    return text, InlineKeyboardMarkup(rows)


def screen_cuenta(user) -> tuple[str, InlineKeyboardMarkup]:
    member = get_member(user.id)
    if not is_active(member):
        text = "👤 <b>Mi membresía</b>\n\nNo tienes una membresía activa."
        if member:
            text += f" La última venció el {fecha(parse(member['expires_at']))}."
        text += "\n\nElige un plan y te doy acceso al momento:"
        return text, InlineKeyboardMarkup(plan_rows() + [[btn_menu()]])

    exp = parse(member["expires_at"])
    if member["status"] == "activo":
        days_left = max((exp - now()).days, 0)
        estado = f"✅ Activa · faltan {days_left} días" if days_left else "✅ Activa · vence hoy"
    else:
        estado = (
            "⚠️ Vencida, en período de gracia hasta el "
            f"{fecha(exp + timedelta(days=GRACE_DAYS))}"
        )
    text = (
        "👤 <b>Mi membresía</b>\n\n"
        f"Plan: <b>{PLANS[member['plan']]['name']}</b>\n"
        f"Estado: {estado}\n"
        f"Vence: <b>{fecha(exp)}</b>\n\n"
        "Si renuevas ahora, los días que te quedan se suman a la nueva fecha:"
    )
    rows = plan_rows() + [
        [InlineKeyboardButton("🔑 Mis accesos", callback_data="nav:accesos")],
        [btn_menu()],
    ]
    return text, InlineKeyboardMarkup(rows)


def screen_accesos(user) -> tuple[str, InlineKeyboardMarkup]:
    if not is_active(get_member(user.id)):
        return screen_cuenta(user)
    lines = ["🔑 <b>Mis accesos</b>\n", "• <b>Canal privado:</b> te mando un link personal (un solo uso, vence en 24 h)."]
    rows = [[InlineKeyboardButton("📢 Link al canal privado", callback_data="acc:canal")]]
    if WEB_ENABLED:
        lines.append("• <b>Cursos en la web:</b> te mando un enlace para entrar.")
        rows.append([InlineKeyboardButton("🎓 Entrar a los cursos", callback_data="acc:web")])
    if COMMUNITY_URL:
        lines.append("• <b>Comunidad de Telegram:</b> entra con el botón.")
        rows.append([InlineKeyboardButton("👥 Comunidad de Telegram", url=COMMUNITY_URL)])
    rows.append([btn_menu()])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def screen_faq(user) -> tuple[str, InlineKeyboardMarkup]:
    rows = [
        [InlineKeyboardButton(q, callback_data=f"nav:faq:{i}")]
        for i, (q, _) in enumerate(faq_items())
    ]
    if SUPPORT_URL:
        rows.append([InlineKeyboardButton("💬 Hablar con soporte", url=SUPPORT_URL)])
    rows.append([btn_menu()])
    return "❓ <b>Preguntas frecuentes</b>\n\nToca una pregunta para ver la respuesta:", InlineKeyboardMarkup(rows)


def screen_faq_answer(index: int) -> tuple[str, InlineKeyboardMarkup] | None:
    items = faq_items()
    if not 0 <= index < len(items):
        return None
    question, answer = items[index]
    rows = [[InlineKeyboardButton("⬅️ Volver a las preguntas", callback_data="nav:faq")]]
    if SUPPORT_URL:
        rows.append([InlineKeyboardButton("💬 Hablar con soporte", url=SUPPORT_URL)])
    rows.append([btn_menu()])
    return f"❓ <b>{question}</b>\n\n{answer}", InlineKeyboardMarkup(rows)


def build_screen(user, name: str) -> tuple[str, InlineKeyboardMarkup] | None:
    if name == "menu":
        return screen_menu(user)
    if name == "cuenta":
        return screen_cuenta(user)
    if name == "accesos":
        return screen_accesos(user)
    if name == "faq":
        return screen_faq(user)
    if name.startswith("faq:") and name[4:].isdigit():
        return screen_faq_answer(int(name[4:]))
    return None


async def send_screen(bot, user, name: str):
    result = build_screen(user, name)
    if result:
        text, markup = result
        await bot.send_message(user.id, text, parse_mode="HTML", reply_markup=markup)


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
# Acceso a la web (Supabase)
# ----------------------------------------------------------------------------

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")


async def web_access(payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            WEB_ACCESS_URL, headers={"x-bot-secret": WEB_ACCESS_SECRET}, json=payload
        )
    data = r.json()
    if r.status_code != 200:
        raise RuntimeError(f"bot-access {payload.get('action')} {r.status_code}: {data}")
    return data


def mask_email(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:1]}***@{domain}"


def set_awaiting_email(user_id: int, awaiting: bool, email: str | None = None):
    db.execute(
        "UPDATE users SET awaiting_email=?, pending_email=? WHERE user_id=?",
        (1 if awaiting else 0, email, user_id),
    )
    db.commit()


def web_button(link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🎓 Entrar a tus cursos", url=link)], [btn_menu(edit=False)]]
    )


def ask_email_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⏭ Ahora no, después", callback_data="email:later")]]
    )


async def sync_web(bot, user_id: int, plan: str, ref: str, method: str):
    """Lleva el pago a la web. Si falla, el pago y el canal ya están hechos: solo se avisa."""
    if not WEB_ENABLED:
        return
    try:
        result = await web_access(
            {
                "action": "payment",
                "ref": ref,
                "telegram_id": user_id,
                "plan": plan,
                "amount": PLANS[plan]["amount"],
                "asset": ASSET,
                "method": method,
            }
        )
        if result["status"] == "need_email":
            set_awaiting_email(user_id, True)
            await bot.send_message(
                user_id, TEXT_ASK_EMAIL, parse_mode="HTML", reply_markup=ask_email_keyboard()
            )
        elif result.get("login_link"):
            await bot.send_message(
                user_id, TEXT_WEB_READY, parse_mode="HTML", reply_markup=web_button(result["login_link"])
            )
        else:
            await bot.send_message(
                user_id, TEXT_WEB_RENEWED, parse_mode="HTML", reply_markup=menu_button()
            )
    except Exception as e:  # noqa: BLE001
        log.error("No pude llevar a la web el pago %s: %s", ref, e)
        await notify_admins(
            bot,
            f"⚠️ El pago de {user_label(user_id)} quedó en el bot y en el canal, pero no pude darle "
            f"acceso a la web: {html.escape(str(e))[:200]}",
        )


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


async def activate(bot, user_id: int, plan: str, source: str, ref: str | None = None, method: str = "crypto-pay"):
    """Activa o renueva la membresía, entrega el acceso al canal y, si hay ref, a la web."""
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
        if ref:
            await sync_web(bot, user_id, plan, ref, method)
        return

    # Por si había sido sacado antes por falta de pago
    try:
        await bot.unban_chat_member(CHANNEL_ID, user_id, only_if_banned=True)
    except TelegramError as e:
        log.warning("unban %s: %s", user_id, e)

    link_text = ""
    invite_failed = False
    rows = []
    if await is_in_group(bot, user_id):
        msg = TEXT_RENEWED.format(fecha=fecha(new_exp))
    else:
        msg = TEXT_WELCOME.format(fecha=fecha(new_exp))
        try:
            invite = await bot.create_chat_invite_link(
                CHANNEL_ID,
                member_limit=1,
                expire_date=now() + timedelta(days=1),
                name=f"pago {user_id}"[:32],
            )
            rows.append([InlineKeyboardButton("👉 Entrar al canal", url=invite.invite_link)])
            link_text = f"\nLink de acceso: {invite.invite_link}"
        except TelegramError as e:
            # La membresía ya quedó registrada: no usar /aprobar (sumaría días otra vez)
            log.error("No pude crear el link para %s: %s", user_id, e)
            invite_failed = True
            msg += "\n\nEn unos minutos escribe /acceso para recibir tu link al canal."
            link_text = (
                f"\n⚠️ No pude crear el link ({html.escape(str(e))}). Revisa que el bot siga "
                "siendo admin del canal y pídele que use /acceso. No uses /aprobar."
            )
    if COMMUNITY_URL:
        msg += TEXT_COMMUNITY
        rows.append([InlineKeyboardButton("👥 Comunidad de Telegram", url=COMMUNITY_URL)])
    rows.append([btn_menu(edit=False)])

    try:
        await bot.send_message(user_id, msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        delivered = link_text if invite_failed else "✅ Acceso enviado."
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

    if ref:
        await sync_web(bot, user_id, plan, ref, method)


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
    await send_screen(context.bot, user, "menu")


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    save_user(update.effective_user)
    await send_screen(context.bot, update.effective_user, "menu")


async def cmd_estado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    save_user(update.effective_user)
    await send_screen(context.bot, update.effective_user, "cuenta")


async def cmd_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    save_user(update.effective_user)
    await send_screen(context.bot, update.effective_user, "faq")


async def on_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Botones del menú. "nav:" reemplaza el mensaje; "go:" manda uno nuevo."""
    query = update.callback_query
    await query.answer()
    user = query.from_user
    save_user(user)
    kind, _, name = query.data.partition(":")
    result = build_screen(user, name)
    if not result:
        return
    text, markup = result
    if kind == "nav" and query.message:
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
            return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
            # Mensaje que no se puede editar (muy viejo, con foto...): se manda uno nuevo
    await context.bot.send_message(user.id, text, parse_mode="HTML", reply_markup=markup)


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
        await send_pay_button(context.bot, user.id, p, pending["pay_url"])
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
        await context.bot.send_message(
            user.id,
            "Hubo un problema generando tu pago. Intenta de nuevo en unos minutos.",
            reply_markup=menu_button(),
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
    await send_pay_button(context.bot, user.id, p, pay_url)


async def send_pay_button(bot, user_id: int, p: dict, pay_url: str):
    await bot.send_message(
        user_id,
        f"🧾 <b>Membresía {p['name']}: {p['amount']} {ASSET}</b>\n\n"
        "Toca el botón para pagar con Crypto Pay. Cuando el pago se confirme, "
        "te envío tu acceso aquí mismo (puede tardar hasta un minuto)."
        + ("\n\nDespués te pido tu correo para darte acceso a los cursos de la web." if WEB_ENABLED else "")
        + "\n\nLa factura vence 1 hora después de creada.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(f"💳 Pagar {p['amount']} {ASSET}", url=pay_url)],
                [InlineKeyboardButton("⬅️ Cambiar de plan", callback_data="go:menu")],
                [InlineKeyboardButton("❓ ¿Cómo pago?", callback_data="go:faq:2")],
            ]
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
                    await activate(context.bot, row["user_id"], row["plan"], "Crypto Pay", ref=row["ref"])
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
            [[InlineKeyboardButton("👉 Entrar al canal", url=invite.invite_link)], [btn_menu(edit=False)]]
        ),
    )


async def on_private_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe el correo cuando el bot lo pidió. Cualquier otro texto: muestra el menú."""
    user = update.effective_user
    save_user(user)
    u = get_user(user.id)
    if not WEB_ENABLED or not u or not u["awaiting_email"]:
        await update.message.reply_text("No entendí ese mensaje. Aquí tienes el menú 👇")
        await send_screen(context.bot, user, "menu")
        return
    email = update.message.text.strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        await update.message.reply_text(
            "Eso no parece un correo. Escríbelo así: nombre@ejemplo.com",
            reply_markup=ask_email_keyboard(),
        )
        return
    set_awaiting_email(user.id, True, email)
    await update.message.reply_text(
        f"¿Tu correo es <b>{html.escape(email)}</b>?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("✅ Sí, es correcto", callback_data="email:ok"),
                InlineKeyboardButton("✏️ Cambiar", callback_data="email:edit"),
            ]]
        ),
    )


async def on_email_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    u = get_user(user.id)
    if not WEB_ENABLED or not u or not u["awaiting_email"]:
        return
    if query.data == "email:later":
        set_awaiting_email(user.id, False)
        await context.bot.send_message(
            user.id,
            "Sin problema. Cuando quieras tu acceso a los cursos, escribe /web o entra a "
            "«🔑 Mis accesos» en el menú.",
            reply_markup=menu_button(),
        )
        return
    if query.data == "email:edit" or not u["pending_email"]:
        set_awaiting_email(user.id, True)
        await context.bot.send_message(
            user.id, "Escríbeme tu correo de nuevo:", reply_markup=ask_email_keyboard()
        )
        return

    try:
        result = await web_access(
            {
                "action": "link",
                "telegram_id": user.id,
                "email": u["pending_email"],
                "full_name": (user.full_name or "")[:120],
            }
        )
    except Exception as e:  # noqa: BLE001
        log.error("No pude crear el acceso web de %s: %s", user.id, e)
        await context.bot.send_message(
            user.id,
            "Hubo un problema creando tu acceso. Intenta de nuevo en unos minutos con /web.",
            reply_markup=menu_button(),
        )
        await notify_admins(
            context.bot,
            f"⚠️ No pude crear el acceso web de {user_label(user.id)}: {html.escape(str(e))[:200]}",
        )
        return

    status = result["status"]
    if status == "email_taken":
        await context.bot.send_message(
            user.id,
            "Ese correo ya está vinculado a otra cuenta de Telegram. Escríbeme otro correo, "
            "o pide ayuda al soporte.",
            reply_markup=ask_email_keyboard(),
        )
        return

    set_awaiting_email(user.id, False)
    if status == "created":
        await context.bot.send_message(
            user.id, TEXT_WEB_READY, parse_mode="HTML", reply_markup=web_button(result["login_link"])
        )
    elif status == "existing_account":
        await context.bot.send_message(
            user.id,
            f"Ese correo ya tenía una cuenta en la web ({html.escape(mask_email(result['email']))}). "
            "Tu membresía quedó en esa cuenta: entra con tu contraseña, o usa "
            "«¿La olvidaste?» en la web si no la recuerdas.",
            parse_mode="HTML",
            reply_markup=menu_button(),
        )
    elif status == "already_linked":
        await context.bot.send_message(
            user.id,
            f"Tu Telegram ya está vinculado a la cuenta {html.escape(mask_email(result['email']))}. "
            "Escribe /web para entrar.",
            parse_mode="HTML",
            reply_markup=menu_button(),
        )


async def send_web_access(bot, user):
    """Un enlace nuevo para entrar a los cursos de la web (o pide el correo si falta)."""
    if not WEB_ENABLED:
        await bot.send_message(
            user.id, "El acceso a los cursos de la web todavía no está disponible aquí.",
            reply_markup=menu_button(),
        )
        return
    if not is_active(get_member(user.id)):
        await bot.send_message(
            user.id,
            "Tu membresía no está activa. Elige un plan y te doy acceso al canal y a los cursos:",
            reply_markup=plans_keyboard(),
        )
        return
    try:
        result = await web_access({"action": "login_link", "telegram_id": user.id})
    except Exception as e:  # noqa: BLE001
        log.error("No pude generar el enlace web de %s: %s", user.id, e)
        await bot.send_message(
            user.id, "Hubo un problema. Intenta de nuevo en unos minutos.", reply_markup=menu_button()
        )
        return
    if result["status"] == "link":
        await bot.send_message(
            user.id, TEXT_WEB_READY, parse_mode="HTML", reply_markup=web_button(result["login_link"])
        )
    elif result["status"] == "use_password":
        await bot.send_message(
            user.id,
            f"Tu cuenta de la web es {html.escape(mask_email(result['email']))}. Entra con tu correo "
            "y tu contraseña, o usa «¿La olvidaste?» en la web.",
            parse_mode="HTML",
            reply_markup=menu_button(),
        )
    else:
        set_awaiting_email(user.id, True)
        await bot.send_message(
            user.id, TEXT_ASK_EMAIL, parse_mode="HTML", reply_markup=ask_email_keyboard()
        )


async def send_channel_access(bot, user_id: int):
    """El miembro pide un link nuevo al canal (por ejemplo, si el anterior venció)."""
    if not is_active(get_member(user_id)):
        await bot.send_message(
            user_id,
            "Tu membresía no está activa. Elige un plan y te envío el acceso de inmediato:",
            reply_markup=plans_keyboard(),
        )
        return
    if not CHANNEL_ID:
        await bot.send_message(
            user_id, "El acceso al canal todavía no está listo. Intenta más tarde.",
            reply_markup=menu_button(),
        )
        return
    if await is_in_group(bot, user_id):
        await bot.send_message(user_id, "Ya estás dentro del canal. ✅", reply_markup=menu_button())
        return
    try:
        await send_invite(bot, user_id, "Este es tu acceso personal al canal (un solo uso, vence en 24 h):")
    except TelegramError as e:
        log.error("No pude crear el link para %s: %s", user_id, e)
        await bot.send_message(
            user_id, "No pude crear tu link en este momento. Intenta de nuevo en unos minutos.",
            reply_markup=menu_button(),
        )
        await notify_admins(
            bot,
            f"⚠️ {user_label(user_id)} pidió su link al canal y no pude crearlo: "
            f"{html.escape(str(e))[:200]}. Revisa que el bot siga siendo admin del canal.",
        )


async def cmd_web(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    save_user(update.effective_user)
    await send_web_access(context.bot, update.effective_user)


async def cmd_acceso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    await send_channel_access(context.bot, update.effective_user.id)


async def on_access_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Botones de «Mis accesos»."""
    query = update.callback_query
    await query.answer()
    user = query.from_user
    save_user(user)
    if query.data == "acc:canal":
        await send_channel_access(context.bot, user.id)
    elif query.data == "acc:web":
        await send_web_access(context.bot, user)


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
    ref = f"manual:{uid}:{now().timestamp()}"
    db.execute(
        """INSERT INTO payments (ref, user_id, plan, amount, method, status, created_at, paid_at)
           VALUES (?,?,?,?, 'manual', 'pagado', ?, ?)""",
        (ref, uid, plan, PLANS[plan]["amount"], iso(now()), iso(now())),
    )
    db.commit()
    await activate(context.bot, uid, plan, f"aprobado por {update.effective_user.first_name}", ref=ref, method="manual")


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
async def cmd_saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saldo de la app de Crypto Pay conectada: lo cobrado que aún no se pasó a la Wallet."""
    try:
        app_info = await cryptopay("getMe", {})
        balances = await cryptopay("getBalance", {})
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"No pude consultar Crypto Pay: {html.escape(str(e))[:200]}", parse_mode="HTML")
        return
    # Se muestra la moneda de cobro siempre, y las demás solo si tienen saldo
    lines = []
    for b in balances:
        available, onhold = Decimal(b["available"]), Decimal(b.get("onhold") or "0")
        if b["currency_code"] == ASSET or available or onhold:
            line = f"• {b['currency_code']}: <b>{b['available']}</b>"
            if onhold:
                line += f" (en espera: {b['onhold']})"
            lines.append(line)
    await update.message.reply_text(
        f"💼 <b>Saldo de la app «{html.escape(str(app_info.get('name', '')))}»</b> "
        f"({'PRUEBA' if CRYPTOPAY_TESTNET else 'REAL'})\n"
        + ("\n".join(lines) or f"• {ASSET}: <b>0</b>")
        + "\n\nYa descontada la comisión de Crypto Pay. Se pasa a la Wallet desde "
        "@CryptoBot → Crypto Pay → la app → Transfer Coins to Wallet.",
        parse_mode="HTML",
    )


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


def broadcast_targets(audience: str) -> list[int]:
    if audience == "activos":
        sql = "SELECT user_id FROM members WHERE status IN ('activo','gracia')"
    else:
        sql = "SELECT user_id FROM users"
    return [r["user_id"] for r in db.execute(sql)]


@admin_only
async def cmd_difundir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manda un aviso a los miembros. Se responde a un mensaje con /difundir, o /difundir texto."""
    msg = update.message
    if msg.reply_to_message:
        data = {"chat": msg.chat_id, "message_id": msg.reply_to_message.message_id}
    else:
        parts = (msg.text_html or "").split(maxsplit=1)
        if len(parts) < 2:
            await msg.reply_text(
                "Uso:\n• /difundir texto del aviso\n"
                "• O responde a cualquier mensaje (texto, foto, video…) con /difundir"
            )
            return
        data = {"html": parts[1]}
        await msg.reply_text("Así se va a ver:")
        await msg.reply_text(parts[1], parse_mode="HTML")
    context.user_data["broadcast"] = data
    activos, todos = len(broadcast_targets("activos")), len(broadcast_targets("todos"))
    await msg.reply_text(
        "📣 <b>¿A quién se lo mando?</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(f"Miembros activos ({activos})", callback_data="bc:activos")],
                [InlineKeyboardButton(f"Todos los que iniciaron el bot ({todos})", callback_data="bc:todos")],
                [InlineKeyboardButton("❌ Cancelar", callback_data="bc:cancelar")],
            ]
        ),
    )


@admin_only
async def on_broadcast_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = context.user_data.pop("broadcast", None)
    audience = query.data.split(":", 1)[1]
    if audience == "cancelar":
        await query.edit_message_text("Difusión cancelada.")
        return
    if not data:
        await query.edit_message_text("Ya no hay un aviso pendiente. Vuelve a usar /difundir.")
        return
    targets = broadcast_targets(audience)
    await query.edit_message_text(f"📣 Enviando a {len(targets)} personas… Te aviso al terminar.")
    context.application.create_task(run_broadcast(context.bot, query.from_user.id, data, targets))


async def run_broadcast(bot, admin_id: int, data: dict, targets: list[int]):
    sent = failed = 0
    markup = menu_button()
    for uid in targets:
        for _ in range(2):  # un reintento si Telegram pide esperar
            try:
                if "html" in data:
                    await bot.send_message(uid, data["html"], parse_mode="HTML", reply_markup=markup)
                else:
                    await bot.copy_message(uid, data["chat"], data["message_id"], reply_markup=markup)
                sent += 1
                break
            except RetryAfter as e:
                wait = e.retry_after
                await asyncio.sleep(wait.total_seconds() if hasattr(wait, "total_seconds") else wait)
            except TelegramError:
                failed += 1
                break
        else:
            failed += 1
        await asyncio.sleep(0.05)  # Telegram permite ~30 mensajes por segundo
    try:
        await bot.send_message(
            admin_id,
            f"📣 Difusión terminada: <b>{sent}</b> enviados"
            + (f", {failed} sin entregar (bloquearon el bot o ya no existen)." if failed else "."),
            parse_mode="HTML",
        )
    except TelegramError:
        pass


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
        "/saldo – ver el saldo de la app de Crypto Pay\n"
        "/sacar @usuario – sacarlo del canal ahora\n"
        "/difundir texto – mandar un aviso a los miembros (o responde a un mensaje con /difundir)\n"
        + ("/forzar_vencimiento @usuario – SOLO PRUEBAS: simula que se venció\n" if CRYPTOPAY_TESTNET else "")
        + "/id – ver tu ID de usuario\n\n"
        f"Modo de pagos: <b>{'PRUEBA (testnet)' if CRYPTOPAY_TESTNET else 'REAL'}</b>",
        parse_mode="HTML",
    )


# ----------------------------------------------------------------------------
# Arranque
# ----------------------------------------------------------------------------

async def post_init(app: Application):
    """Lista de comandos que Telegram muestra en el botón «Menú» del chat."""
    user_cmds = [
        BotCommand("menu", "Menú principal"),
        BotCommand("estado", "Ver mi membresía"),
        BotCommand("acceso", "Link nuevo al canal"),
    ]
    if WEB_ENABLED:
        user_cmds.append(BotCommand("web", "Entrar a los cursos"))
    user_cmds.append(BotCommand("ayuda", "Preguntas frecuentes"))
    admin_cmds = user_cmds + [
        BotCommand("admin", "Comandos de administrador"),
        BotCommand("miembros", "Resumen de miembros"),
        BotCommand("vencen", "Quién vence en 7 días"),
        BotCommand("difundir", "Mandar un aviso"),
        BotCommand("saldo", "Saldo de Crypto Pay"),
    ]
    try:
        await app.bot.set_my_commands(user_cmds, scope=BotCommandScopeAllPrivateChats())
    except TelegramError as e:
        log.warning("No pude poner la lista de comandos: %s", e)
    for admin_id in ADMIN_IDS:
        try:
            await app.bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(admin_id))
        except TelegramError as e:  # el admin aún no inició el bot
            log.warning("No pude poner los comandos del admin %s: %s", admin_id, e)


def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("ayuda", cmd_ayuda))
    app.add_handler(CommandHandler("estado", cmd_estado))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("acceso", cmd_acceso))
    app.add_handler(CommandHandler("web", cmd_web))
    app.add_handler(CallbackQueryHandler(on_email_choice, pattern=r"^email:"))
    app.add_handler(CallbackQueryHandler(on_nav, pattern=r"^(nav|go):"))
    app.add_handler(CallbackQueryHandler(on_access_button, pattern=r"^acc:"))
    app.add_handler(CallbackQueryHandler(on_broadcast_choice, pattern=r"^bc:"))
    app.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, on_private_text)
    )
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
    app.add_handler(CommandHandler("saldo", cmd_saldo))
    app.add_handler(CommandHandler("sacar", cmd_sacar))
    app.add_handler(CommandHandler("difundir", cmd_difundir))
    app.add_handler(CommandHandler("forzar_vencimiento", cmd_forzar_vencimiento))

    app.job_queue.run_repeating(job_check_payments, interval=20, first=5)
    app.job_queue.run_repeating(job_check_expirations, interval=3600, first=30)

    log.info("Bot iniciado. Pagos en modo %s.", "PRUEBA" if CRYPTOPAY_TESTNET else "REAL")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
