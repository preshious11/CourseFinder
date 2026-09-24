from bot.i18n import available_languages, t


class TestI18n:
    def test_english_default(self):
        assert t("not_subscribed") == "You weren't subscribed."

    def test_spanish(self):
        assert t("not_subscribed", "es") == "No estabas suscrito."

    def test_fallback_to_english(self):
        # de is not a registered language
        assert t("not_subscribed", "de") == "You weren't subscribed."

    def test_missing_key_returns_key(self):
        assert t("totally_unknown_key") == "totally_unknown_key"

    def test_format_kwargs(self):
        result = t("freq_set", "en", frequency="daily")
        assert "daily" in result

    def test_format_kwargs_spanish(self):
        result = t("freq_set", "es", frequency="weekly")
        assert "weekly" in result

    def test_bad_format_kwargs_returns_template(self):
        # Missing required kwarg — should not raise
        result = t("freq_set", "en")
        assert "{frequency}" in result or "frequency" in result

    def test_available_languages(self):
        langs = available_languages()
        assert "en" in langs
        assert "es" in langs

    def test_all_keys_present_in_all_languages(self):
        from bot.i18n import _EN, _TRANSLATIONS
        for lang, table in _TRANSLATIONS.items():
            for key in _EN:
                assert key in table, f"missing key {key!r} in language {lang!r}"
