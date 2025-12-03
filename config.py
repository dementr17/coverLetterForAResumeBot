# -*- coding: utf-8 -*-
"""
Конфигурация бота
Использует переменные окружения с fallback на secrets.py для обратной совместимости
"""
import os
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла
load_dotenv()

# API Keys - приоритет переменным окружения
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHATGPT_TOKEN = os.getenv('CHATGPT_TOKEN')

# Fallback на secrets.py для обратной совместимости
if not BOT_TOKEN or not CHATGPT_TOKEN:
    try:
        from secrets import BOT_TOKEN as SECRETS_BOT_TOKEN, CHATGPT_TOKEN as SECRETS_CHATGPT_TOKEN
        BOT_TOKEN = BOT_TOKEN or SECRETS_BOT_TOKEN
        CHATGPT_TOKEN = CHATGPT_TOKEN or SECRETS_CHATGPT_TOKEN
    except ImportError:
        pass

# Валидация обязательных токенов
if not BOT_TOKEN or not CHATGPT_TOKEN:
    raise ValueError(
        "BOT_TOKEN и CHATGPT_TOKEN должны быть установлены в переменных окружения "
        "или в файле secrets.py"
    )

# Bot Settings
ADMIN_ID = int(os.getenv('ADMIN_ID', '292730940'))
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
OPENAI_TEMPERATURE = float(os.getenv('OPENAI_TEMPERATURE', '0.7'))
OPENAI_MAX_TOKENS = int(os.getenv('OPENAI_MAX_TOKENS', '1000'))
OPENAI_TIMEOUT = float(os.getenv('OPENAI_TIMEOUT', '30.0'))

# File Limits
MAX_FILE_SIZE = int(os.getenv('MAX_FILE_SIZE', str(10 * 1024 * 1024)))  # 10MB
MAX_RESUME_LENGTH = int(os.getenv('MAX_RESUME_LENGTH', '50000'))  # 50KB
MAX_PDF_PAGES = int(os.getenv('MAX_PDF_PAGES', '50'))
MIN_RESUME_LENGTH = int(os.getenv('MIN_RESUME_LENGTH', '50'))

# Rate Limiting
MAX_REQUESTS_PER_MINUTE = int(os.getenv('MAX_REQUESTS_PER_MINUTE', '5'))

