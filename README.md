# 🎬 Video PRO Studio

**Sube la grabación de tu vídeo y sale editado de punta a punta, con TU marca, en TU PC y gratis.**
Cortes y silencios, zoom entre tu cara y tu pantalla, tarjetas animadas, ilustraciones, escenas a pantalla completa, música de fondo, título, miniaturas, capítulos, descripción, Shorts, carruseles, posts para redes… y todo se puede encender, apagar y personalizar para **tu** canal.

> La IA de edición es Gemini (gratis con tu propia cuenta de Google). La transcripción y el montaje corren en tu ordenador. Nada de tus vídeos sale de tu PC, salvo el texto que le mandas a Gemini.

---

## ⬇️ Instalar (3 pasos, unos 10 minutos)

Necesitas **Windows 10 u 11**. No hace falta saber programar.

1. **Descarga** el programa: en esta página de GitHub pulsa el botón verde **`Code` → `Download ZIP`** y descomprímelo donde quieras (por ejemplo `Documentos\Video PRO Studio`).
2. Haz **doble clic en `INSTALAR.bat`**. Instala solo lo que falte (Python, ffmpeg y las librerías), te va diciendo qué hace y crea un acceso directo en tu escritorio.
   *Si Windows avisa de «aplicación no reconocida», pulsa «Más información → Ejecutar de todas formas».*
3. Haz **doble clic en `ABRIR PANEL.cmd`** (o en el acceso directo). Se abre el panel en tu navegador y **te guía paso a paso** (ver abajo).

Para apagarlo: cierra la ventana negra o usa `CERRAR PANEL.cmd`. Para actualizar a una versión nueva: `ACTUALIZAR.bat` (si lo instalaste con `git clone`) o baja el ZIP nuevo y cópialo encima (tu configuración no se toca).

### Qué necesita tu PC
| | Mínimo | Recomendado |
|---|---|---|
| Sistema | Windows 10/11 | Windows 11 |
| Memoria | 8 GB | 16 GB o más |
| Disco | 10 GB libres + ~2 GB por vídeo | SSD |
| Tarjeta gráfica | no hace falta | **NVIDIA** (la transcripción y el render van 5-10× más rápido) |

Sin tarjeta NVIDIA funciona igual, solo que tarda más: usa el modelo de transcripción `large-v3-turbo` o `medium` (en `config.yaml` → `transcribe.model`).

---

## 🚀 La primera vez: el panel te guía

Al abrir el panel por primera vez aparece **Primeros pasos**:

1. **Tu clave de Gemini** (obligatoria, gratis): la sacas en [AI Studio](https://aistudio.google.com/api-keys) y la pegas. El programa la prueba con Google antes de guardarla. *No actives la facturación de Google: con el plan gratis, si se acaba un modelo sigue con el siguiente y nunca cobra.*
2. **Cloudflare** (opcional, gratis): solo para las **miniaturas con IA** (unas 60 al día). Sin esto todo lo demás funciona.
3. **Tu canal**: un asistente de 6 pasos donde lo configuras **todo**:
   - de qué trata tu canal, a quién le hablas y con qué tono (esto es lo que lee la IA cada vez que edita un vídeo);
   - tus **colores y tus letras** (con vista previa real de tus tarjetas);
   - tus **enlaces y redes** para la descripción de YouTube, tu llamado a la acción y tus hashtags;
   - **dónde sale tu cámara** en la grabación (la marcas arrastrando sobre un fotograma);
   - **qué ediciones quieres**: 24 interruptores (escenas a pantalla completa, tarjetas, ilustraciones, palabras clave, Shorts, miniaturas con IA, música…) con atajos «Todo», «Equilibrado» y «Solo lo esencial»;
   - tu **jerga y tus herramientas** (para que la transcripción las escriba bien) y, si quieres, tu **cara, retrato y logo** (para miniaturas, carruseles y posts).
4. **Tu YouTube** (opcional): para publicar y programar desde el panel y ver tus métricas. Cada persona usa **su propio proyecto de Google Cloud** (gratis): el botón del panel te lleva paso a paso. Truco: en Google Cloud pulsa **Publish app** para que el permiso no caduque cada 7 días.
5. **¡A editar!** Sube tu primera grabación en *Mis vídeos*.

Puedes crear **varios canales** (cada uno con su marca, sus enlaces y sus ediciones) y cambiarlos cuando quieras con «Editar canal».

---

## 🧩 Qué hace

- **Vídeos PRO**: recorta relleno y silencios, elige cuándo ver tu cara y cuándo tu pantalla (con zoom que siempre enseña todo lo que haces con el ratón), tarjetas y palabras clave que salen justo cuando las dices, ilustraciones animadas, escenas a pantalla completa, música estable de fondo, título A/B/C, miniaturas, capítulos y descripción con tus enlaces. Salida 1080p60.
- **Mejorar con IA**: dile en una frase qué cambiar y sale una versión nueva.
- **Shorts · TikTok**: un vídeo vertical muy editado (silencios fuera, animaciones, subtítulos, sonido) con títulos para YouTube, TikTok e Instagram.
- **Ideas de vídeos** con datos reales de YouTube, **artículos para tu web**, **carruseles** y **posts** para redes, **miniaturas** e **imágenes destacadas** con IA.
- **Publicar en YouTube**: programar, miniaturas A/B/C, listas de reproducción y métricas.
- **Probar el sistema** (pestaña *Sistema*): comprueba que todo funciona (ffmpeg, GPU, Gemini, render de prueba).

## 🎨 Personalízalo todo

| Quieres cambiar | Dónde |
|---|---|
| Colores, letras, enlaces, cámara, ediciones de un canal | Panel → **Editar canal** |
| Tu cara, retrato y logo | Panel → Editar canal → paso 6 (se guardan en `mis_caras/` y `mi_marca/`) |
| Tu música de fondo | Pon tus `.mp3` en la carpeta `musica_fondo/` |
| Tus letras | Copia el `.ttf` a `fonts/` y aparece en el asistente |
| Cualquier ajuste técnico (calidad, motor GPU/CPU, topes…) | `config.yaml` — mejor copia solo lo que cambies a **`config.local.yaml`**: así no se pisa al actualizar |

Los canales y tus ajustes están en `canales/` y `config.local.yaml`, que **nunca** se suben a Git.

## 🔒 Privacidad y claves

- Tus claves (Gemini, Cloudflare) se guardan en el archivo `.env` de **tu** carpeta; el token de YouTube, en `work/`. Nada de eso se sube a Git ni sale de tu PC.
- En el plan gratis de Gemini, Google puede usar lo que le mandas (la transcripción del vídeo) para mejorar sus productos: vale para vídeos que vas a publicar, no para material confidencial.
- Límites gratis (los pone Google/Cloudflare, no el programa): unas 20 peticiones al día por cada modelo Flash y 500 por Flash-Lite. La cabecera del panel muestra siempre **cuánto te queda hoy**.

## 🛟 Problemas frecuentes

- **No abre / se cierra solo**: mira `work/panel.log`, o ejecuta *Sistema → Probar el sistema*.
- **Va lento**: sin NVIDIA es normal. Elige un modelo más pequeño de Whisper (`transcribe.model: medium`).
- **«No se encontró ffmpeg»**: cierra y vuelve a abrir `INSTALAR.bat` (lo instala con winget).
- **YouTube pide reconectar cada semana**: en Google Cloud → *Audience* → **Publish app**.

## 📜 Licencia

[PolyForm Shield 1.0.0](LICENSE): **gratis para usar, incluso en tu canal monetizado o en tu negocio**, y puedes modificarlo y compartirlo, **pero no puedes venderlo ni ofrecer un producto que compita con él** (por ejemplo, una versión de pago o un servicio de edición basado en este código).

Componentes de terceros: letra [Rubik](https://github.com/googlefonts/rubik) (OFL), icono de «me gusta» de Material Design (Apache 2.0), icono oficial de YouTube (uso según las [normas de marca de YouTube](https://www.youtube.com/howyoutubeworks/resources/brand-resources/)), faster-whisper, yt-dlp, FFmpeg, Flask y Pillow, cada uno con su licencia.

---

<sub>Hecho con ❤️ para creadores · [github.com/eljoseom-bot/Video-PRO-Studio](https://github.com/eljoseom-bot/Video-PRO-Studio)</sub>
