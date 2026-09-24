"""Lightweight translations for user-facing bot messages.

Only strings that change with *language* live here; everything else stays in
the handler code.  Add a new language by adding a top-level dict — missing
keys fall back to English automatically.
"""

from __future__ import annotations

from typing import Any

_EN: dict[str, str] = {
    "welcome": (
        "👋 <b>Welcome, {username}!</b>\n"
        "──────────────────────\n"
        "I track free Udemy deals and new courses from\n"
        "Coursera, edX, MIT &amp; freeCodeCamp.\n\n"

        "🔔 <b>Notifications</b>\n"
        "New finds are pushed to you automatically.\n"
        "Use /filter to narrow topics, /freq to change how often.\n\n"

        "🔍 <b>Two ways to search</b>\n\n"
        "<b>1 · In this chat</b>\n"
        "<code>/search python</code>\n"
        "<code>/search python coursera</code>  (one platform)\n\n"
        "<b>2 · From any chat (inline)</b>\n"
        "Type <code>@{bot_username} python</code>\n"
        "anywhere on Telegram — results appear as cards\n"
        "you can forward without leaving that chat.\n\n"
        "──────────────────────\n"
        "📋 /latest · /course · /categories\n"
        "🔖 /filter · 🔔 /freq · 📊 /status\n"
        "🚪 /stop to unsubscribe · /help for everything"
    ),
    "already_subscribed": "✅ You're already subscribed.\n\n",
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
        "👋 <b>¡Bienvenido, {username}!</b>\n"
        "──────────────────────\n"
        "Rastreo ofertas de Udemy y cursos nuevos de\n"
        "Coursera, edX, MIT &amp; freeCodeCamp.\n\n"

        "🔔 <b>Notificaciones</b>\n"
        "Te aviso automáticamente de los nuevos hallazgos.\n"
        "Usa /filter para filtrar temas, /freq para cambiar la frecuencia.\n\n"

        "🔍 <b>Dos formas de buscar</b>\n\n"
        "<b>1 · En este chat</b>\n"
        "<code>/search python</code>\n"
        "<code>/search python coursera</code>  (una plataforma)\n\n"
        "<b>2 · Desde cualquier chat (inline)</b>\n"
        "Escribe <code>@{bot_username} python</code>\n"
        "en cualquier parte de Telegram — los resultados aparecen\n"
        "como tarjetas que puedes reenviar sin salir de ese chat.\n\n"
        "──────────────────────\n"
        "📋 /latest · /course · /categories\n"
        "🔖 /filter · 🔔 /freq · 📊 /status\n"
        "🚪 /stop para darte de baja · /help para todo"
    ),
    "already_subscribed": "✅ Ya estás suscrito.\n\n",
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
