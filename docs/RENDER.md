# Prueba de Certicoex en Render

Esta configuración prepara un único servicio Docker de prueba. No publica nada por sí sola. Hace falta que el código esté en GitHub y que Render tenga acceso al repositorio y la rama que contiene `render.yaml`. El entorno cloud publicado y el servicio Render son cosas distintas.

## Crear la prueba

1. En Render, conecta GitHub y autoriza el repositorio `kaxila10/certicoex`.
2. Cuando la rama de despliegue esté subida a GitHub, elige **New → Blueprint** y selecciona ese repositorio y esa rama. El fichero es `render.yaml`.
3. Revisa que el servicio `certicoex` usa Docker, región Frankfurt y **plan Free**, si tu cuenta permite ese plan. No selecciones un plan de pago para esta prueba. Si Render no ofrece Free o muestra un coste, detente antes de confirmar.
4. Tras crear el servicio, espera a que aparezca **Live**. La construcción incluye LibreOffice y puede tardar varios minutos.
5. En el servicio, abre **Environment**: el usuario es `tecnico` y la contraseña es el valor generado de `CERTICOEX_PASSWORD`. Se configura en Render, no en el código ni en el chat. Si Render oculta el valor generado, puedes sustituirlo ahí por una contraseña propia de al menos 16 caracteres y guardar el cambio.
6. Abre la dirección HTTPS que Render muestre para el servicio. El navegador pedirá usuario y contraseña. Después, entra en **Mi perfil** y guarda tu nombre y firma.

La plantilla preparada de manipulado, envasado y control de maquinaria se carga en una instalación nueva. Se ha retirado la firma anterior, el nombre fijo y los metadatos de autor. Los demás cursos se añaden desde la app con los marcadores documentados en README.

## Límites de esta prueba

El plan Free usa almacenamiento temporal: perfil, firma, plantillas añadidas y documentos pueden perderse al reiniciar, redesplegar o suspender la instancia. La plantilla inicial se restaura desde el código; los demás datos no. La app muestra un aviso de este modo. Para conservar tus datos de forma estable hará falta configurar almacenamiento persistente y revisar su coste; esta configuración no contrata ningún disco.

Render puede suspender el servicio por inactividad y la primera entrada tardará más. La conversión de grandes lotes depende de la memoria y los tiempos de espera del plan; empieza con 2–3 alumnos ficticios. La prueba local de 50 alumnos no garantiza ese tamaño en el plan Free. No es todavía una versión multiusuario: hay una contraseña y un perfil de técnico por instalación.

## Si falla el despliegue

- Error de construcción: revisa los logs del servicio, especialmente la instalación de paquetes y dependencias.
- Salud HTTP 503: comprueba que `CERTICOEX_PASSWORD` contiene al menos 16 caracteres.
- No aparece la plantilla: revisa que la rama incluye la carpeta `defaults/` y se ha construido esa revisión.
- Puedes entrar pero no genera PDF: revisa los logs de LibreOffice y los recursos del servicio. El Word debería seguir disponible si la conversión devuelve un fallo controlado.

## Comprobación local del contenedor

```bash
docker build -t certicoex:render .
```

Ejecuta la imagen proporcionando una contraseña mediante las variables seguras de tu entorno y publica el puerto 10000 solo en la interfaz local de tu equipo. No añadas la contraseña al Dockerfile, Git ni a los comandos guardados. `GET /api/health` debe responder sin autenticación; la interfaz, los datos y las descargas deben responder 401 sin credenciales.
