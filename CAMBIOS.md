# Cambios de la revisión (AG Software, 05/11/2026)

El código original estaba bien: tokens solo en variables, token fuera de los logs, pagos sin doble activación, montos como texto y fechas en UTC. Estos son los arreglos:

1. **`.gitignore` nuevo**: `.env` y `*.db` ya no se pueden subir al repo por accidente.
2. **Si falla crear el link al canal**, la membresía queda guardada, el alumno recibe la bienvenida con "escribe /acceso" y los admins reciben un aviso que dice "No uses /aprobar". Antes el aviso decía "Usa /aprobar", y eso sumaba los días dos veces.
3. **Si el bot no puede sacar a alguien** (por ejemplo, porque perdió el permiso en el canal), ya no lo marca como vencido ni avisa "Salió del canal": avisa "No pude sacarlo" y lo reintenta cada hora.
4. **`/forzar_vencimiento` solo funciona en modo PRUEBA** y no aparece en `/admin` en modo REAL.
5. **Facturas:** si el alumno toca el plan varias veces, se reutiliza su factura pendiente en vez de crear otra. El revisor de pagos ahora revisa todas las pendientes, de 100 en 100 (antes solo las 100 más recientes). Las facturas que Crypto Pay ya no devuelve se marcan como vencidas después de 2 horas.
6. **`/start` en periodo de gracia** ya no dice "activa hasta" una fecha pasada: muestra cuándo venció y hasta qué día puede renovar.
7. **`python-dotenv`**: en una computadora local lee el archivo `.env`. En Railway no cambia nada (sigue usando sus Variables).

La base de datos se actualiza sola al arrancar: agrega la columna `payments.pay_url`. No hay que borrar `bot.db`.

## Precios configurables para pruebas (09/11/2026)

- Variables opcionales `PRICE_MENSUAL` y `PRICE_ANUAL` para probar con dinero real a 1 y 2 USDT. Sin ellas, el bot cobra 27.77 y 297 como siempre.
- El mensaje de `/start`, los botones de plan y el ahorro del anual usan esos precios; antes estaban escritos a mano en el texto.
- Si el valor no es un número mayor que cero, el bot no arranca y dice por qué.
- **Quitar estas variables antes de abrir los cobros reales.**
- Variable opcional `PAYMENT_ASSET` para cobrar en otra moneda solo durante las pruebas (por ejemplo `TRX`, porque Crypto Testnet Bot no acredita el USDT de prueba de los faucets). Sin ella, el bot cobra en USDT. Los textos de `/start` y los botones muestran la moneda configurada. **Quitarla antes de los cobros reales.**

## Comando /saldo (10/11/2026)

- Nuevo comando de administrador `/saldo`: muestra el saldo de la app de Crypto Pay conectada (lo cobrado, ya sin la comisión, que aún no se pasó a la Wallet). Siempre muestra la moneda de cobro y las demás solo si tienen saldo. Aparece en `/admin`.

## Acceso a los cursos de la web (10/11/2026)

Quien paga en el bot recibe también acceso completo a la plataforma web de Lorenzo.

- Al confirmar un pago (Crypto Pay o `/aprobar`), el bot avisa a la función `bot-access` de Supabase.
- Si la persona no tiene cuenta web vinculada, el bot le pide su correo, se lo hace confirmar y la función le crea la cuenta y le da la membresía. Recibe un botón **"🎓 Entrar a tus cursos"** con un enlace de un solo uso.
- Si ese correo ya tenía cuenta, la membresía se aplica a esa cuenta, pero el bot **no** da un enlace de entrada: la persona entra con su contraseña. Así nadie entra a una cuenta ajena escribiendo su correo.
- Nuevo comando `/web`: pide un enlace nuevo, o el correo si aún no lo dio.
- Las renovaciones también se aplican a la web. Cada pago cuenta una sola vez aunque se reporte dos veces.
- Si la web falla, el pago y el canal quedan igual y los admins reciben un aviso.
- Se activa con `WEB_ACCESS_URL` y `WEB_ACCESS_SECRET`. Sin ellas, el bot funciona como antes.
- **Activado y probado el 10/11/2026:** con `/aprobar @usuario mensual`, el bot pidió el correo, creó la cuenta y el enlace entró a la web con la membresía activa. Después de cambiar el `.env` hay que reiniciar el bot: solo lo lee al arrancar.
- Pendiente: `/sacar` todavía no quita el acceso a la web, y los pagos hechos antes de la integración no llegaron a la web.

## Menú, preguntas frecuentes y comunidad (11/11/2026)

- **Menú principal** con `/start` o `/menu`: planes, "👤 Mi membresía", "❓ Preguntas", "🔑 Mis accesos" (solo miembros) y "💬 Hablar con soporte".
- **Siempre hay forma de volver:** cada pantalla y cada mensaje del bot (bienvenida, pago, recordatorios, links, avisos) tiene un botón "🏠 Menú principal". El mensaje de pago también tiene "⬅️ Cambiar de plan" y "❓ ¿Cómo pago?". Al moverse por el menú, el mensaje se reemplaza en lugar de llenar el chat. La bienvenida, los links y los recordatorios no se borran: el menú llega en un mensaje nuevo.
- **Preguntas frecuentes** (también con `/ayuda`): qué incluye, precios, cómo pagar, qué hacer si no llega el acceso, link vencido, cursos de la web, vencimiento y que no hay cobros automáticos. Se editan en `faq_items()` en `bot.py`.
- **Mi membresía** (también con `/estado`): plan, estado, días que faltan, fecha de vencimiento y botones para renovar.
- **Mis accesos:** botones para pedir un link nuevo al canal, entrar a los cursos de la web y abrir la comunidad.
- **Comunidad de Telegram:** con la variable `COMMUNITY_URL`, la bienvenida después del pago trae el botón "👥 Comunidad de Telegram". También aparece en "Mis accesos". Solo lo ven los miembros.
- **Soporte:** con la variable `SUPPORT_URL`, aparece el botón "💬 Hablar con soporte" en el menú y en las preguntas frecuentes.
- **Correo para la web:** se puede responder "⏭ Ahora no, después" y pedir el acceso luego con `/web` o desde "Mis accesos". El mensaje de pago avisa que después se pedirá el correo.
- **Texto que el bot no entiende:** responde con el menú en lugar de quedarse callado.
- **Lista de comandos de Telegram** (el botón "Menú" junto al chat): los usuarios ven `/menu`, `/estado`, `/acceso`, `/web` y `/ayuda`; los admins ven además sus comandos.
- **Nuevo `/difundir`** (admin): manda un aviso a los miembros activos o a todos los que iniciaron el bot, y pregunta antes de enviar. Se puede escribir el texto o responder a cualquier mensaje (foto, video, formato) con `/difundir`. Al terminar avisa cuántos se entregaron.
- `/acceso` ya no se cae si Telegram no deja crear el link: le avisa al miembro y a los admins.
- No hay cambios en la base de datos.
