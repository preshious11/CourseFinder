#!/usr/bin/env python
from setuptools import find_packages, setup

setup(
    name="udemy-free-bot",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        "python-telegram-bot[job-queue]>=21.0",
        "httpx>=0.27",
        "python-dotenv>=1.0",
        "feedparser>=6.0",
        "APScheduler>=3.0",
    ],
    python_requires=">=3.10",
    entry_points={
        "console_scripts": [
            "udemy-free-bot=bot.main:main",
        ],
    },
)
