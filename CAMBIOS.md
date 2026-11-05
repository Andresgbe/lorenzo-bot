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
