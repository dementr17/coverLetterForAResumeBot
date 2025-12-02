# -*- coding: utf-8 -*-
import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI
from secrets import BOT_TOKEN, CHATGPT_TOKEN
import io

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

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

# ID администратора для уведомлений об ошибках
ADMIN_ID = 292730940

# Инициализация OpenAI клиента
client = OpenAI(api_key=CHATGPT_TOKEN)

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
                text = ""
                for page in pdf_reader.pages:
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
            error_msg = "Ошибка: не удалось загрузить промпт. Пожалуйста, проверьте файл promt.txt"
            await send_error_notification(
                "Не удалось загрузить промпт из файла promt.txt",
                f"ID: {user_id}, Username: @{username}" if user_id else "",
                "CRITICAL: Missing Prompt File"
            )
            return error_msg
        
        full_prompt = f"{SYSTEM_PROMPT}\n\n{ADDITIONAL_INSTRUCTIONS}\n\nResume:\n{resume_text}"
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + ADDITIONAL_INSTRUCTIONS},
                {"role": "user", "content": f"Generate a cover letter template based on this resume:\n\n{resume_text}"}
            ],
            temperature=0.7,
            max_tokens=1000
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
        
    except Exception as e:
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
            notification_type = "ERROR: Generation Failed"
            error_details = f"{error_type}: {error_message}"
        
        logger.error(f"Ошибка при генерации письма: {e}", exc_info=True)
        
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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    user_message = update.message.text
    
    # Проверяем, не является ли это командой
    if user_message.startswith('/'):
        return
    
    # Проверяем минимальную длину резюме
    if len(user_message.strip()) < 50:
        await update.message.reply_text(
            "⚠️ Текст резюме слишком короткий. "
            "Пожалуйста, отправь полное резюме (минимум 50 символов) "
            "для создания качественного шаблона."
        )
        return
    
    # Отправляем сообщение о обработке
    processing_msg = await update.message.reply_text("⏳ Обрабатываю твоё резюме и создаю шаблон...")
    
    try:
        # Получаем информацию о пользователе
        user_id = update.effective_user.id
        username = update.effective_user.username or "N/A"
        
        # Генерируем шаблон
        cover_letter = await generate_cover_letter(user_message, user_id=user_id, username=username)
        
        if cover_letter == "REGION_BLOCKED":
            # Специальная обработка ошибки региона
            await processing_msg.edit_text(
                "❌ К сожалению, сервис OpenAI API недоступен в вашем регионе.\n\n"
                "Это ограничение со стороны OpenAI. Для решения проблемы:\n"
                "• Используйте VPN\n"
                "• Обратитесь к администратору бота\n\n"
                "Извините за неудобства."
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
                "❌ Произошла ошибка при генерации шаблона. "
                "Пожалуйста, попробуй ещё раз или отправь резюме в другом формате."
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
            "❌ Произошла ошибка. Пожалуйста, попробуй ещё раз."
        )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик документов"""
    document = update.message.document
    
    # Проверяем тип файла
    if document.file_name:
        file_ext = document.file_name.lower().split('.')[-1]
        if file_ext not in ['txt', 'pdf', 'docx']:
            if file_ext == 'doc':
                await update.message.reply_text(
                    "📄 Файлы формата DOC (старый формат Word) не поддерживаются.\n"
                    "Пожалуйста, конвертируй файл в DOCX или PDF, "
                    "или отправь резюме текстом."
                )
            else:
                await update.message.reply_text(
                    "📄 Пожалуйста, отправь резюме в формате TXT, PDF или DOCX.\n"
                    "Или просто скопируй текст резюме и отправь сообщением."
                )
            return
    
    # Отправляем сообщение о обработке
    processing_msg = await update.message.reply_text("⏳ Обрабатываю файл и создаю шаблон...")
    
    try:
        # Извлекаем текст из файла
        resume_text = await extract_text_from_file(document)
        
        if not resume_text:
            await processing_msg.edit_text(
                "❌ Не удалось извлечь текст из файла. "
                "Возможные причины:\n"
                "• Файл повреждён или защищён\n"
                "• Файл в неподдерживаемом формате\n\n"
                "Пожалуйста, отправь резюме текстом или попробуй другой файл."
            )
            return
        
        if len(resume_text) < 50:
            await processing_msg.edit_text(
                "⚠️ Текст в файле слишком короткий. "
                "Пожалуйста, убедись, что файл содержит полное резюме."
            )
            return
        
        # Получаем информацию о пользователе
        user_id = update.effective_user.id
        username = update.effective_user.username or "N/A"
        
        # Генерируем шаблон
        cover_letter = await generate_cover_letter(resume_text, user_id=user_id, username=username)
        
        if cover_letter == "REGION_BLOCKED":
            # Специальная обработка ошибки региона
            await processing_msg.edit_text(
                "❌ К сожалению, сервис OpenAI API недоступен в вашем регионе.\n\n"
                "Это ограничение со стороны OpenAI. Для решения проблемы:\n"
                "• Используйте VPN\n"
                "• Обратитесь к администратору бота\n\n"
                "Извините за неудобства."
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
                "❌ Произошла ошибка при генерации шаблона. "
                "Пожалуйста, попробуй отправить резюме текстом."
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
            "❌ Произошла ошибка при обработке файла. "
            "Пожалуйста, попробуй отправить резюме текстом."
        )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик фотографий (резюме может быть отправлено как фото)"""
    await update.message.reply_text(
        "📸 Я вижу, что ты отправил фото. "
        "К сожалению, я пока не умею обрабатывать изображения.\n\n"
        "Пожалуйста, отправь резюме одним из способов:\n"
        "• Скопируй текст резюме и отправь сообщением\n"
        "• Отправь файл с резюме (PDF, DOC, DOCX, TXT)"
    )

async def handle_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик неизвестных типов сообщений"""
    await update.message.reply_text(
        "🤔 Я не могу обработать этот тип сообщения.\n\n"
        "Пожалуйста, отправь резюме одним из способов:\n"
        "• Скопируй текст резюме и отправь сообщением\n"
        "• Отправь файл с резюме (PDF, DOC, DOCX, TXT)\n\n"
        "Используй /help для получения подробной информации."
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

