# -*- coding: utf-8 -*-
import os
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI, RateLimitError, APIError, APIConnectionError, APITimeoutError
from config import (
    BOT_TOKEN, CHATGPT_TOKEN, ADMIN_ID,
    OPENAI_MODEL, OPENAI_TEMPERATURE, OPENAI_MAX_TOKENS, OPENAI_TIMEOUT,
    MAX_FILE_SIZE, MAX_RESUME_LENGTH, MAX_PDF_PAGES, MIN_RESUME_LENGTH,
    MAX_REQUESTS_PER_MINUTE
)
import io

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Rate limiting: словарь для хранения запросов пользователей
user_requests = defaultdict(list)

# Импорты для обработки файлов (с обработкой ошибок)
try:
    import PyPDF2
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False
    logger.warning("PyPDF2 не установлен. Поддержка PDF файлов будет ограничена.")

try:
    from docx import Document
    DOCX_SUPPORT = True
except ImportError:
    DOCX_SUPPORT = False
    logger.warning("python-docx не установлен. Поддержка DOCX файлов будет ограничена.")

# Инициализация OpenAI клиента с таймаутом
client = OpenAI(api_key=CHATGPT_TOKEN, timeout=OPENAI_TIMEOUT)

# Глобальная переменная для приложения (будет установлена при запуске)
application_instance = None

# Загрузка промпта из файла
def load_prompt():
    try:
        with open('promt.txt', 'r', encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        logger.error("Файл promt.txt не найден")
        return None

SYSTEM_PROMPT = load_prompt()

# Дополнительные инструкции для ИИ
ADDITIONAL_INSTRUCTIONS = """
CRITICAL INSTRUCTIONS:
- You MUST return ONLY the cover letter template text
- DO NOT include any introductory text, explanations, or comments
- DO NOT say things like "Here is your cover letter:" or "Based on your resume:"
- DO NOT use markdown code blocks (```)
- DO NOT add any text before or after the template
- The template must be in English
- Include placeholders in square brackets [ ] as shown in the format
- Base the template on the resume information provided
- Start directly with the template format: [Your Name] [Your City, Country]...
"""

def check_rate_limit(user_id: int) -> bool:
    """Проверка rate limit для пользователя"""
    now = datetime.now()
    # Очищаем старые запросы (старше 1 минуты)
    user_requests[user_id] = [
        req_time for req_time in user_requests[user_id]
        if now - req_time < timedelta(minutes=1)
    ]
    
    # Проверяем лимит
    if len(user_requests[user_id]) >= MAX_REQUESTS_PER_MINUTE:
        return False
    
    # Добавляем текущий запрос
    user_requests[user_id].append(now)
    return True

def sanitize_resume_text(text: str) -> str:
    """Очистка и валидация текста резюме"""
    if len(text) > MAX_RESUME_LENGTH:
        raise ValueError(f"Resume is too long (maximum {MAX_RESUME_LENGTH} characters)")
    
    # Удаляем потенциально опасные символы
    text = text.replace('\x00', '')  # Null bytes
    text = text[:MAX_RESUME_LENGTH]  # Обрезаем до лимита
    
    return text.strip()

async def send_error_notification(error_message: str, user_info: str = "", error_type: str = "ERROR"):
    """Отправка уведомления об ошибке администратору"""
    try:
        if application_instance:
            from datetime import datetime
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            error_text = (
                f"🚨 <b>{error_type}</b>\n\n"
                f"<b>Ошибка:</b>\n<code>{error_message[:1000]}</code>\n\n"
            )
            if user_info:
                error_text += f"<b>Пользователь:</b> {user_info}\n\n"
            error_text += f"<b>Время:</b> {current_time}"
            
            await application_instance.bot.send_message(
                chat_id=ADMIN_ID,
                text=error_text,
                parse_mode='HTML'
            )
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление администратору: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    welcome_message = (
        "👋 Привет! Я бот для создания шаблонов сопроводительных писем.\n\n"
        "📄 Просто отправь мне своё резюме (текстом или файлом), "
        "и я создам для тебя персонализированный шаблон на английском языке.\n\n"
        "Шаблон будет содержать плейсхолдеры в квадратных скобках [ ], "
        "которые ты сможешь заменить на данные конкретной вакансии."
    )
    await update.message.reply_text(welcome_message)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = (
        "📋 Как использовать бота:\n\n"
        "1. Отправь своё резюме одним из способов:\n"
        "   • Скопируй текст резюме и отправь сообщением\n"
        "   • Отправь файл с резюме (PDF, DOC, DOCX, TXT)\n\n"
        "2. Бот автоматически создаст шаблон сопроводительного письма\n\n"
        "3. Шаблон будет содержать плейсхолдеры [ ], которые нужно заменить на данные вакансии\n\n"
        "💡 Совет: Чем подробнее резюме, тем лучше будет шаблон!"
    )
    await update.message.reply_text(help_text)

async def extract_text_from_file(file) -> str:
    """Извлечение текста из файла"""
    try:
        # Получаем файл
        file_obj = await file.get_file()
        
        # Проверка размера файла
        if file_obj.file_size and file_obj.file_size > MAX_FILE_SIZE:
            logger.warning(f"Файл слишком большой: {file_obj.file_size} bytes (максимум {MAX_FILE_SIZE})")
            await send_error_notification(
                f"File too large: {file_obj.file_size} bytes",
                f"File: {file.file_name if hasattr(file, 'file_name') else 'Unknown'}",
                "WARNING: File Size Exceeded"
            )
            return None
        
        file_content = await file_obj.download_as_bytearray()
        
        # Определяем тип файла
        file_name = file.file_name.lower() if file.file_name else ""
        
        if file_name.endswith('.txt'):
            return file_content.decode('utf-8', errors='ignore')
        
        elif file_name.endswith('.pdf'):
            if not PDF_SUPPORT:
                return None
            try:
                pdf_file = io.BytesIO(file_content)
                pdf_reader = PyPDF2.PdfReader(pdf_file)
                
                # Проверка количества страниц
                num_pages = len(pdf_reader.pages)
                if num_pages > MAX_PDF_PAGES:
                    logger.warning(f"PDF слишком большой: {num_pages} страниц (максимум {MAX_PDF_PAGES})")
                    await send_error_notification(
                        f"PDF too large: {num_pages} pages",
                        f"File: {file_name}",
                        "WARNING: PDF Too Large"
                    )
                    return None
                
                text = ""
                for page in pdf_reader.pages[:MAX_PDF_PAGES]:  # Ограничиваем количество страниц
                    text += page.extract_text() + "\n"
                return text.strip() if text.strip() else None
            except Exception as e:
                logger.error(f"Ошибка при чтении PDF: {e}", exc_info=True)
                # Отправляем уведомление о критической ошибке чтения PDF
                await send_error_notification(
                    f"PDF Reading Error: {type(e).__name__}\n{str(e)}",
                    f"File: {file_name}",
                    "ERROR: PDF Processing Failed"
                )
                return None
        
        elif file_name.endswith('.docx'):
            if not DOCX_SUPPORT:
                return None
            try:
                doc_file = io.BytesIO(file_content)
                doc = Document(doc_file)
                text = "\n".join([paragraph.text for paragraph in doc.paragraphs])
                return text.strip() if text.strip() else None
            except Exception as e:
                logger.error(f"Ошибка при чтении DOCX: {e}", exc_info=True)
                # Отправляем уведомление о критической ошибке чтения DOCX
                await send_error_notification(
                    f"DOCX Reading Error: {type(e).__name__}\n{str(e)}",
                    f"File: {file_name}",
                    "ERROR: DOCX Processing Failed"
                )
                return None
        
        elif file_name.endswith('.doc'):
            # Старые .doc файлы сложнее обрабатывать, просим пользователя конвертировать
            return None
        
        else:
            return None
    except Exception as e:
        logger.error(f"Ошибка при обработке файла: {e}", exc_info=True)
        # Отправляем уведомление о критической ошибке обработки файла
        file_name = file.file_name if hasattr(file, 'file_name') and file.file_name else "Unknown"
        await send_error_notification(
            f"File Processing Error: {type(e).__name__}\n{str(e)}",
            f"File: {file_name}",
            "ERROR: File Processing Failed"
        )
        return None

async def generate_cover_letter(resume_text: str, user_id: int = None, username: str = None) -> str:
    """Генерация шаблона сопроводительного письма через OpenAI"""
    try:
        if not SYSTEM_PROMPT:
            error_msg = "Error: Failed to load prompt. Please check the promt.txt file"
            await send_error_notification(
                "Failed to load prompt from promt.txt file",
                f"ID: {user_id}, Username: @{username}" if user_id else "",
                "CRITICAL: Missing Prompt File"
            )
            return error_msg
        
        # Валидация и санитизация резюме
        try:
            resume_text = sanitize_resume_text(resume_text)
        except ValueError as e:
            logger.warning(f"Валидация резюме не прошла: {e}")
            return None
        
        full_prompt = f"{SYSTEM_PROMPT}\n\n{ADDITIONAL_INSTRUCTIONS}\n\nResume:\n{resume_text}"
        
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + ADDITIONAL_INSTRUCTIONS},
                {"role": "user", "content": f"Generate a cover letter template based on this resume:\n\n{resume_text}"}
            ],
            temperature=OPENAI_TEMPERATURE,
            max_tokens=OPENAI_MAX_TOKENS,
            timeout=OPENAI_TIMEOUT
        )
        
        cover_letter = response.choices[0].message.content.strip()
        
        # Убираем возможные markdown форматирования и лишний текст
        cover_letter = cover_letter.replace('```markdown', '').replace('```', '').strip()
        
        # Удаляем возможные вводные фразы
        intro_phrases = [
            "here is your cover letter:",
            "based on your resume:",
            "here's your cover letter:",
            "cover letter template:",
            "template:"
        ]
        for phrase in intro_phrases:
            if cover_letter.lower().startswith(phrase):
                cover_letter = cover_letter[len(phrase):].strip()
                # Убираем двоеточие и пробелы в начале
                if cover_letter.startswith(':'):
                    cover_letter = cover_letter[1:].strip()
        
        # Убираем лишние пробелы и переносы в начале
        cover_letter = cover_letter.lstrip()
        
        return cover_letter
        
    except RateLimitError as e:
        error_type = "RateLimitError"
        error_message = str(e)
        notification_type = "CRITICAL: OpenAI Rate Limit"
        error_details = f"OpenAI Rate Limit Exceeded: {error_message}"
        logger.error(f"Rate limit exceeded: {e}", exc_info=True)
        
        user_info = f"ID: {user_id}, Username: @{username}" if user_id else "Unknown user"
        await send_error_notification(error_details, user_info, notification_type)
        return None
        
    except APIConnectionError as e:
        error_type = "APIConnectionError"
        error_message = str(e)
        notification_type = "CRITICAL: OpenAI Connection Error"
        error_details = f"OpenAI Connection Error: {error_message}"
        logger.error(f"Connection error: {e}", exc_info=True)
        
        user_info = f"ID: {user_id}, Username: @{username}" if user_id else "Unknown user"
        await send_error_notification(error_details, user_info, notification_type)
        return None
        
    except APITimeoutError as e:
        error_type = "APITimeoutError"
        error_message = str(e)
        notification_type = "CRITICAL: OpenAI Timeout"
        error_details = f"OpenAI API Timeout: {error_message}"
        logger.error(f"API timeout: {e}", exc_info=True)
        
        user_info = f"ID: {user_id}, Username: @{username}" if user_id else "Unknown user"
        await send_error_notification(error_details, user_info, notification_type)
        return None
        
    except APIError as e:
        error_type = type(e).__name__
        error_message = str(e)
        
        # Проверяем на ошибку региона (должна быть первой проверкой)
        is_region_blocked = (
            "unsupported_country" in error_message.lower() or 
            "country, region, or territory not supported" in error_message.lower() or
            "unsupported_country_region_territory" in error_message.lower()
        )
        
        # Определяем тип ошибки для более детального уведомления
        if is_region_blocked:
            notification_type = "CRITICAL: OpenAI API Region Blocked"
            error_details = (
                f"OpenAI API Region Blocked: {error_type}\n{error_message}\n\n"
                f"⚠️ OpenAI API недоступен в регионе пользователя.\n\n"
                f"Возможные решения:\n"
                f"1. Использовать VPN/прокси для API запросов\n"
                f"2. Использовать альтернативный API endpoint\n"
                f"3. Проверить настройки аккаунта OpenAI\n"
                f"4. Использовать другой API ключ из поддерживаемого региона"
            )
        elif "permissiondenied" in error_type.lower() or "403" in error_message.lower():
            notification_type = "CRITICAL: OpenAI API Permission Denied"
            error_details = f"OpenAI API Permission Denied (403): {error_type}\n{error_message}"
        elif "openai" in error_type.lower() or "api" in error_message.lower() or "rate limit" in error_message.lower():
            notification_type = "CRITICAL: OpenAI API Error"
            error_details = f"OpenAI API Error: {error_type}\n{error_message}"
        elif "authentication" in error_message.lower() or "invalid" in error_message.lower() or "token" in error_message.lower():
            notification_type = "CRITICAL: Authentication Error"
            error_details = f"Authentication Error: {error_type}\n{error_message}"
        else:
            notification_type = "CRITICAL: OpenAI API Error"
            error_details = f"OpenAI API Error: {error_type}\n{error_message}"
        
        logger.error(f"OpenAI API error: {e}", exc_info=True)
        
        # Отправляем уведомление администратору
        user_info = f"ID: {user_id}, Username: @{username}" if user_id else "Unknown user"
        await send_error_notification(
            error_details,
            user_info,
            notification_type
        )
        
        # Возвращаем специальное сообщение для пользователя в случае ошибки региона
        if is_region_blocked:
            return "REGION_BLOCKED"
        
        return None
        
    except ValueError as e:
        # Ошибка валидации
        logger.warning(f"Validation error: {e}")
        return None
        
    except Exception as e:
        # Неожиданные ошибки
        error_type = type(e).__name__
        error_message = str(e)
        logger.error(f"Unexpected error in generate_cover_letter: {e}", exc_info=True)
        
        user_info = f"ID: {user_id}, Username: @{username}" if user_id else "Unknown user"
        await send_error_notification(
            f"Unexpected Error: {error_type}\n{error_message}",
            user_info,
            "ERROR: Unexpected Error"
        )
        return None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    user_message = update.message.text
    
    # Проверяем, не является ли это командой
    if user_message.startswith('/'):
        return
    
    # Получаем информацию о пользователе для rate limiting
    user_id = update.effective_user.id
    username = update.effective_user.username or "N/A"
    
    # Проверка rate limit
    if not check_rate_limit(user_id):
        await update.message.reply_text(
            "⏳ Too many requests. Please wait a minute before your next request."
        )
        logger.info(f"Rate limit exceeded for user {user_id} (@{username})")
        return
    
    # Проверяем минимальную длину резюме
    if len(user_message.strip()) < MIN_RESUME_LENGTH:
        await update.message.reply_text(
            f"⚠️ Resume text is too short.\n\n"
            f"Please send a complete resume (minimum {MIN_RESUME_LENGTH} characters) "
            f"to create a quality template.\n\n"
            f"📝 Resume should include:\n"
            f"• Personal information (name, contacts)\n"
            f"• Work experience\n"
            f"• Education\n"
            f"• Skills and competencies\n\n"
            f"The more detailed the resume, the better the template will be!"
        )
        return
    
    # Отправляем сообщение о обработке
    processing_msg = await update.message.reply_text("⏳ Processing your resume and creating a template...")
    
    try:
        # Валидация и санитизация резюме
        try:
            sanitized_message = sanitize_resume_text(user_message)
        except ValueError as e:
            await processing_msg.edit_text(
                f"❌ {str(e)}\n\n"
                f"Please send a resume shorter than {MAX_RESUME_LENGTH} characters."
            )
            return
        
        # Генерируем шаблон
        cover_letter = await generate_cover_letter(sanitized_message, user_id=user_id, username=username)
        
        # Логируем успешную генерацию
        logger.info(f"User {user_id} (@{username}) successfully generated cover letter")
        
        if cover_letter == "REGION_BLOCKED":
            # Специальная обработка ошибки региона
            await processing_msg.edit_text(
                "❌ Unfortunately, the OpenAI API service is not available in your region.\n\n"
                "This is a limitation from OpenAI. To resolve the issue:\n"
                "• Use a VPN\n"
                "• Contact the bot administrator\n\n"
                "Sorry for the inconvenience."
            )
        elif cover_letter:
            # Удаляем сообщение о обработке
            await processing_msg.delete()
            
            # Отправляем результат
            if len(cover_letter) <= 4096:
                await update.message.reply_text(cover_letter)
            else:
                # Если текст слишком длинный, разбиваем на части
                parts = [cover_letter[i:i+4096] for i in range(0, len(cover_letter), 4096)]
                for part in parts:
                    await update.message.reply_text(part)
        else:
            await processing_msg.edit_text(
                "❌ An error occurred while generating the template. "
                "Please try again or send the resume in a different format."
            )
            
    except Exception as e:
        error_type = type(e).__name__
        error_message = str(e)
        logger.error(f"Ошибка в handle_message: {e}", exc_info=True)
        
        # Отправляем уведомление администратору о критической ошибке
        user_id = update.effective_user.id if update.effective_user else None
        username = update.effective_user.username if update.effective_user else "N/A"
        user_info = f"ID: {user_id}, Username: @{username}"
        
        await send_error_notification(
            f"Message Processing Error: {error_type}\n{error_message}",
            user_info,
            "ERROR: Message Processing Failed"
        )
        
        await processing_msg.edit_text(
            "❌ An error occurred. Please try again."
        )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик документов"""
    document = update.message.document
    
    # Получаем информацию о пользователе для rate limiting
    user_id = update.effective_user.id
    username = update.effective_user.username or "N/A"
    
    # Проверка rate limit
    if not check_rate_limit(user_id):
        await update.message.reply_text(
            "⏳ Too many requests. Please wait a minute before your next request."
        )
        logger.info(f"Rate limit exceeded for user {user_id} (@{username})")
        return
    
    # Проверяем тип файла
    if document.file_name:
        file_ext = document.file_name.lower().split('.')[-1]
        if file_ext not in ['txt', 'pdf', 'docx']:
            if file_ext == 'doc':
                await update.message.reply_text(
                    "📄 DOC format files (old Word format) are not supported.\n"
                    "Please convert the file to DOCX or PDF, "
                    "or send the resume as text."
                )
            else:
                await update.message.reply_text(
                    "📄 Please send the resume in TXT, PDF, or DOCX format.\n"
                    "Or simply copy the resume text and send it as a message."
                )
            return
    
    # Отправляем сообщение о обработке
    processing_msg = await update.message.reply_text("⏳ Processing the file and creating a template...")
    
    try:
        # Извлекаем текст из файла
        resume_text = await extract_text_from_file(document)
        
        if not resume_text:
            await processing_msg.edit_text(
                "❌ Failed to extract text from the file. "
                "Possible reasons:\n"
                "• File is corrupted or protected\n"
                "• File is in an unsupported format\n\n"
                "Please send the resume as text or try a different file."
            )
            return
        
        if len(resume_text) < MIN_RESUME_LENGTH:
            await processing_msg.edit_text(
                f"⚠️ Text in the file is too short.\n\n"
                f"Please make sure the file contains a complete resume (minimum {MIN_RESUME_LENGTH} characters).\n\n"
                f"📝 Resume should include:\n"
                f"• Personal information (name, contacts)\n"
                f"• Work experience\n"
                f"• Education\n"
                f"• Skills and competencies\n\n"
                f"The more detailed the resume, the better the template will be!"
            )
            return
        
        # Валидация и санитизация резюме из файла
        try:
            resume_text = sanitize_resume_text(resume_text)
        except ValueError as e:
            await processing_msg.edit_text(
                f"❌ {str(e)}\n\n"
                f"Please send a resume shorter than {MAX_RESUME_LENGTH} characters."
            )
            return
        
        # Получаем информацию о пользователе
        user_id = update.effective_user.id
        username = update.effective_user.username or "N/A"
        
        # Генерируем шаблон
        cover_letter = await generate_cover_letter(resume_text, user_id=user_id, username=username)
        
        # Логируем успешную генерацию
        logger.info(f"User {user_id} (@{username}) successfully generated cover letter from file")
        
        if cover_letter == "REGION_BLOCKED":
            # Специальная обработка ошибки региона
            await processing_msg.edit_text(
                "❌ Unfortunately, the OpenAI API service is not available in your region.\n\n"
                "This is a limitation from OpenAI. To resolve the issue:\n"
                "• Use a VPN\n"
                "• Contact the bot administrator\n\n"
                "Sorry for the inconvenience."
            )
        elif cover_letter:
            # Удаляем сообщение о обработке
            await processing_msg.delete()
            
            # Отправляем результат
            if len(cover_letter) <= 4096:
                await update.message.reply_text(cover_letter)
            else:
                # Если текст слишком длинный, разбиваем на части
                parts = [cover_letter[i:i+4096] for i in range(0, len(cover_letter), 4096)]
                for part in parts:
                    await update.message.reply_text(part)
        else:
            await processing_msg.edit_text(
                "❌ An error occurred while generating the template. "
                "Please try sending the resume as text."
            )
            
    except Exception as e:
        error_type = type(e).__name__
        error_message = str(e)
        logger.error(f"Ошибка в handle_document: {e}", exc_info=True)
        
        # Отправляем уведомление администратору о критической ошибке
        user_id = update.effective_user.id if update.effective_user else None
        username = update.effective_user.username if update.effective_user else "N/A"
        user_info = f"ID: {user_id}, Username: @{username}"
        
        await send_error_notification(
            f"File Processing Error: {error_type}\n{error_message}",
            user_info,
            "ERROR: File Processing Failed"
        )
        
        await processing_msg.edit_text(
            "❌ An error occurred while processing the file. "
            "Please try sending the resume as text."
        )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик фотографий (резюме может быть отправлено как фото)"""
    await update.message.reply_text(
        "📸 I see you sent a photo. "
        "Unfortunately, I cannot process images yet.\n\n"
        "Please send your resume in one of the following ways:\n"
        "• Copy the resume text and send it as a message\n"
        "• Send a resume file (PDF, DOC, DOCX, TXT)"
    )

async def handle_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик неизвестных типов сообщений"""
    await update.message.reply_text(
        "🤔 I cannot process this type of message.\n\n"
        "Please send your resume in one of the following ways:\n"
        "• Copy the resume text and send it as a message\n"
        "• Send a resume file (PDF, DOC, DOCX, TXT)\n\n"
        "Use /help for detailed information."
    )

def main():
    """Основная функция запуска бота"""
    global application_instance
    
    # Создаём приложение
    application = Application.builder().token(BOT_TOKEN).build()
    application_instance = application
    
    # Регистрируем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    
    # Регистрируем обработчики сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    # Обработчик для всех остальных типов сообщений
    application.add_handler(MessageHandler(filters.ALL, handle_unknown))
    
    # Запускаем бота
    logger.info("Бот запущен...")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске бота: {e}", exc_info=True)
        # Попытка отправить уведомление (если бот уже инициализирован)
        if application_instance:
            import asyncio
            try:
                asyncio.run(send_error_notification(
                    f"Critical bot startup error: {type(e).__name__}\n{str(e)}",
                    "",
                    "CRITICAL: Bot Startup Failed"
                ))
            except:
                pass

if __name__ == '__main__':
    main()

