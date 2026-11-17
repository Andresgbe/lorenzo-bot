# Guía de instalación – @LorenzoBrokerBot

Guía paso a paso para poner a funcionar el bot del canal privado de la Comunidad Lorenzo Broker. No hace falta saber programar. Tiempo estimado: 1 a 2 horas.

**Archivos del bot:**
- `bot.py` – el programa
- `requirements.txt` – lo que el servidor necesita instalar
- `Procfile` – le dice al servidor cómo arrancar el bot
- `.env.example` – la lista de "variables" (claves) que hay que configurar

> 🔒 **Regla de oro:** los tokens son contraseñas. Nunca los pegues en chats, correos ni capturas. Solo van en el panel de "Variables" del servidor.

---

## Paso 1 – Crear la app de Crypto Pay (modo prueba primero)

Primero probamos con dinero de prueba.

1. En Telegram, abre **@CryptoTestnetBot**.
2. Envía `/pay` → **Crypto Pay** → **Crear app** (Create App). Ponle de nombre "Comunidad Lorenzo Broker".
3. Entra a la app → **API Token**. Copia el token y guárdalo en un lugar seguro (por ejemplo, en notas privadas).

Más adelante, para cobrar de verdad, repetirás esto en **@CryptoBot** (el real) y obtendrás otro token.

---

## Paso 2 – Tener el token del bot

1. Abre **@BotFather** → `/mybots` → **@LorenzoBrokerBot** → **API Token**.
2. Cópialo y guárdalo junto al de Crypto Pay.

---

## Paso 3 – Revisar el bot en el canal

El canal es **"Lorenzo Broker — Miembros"** (privado, con contenido protegido).

1. Abre el canal → nombre del canal → **Administradores** → @LorenzoBrokerBot.
2. Activa **todos los permisos que aparezcan**, excepto "Agregar administradores". Lo indispensable es **Agregar suscriptores / Invitar mediante enlace**.
3. No compartas links generales del canal. Desde ahora **solo se entra con el link personal que da el bot**: si alguien entra por otro lado y no tiene membresía, el bot lo saca solo.

---

## Paso 4 – Subir el código a GitHub

El servidor toma el código desde GitHub.

1. Entra a **github.com** (puedes crear una cuenta gratis) → botón **New** (nuevo repositorio).
2. Nombre: `lorenzo-bot`. Marca **Private** (privado). Crear.
3. Toca **uploading an existing file**, arrastra los 4 archivos (`bot.py`, `requirements.txt`, `Procfile`, `.env.example`) y toca **Commit changes**.

---

## Paso 5 – Crear el servidor (Railway)

Railway mantiene el bot encendido 24/7. Cuesta aprox. **$5 al mes** (verifica el precio actual al registrarte).

> Railway cobra con tarjeta internacional. Si tu tarjeta no pasa, avísame y vemos un servidor que acepte otra forma de pago.

1. Entra a **railway.com** → **Login with GitHub**.
2. **New Project** → **Deploy from GitHub repo** → elige `lorenzo-bot`.
3. Ve a la pestaña **Variables** y agrega una por una (nombre = valor):

| Variable | Qué poner |
|---|---|
| `BOT_TOKEN` | El token de @BotFather |
| `CRYPTOPAY_TOKEN` | El token de @CryptoTestnetBot (prueba) |
| `CRYPTOPAY_TESTNET` | `true` |
| `ADMIN_IDS` | Tu ID de Telegram (ver Paso 6) |
| `CHANNEL_ID` | El ID del canal (ver Paso 6) |
| `DB_PATH` | `/data/bot.db` |
| `COMMUNITY_URL` | *(Opcional)* Link al grupo de la comunidad en Telegram (`https://t.me/...`). Se entrega solo a quien paga. |
| `SUPPORT_URL` | *(Opcional)* Link para escribirle a soporte, por ejemplo `https://t.me/tu_usuario`. Aparece en el menú y en las preguntas frecuentes. |

4. Agrega un **Volume** (disco donde se guarda la lista de miembros): en el proyecto, botón **+ New** → **Volume** → conéctalo al servicio del bot con la ruta de montaje `/data`.
   ⚠️ Sin este paso, la lista de miembros se borraría cada vez que el servidor se reinicie.
5. Si en **Settings → Deploy** te pide un "Start Command", escribe: `python bot.py`

---

## Paso 6 – Obtener tu ID y el ID del canal

Con el bot ya encendido (aunque falten estos dos valores):

1. **Tu ID:** escríbele `/id` al bot en privado. Copia "Tu ID de usuario" → ponlo en `ADMIN_IDS` y guarda. Railway reinicia el bot solo.
2. **ID del canal:** publica cualquier mensaje en el canal (luego lo puedes borrar). El bot te escribe en privado: "📢 ID del canal…" (empieza con `-100…`) → ponlo en `CHANNEL_ID` y guarda.
3. Escríbele `/probar` al bot. Todo debe salir en ✅ (si "Puede sacar miembros" sale en ⚠️, lo confirmamos en la prueba del Paso 7).

Si quieres que otra persona de tu equipo también sea administrador del bot, pon los IDs separados por coma: `123456,789012`.

---

## Paso 7 – Probar todo (antes del 10 de noviembre)

1. En **@CryptoTestnetBot**, consigue USDT de prueba (el bot de testnet tiene opciones para recibir fondos de prueba).
2. Desde **otra cuenta de Telegram** (la de un amigo o una secundaria), abre `https://t.me/LorenzoBrokerBot?start=web`.
3. Elige **Mensual** → paga la factura con el dinero de prueba.
4. Revisa que:
   - ✅ En menos de 1 minuto llegue la bienvenida con el botón "Entrar al canal".
   - ✅ El link funcione una sola vez.
   - ✅ Tú recibas el aviso "💰 Pago confirmado".
   - ✅ `/miembros`, `/vencen`, `/buscar @usuario` y `/exportar` respondan.
5. **Cuenta vencida:** con la cuenta de prueba que pagó, escribe tú `/forzar_vencimiento @usuario`. Debe salir del canal y recibir el mensaje de vencimiento. Luego que vuelva a pagar: debe recibir un link nuevo y entrar.
6. **Cuenta sin pago:** pásale a una tercera cuenta el link que recibió otra persona (ya usado) o agrégala a mano: no debe poder quedarse; el bot la saca y te avisa.
7. Prueba `/aprobar @usuario mensual` y `/acceso` (el miembro pide un link nuevo).

---

## Paso 8 – Pasar a cobros reales

1. En **@CryptoBot** (el real): `/pay` → **Crypto Pay** → **Crear app** → copia el nuevo **API Token**.
2. En Railway → **Variables**:
   - `CRYPTOPAY_TOKEN` = el token real
   - `CRYPTOPAY_TESTNET` = `false`
3. Escribe `/admin` al bot. Debe decir **Modo de pagos: REAL**.

⚠️ La base de datos de prueba sigue ahí. Si quieres arrancar limpio, en Railway borra el archivo del volumen o cambia `DB_PATH` a `/data/bot-real.db`.

---

## Paso 9 – Conectar la página web

En la página, el botón "Unirme" debe llevar a:

```
https://t.me/LorenzoBrokerBot?start=web
```

Para Instagram (link en la bio): `https://t.me/LorenzoBrokerBot?start=instagram`

---

## Cómo funciona el día a día

- **Menú** → con `/start` o `/menu` se abre el menú: planes, "Mi membresía", "Preguntas frecuentes", "Mis accesos" (solo miembros) y soporte. Cada pantalla y cada mensaje del bot tiene un botón para volver al menú. Si alguien escribe algo que el bot no entiende, le responde con el menú.
- **Alguien paga** → recibe la bienvenida, un link de un solo uso al canal (vence en 24 h) y, si `COMMUNITY_URL` está configurada, el botón a la comunidad de Telegram; tú recibes un aviso.
- **Se le venció el link sin usarlo** → escribe `/acceso` al bot y recibe uno nuevo.
- **Alguien entra sin membresía** (link ajeno o agregado a mano) → el bot lo saca al instante y te avisa.
- **3 días antes de vencer** → el bot le recuerda que renueve.
- **Vence** → tiene **3 días de gracia**; te avisa a ti y a él.
- **Pasan los 3 días** → el bot lo saca del canal y te avisa. Si paga después, entra de nuevo con un link nuevo.
- **Renueva antes de vencer** → los días se suman a su fecha actual.
- **Los administradores del canal nunca son expulsados.**
- **Acceso a la web** (si `WEB_ACCESS_URL` y `WEB_ACCESS_SECRET` están configuradas): después de pagar, el bot pide el correo y entrega un botón "Entrar a tus cursos". Con `/web` el miembro pide un enlace nuevo.

### Comandos de administrador
| Comando | Qué hace |
|---|---|
| `/admin` | Lista de comandos y modo de pago (prueba o real) |
| `/miembros` | Cuántos activos, en gracia y vencidos |
| `/vencen` | Quién vence en los próximos 7 días |
| `/buscar @usuario` | Estado de una persona |
| `/aprobar @usuario mensual` (o `anual`) | Dar acceso manual (pagó por otra vía) |
| `/exportar` | Descarga la lista en un archivo que abre Excel o Google Sheets |
| `/sacar @usuario` | Lo saca del canal en el momento |
| `/probar` | Verifica la conexión con el canal y con Crypto Pay |
| `/saldo` | Saldo de la app de Crypto Pay (lo cobrado que aún no pasaste a tu Wallet) |
| `/difundir texto` | Manda un aviso a los miembros activos o a todos los que iniciaron el bot (te pregunta antes de enviar). También puedes responder a cualquier mensaje (foto, video, texto con formato) con `/difundir` |
| `/forzar_vencimiento @usuario` | **Solo para pruebas:** simula que se le venció |

### Miembros que ya están en el canal antes del lanzamiento
El bot no saca a quienes ya estaban dentro antes de encenderlo. Si ya hay gente en el canal que pagó, pídeles que escriban `/start` al bot y usa `/aprobar @usuario mensual` para registrarlos con su fecha.

---

## Si algo falla
- **El bot no responde:** en Railway → **Deployments** → **View logs**. Envíame el error (sin tokens) y lo revisamos.
- **"Error creando factura de Crypto Pay":** revisa que `CRYPTOPAY_TOKEN` y `CRYPTOPAY_TESTNET` coincidan (token de testnet con `true`, token real con `false`).
- **No da el link al canal:** el bot no es administrador o le falta el permiso de invitar. Usa `/probar`.
