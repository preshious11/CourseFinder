"""Entry point so the bot can be started with `python -m bot`.

This module has to live *inside* the ``bot`` package: the ``-m`` runner turns
``python -m bot`` into ``bot.__main__``, so a ``__main__.py`` sitting next to
the package (at the project root) is never found and Python exits with
``No module named bot.__main__; 'bot' is a package and cannot be directly
executed`` - which is exactly what Railway's start command hit.

Both of these work, as does the ``udemy-free-bot`` console script:

    python -m bot
    python -m bot.main
"""

from bot.main import main

if __name__ == "__main__":
    main()
