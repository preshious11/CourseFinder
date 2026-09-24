"""Lightweight translations for user-facing bot messages.

Only strings that change with *language* live here; everything else stays in
the handler code.  Add a new language by adding a top-level dict — missing
keys fall back to English automatically.
"""

from __future__ import annotations

from typing import Any

_EN: dict[str, str] = {
    "welcome": (
        "🎓✨ <b>Free Udemy Course Bot</b> ✨🎓\n\n"
        "👋 Welcome! I find free courses for you.\n"
        "📢 You'll get notified whenever I spot a new deal.\n\n"
        "🏷️ <b>Quick commands</b>\n"
        "🔍 /search python — search all platforms\n"
        "🔍 /search python coursera — search one platform\n"
        "📋 /latest — most recent finds\n"
        "🔖 /filter python, design — narrow what you see\n"
        "❌ /filter off — see everything\n"
        "🔔 /freq daily — change notification frequency\n"
        "📊 /status — stats\n"
        "🚪 /stop — unsubscribe"
    ),
    "already_subscribed": "You're already subscribed. ",
    "unsubscribed": "Unsubscribed. Send /start any time to come back.",
    "not_subscribed": "You weren't subscribed.",
    "send_start_first": "Send /start first.",
    "filter_removed": "Filter removed — you'll get everything.",
    "nothing_found": "Nothing found yet - I'll message you as soon as I do.",
    "no_matching": "No matching recent finds.",
    "search_no_query": (
        "What should I look for?\n\n"
        "Example: /search python\n"
        "By platform: /search python coursera"
    ),
    "error_generic": "Something went wrong. Try again shortly.",
    "rate_limited": "You're sending commands too quickly. Try again in {seconds:.0f}s.",
    "freq_usage": (
        "Current frequency: <b>{current}</b>\n\n"
        "Set with: /freq instant | daily | weekly | paused"
    ),
    "freq_set": "Notification frequency set to: {frequency}",
    "freq_invalid": "Invalid frequency. Choose: instant, daily, weekly, paused.",
    "digest_header": "📚 <b>Daily digest</b> — {count} new course(s)\n",
    "status_header": "🎓 <b>Bot Status</b>\n",
}

_ES: dict[str, str] = {
    "welcome": (
        "🎓✨ <b>Bot de Cursos Udemy Gratis</b> ✨🎓\n\n"
        "👋 ¡Bienvenido! Encuentro cursos gratis para ti.\n"
        "📢 Te avisaré cuando encuentre una nueva oferta.\n\n"
        "🏷️ <b>Comandos rápidos</b>\n"
        "🔍 /search python — buscar en todas las plataformas\n"
        "🔍 /search python coursera — buscar en una plataforma\n"
        "📋 /latest — hallazgos recientes\n"
        "🔖 /filter python, design — filtrar lo que ves\n"
        "❌ /filter off — ver todo\n"
        "🔔 /freq daily — cambiar frecuencia de notificaciones\n"
        "📊 /status — estadísticas\n"
        "🚪 /stop — darte de baja"
    ),
    "already_subscribed": "Ya estás suscrito. ",
    "unsubscribed": "Dado de baja. Envía /start cuando quieras volver.",
    "not_subscribed": "No estabas suscrito.",
    "send_start_first": "Envía /start primero.",
    "filter_removed": "Filtro eliminado: recibirás todo.",
    "nothing_found": "Aún no hay resultados: te avisaré en cuanto encuentre algo.",
    "no_matching": "Sin resultados recientes coincidentes.",
    "search_no_query": (
        "¿Qué debo buscar?\n\n"
        "Ejemplo: /search python\n"
        "Por plataforma: /search python coursera"
    ),
    "error_generic": "Algo salió mal. Inténtalo de nuevo en un momento.",
    "rate_limited": "Envías comandos demasiado rápido. Reintenta en {seconds:.0f}s.",
    "freq_usage": (
        "Frecuencia actual: <b>{current}</b>\n\n"
        "Cambiar con: /freq instant | daily | weekly | paused"
    ),
    "freq_set": "Frecuencia de notificaciones establecida en: {frequency}",
    "freq_invalid": "Frecuencia no válida. Elige: instant, daily, weekly, paused.",
    "digest_header": "📚 <b>Resumen diario</b> — {count} curso(s) nuevo(s)\n",
    "status_header": "🎓 <b>Estado del Bot</b>\n",
}

_TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": _EN,
    "es": _ES,
}


def t(key: str, language: str = "en", **kwargs: Any) -> str:
    """Translate *key* into *language*, falling back to English.

    Keyword arguments are formatted into the template.
    """
    table = _TRANSLATIONS.get(language, _EN)
    template = table.get(key, _EN.get(key, key))
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError):
            return template
    return template


def available_languages() -> list[str]:
    return sorted(_TRANSLATIONS)
