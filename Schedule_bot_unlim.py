import json
import os
import warnings
from pathlib import Path
from typing import Dict, List, Any

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.warnings import PTBUserWarning
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# =========================
# 0) Убираем PTB предупреждения (это не ошибки)
# =========================
warnings.filterwarnings("ignore", category=PTBUserWarning)

# =========================
# 1) Token from .env
# =========================
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# =========================
# 2) Schedule settings
# =========================
DATA_FILE = Path("schedules.json")

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]
WEEKDAY_RU = {
    "monday": "Понедельник",
    "tuesday": "Вторник",
    "wednesday": "Среда",
    "thursday": "Четверг",
    "friday": "Пятница",
}
PAIR_COUNT = 4

# =========================
# 3) Conversation states
# =========================
(
    STATE_MENU,
    STATE_VIEW_DAY,
    STATE_BUILD_DAY,
    STATE_BUILD_SLOT,
    STATE_BUILD_TEXT,
    STATE_EDIT_DAY,
    STATE_EDIT_SLOT,
    STATE_EDIT_TEXT,
) = range(8)


# =========================
# 4) Storage (multi-user JSON)
# =========================
def default_schedule() -> Dict[str, List[str]]:
    return {d: ["—"] * PAIR_COUNT for d in WEEKDAYS}


def _normalize_schedule(raw: Any) -> Dict[str, List[str]]:
    if not isinstance(raw, dict):
        raw = {}
    out: Dict[str, List[str]] = {}
    for d in WEEKDAYS:
        day_list = raw.get(d)
        if not isinstance(day_list, list):
            day_list = ["—"] * PAIR_COUNT
        out[d] = (day_list + ["—"] * PAIR_COUNT)[:PAIR_COUNT]
    return out


def load_all() -> Dict[str, Dict[str, List[str]]]:
    if not DATA_FILE.exists():
        return {}
    try:
        with DATA_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_all(data: Dict[str, Dict[str, List[str]]]) -> None:
    with DATA_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_user_schedule(user_id: int) -> Dict[str, List[str]]:
    all_data = load_all()
    key = str(user_id)

    if key not in all_data:
        all_data[key] = default_schedule()
        save_all(all_data)
        return all_data[key]

    all_data[key] = _normalize_schedule(all_data[key])
    save_all(all_data)
    return all_data[key]


def set_user_schedule(user_id: int, schedule: Dict[str, List[str]]) -> None:
    all_data = load_all()
    key = str(user_id)
    all_data[key] = _normalize_schedule(schedule)
    save_all(all_data)


def set_user_day_slot(user_id: int, day: str, slot_index: int, value: str) -> Dict[str, List[str]]:
    schedule = get_user_schedule(user_id)
    schedule[day][slot_index] = value if value else "—"
    set_user_schedule(user_id, schedule)
    return schedule


# =========================
# 5) UI keyboards
# =========================
def kb_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Узнать расписание", callback_data="menu:view")],
            [InlineKeyboardButton("Составить расписание", callback_data="menu:build")],
            [InlineKeyboardButton("Редактировать расписание", callback_data="menu:edit")],
        ]
    )


def kb_weekdays(prefix: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(WEEKDAY_RU[d], callback_data=f"{prefix}:{d}")] for d in WEEKDAYS]
    rows.append([InlineKeyboardButton("Меню", callback_data="menu:back")])
    return InlineKeyboardMarkup(rows)


def kb_slots(prefix: str, day: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"{i+1} пара", callback_data=f"{prefix}:{day}:{i}")]
            for i in range(PAIR_COUNT)]
    rows.append([InlineKeyboardButton("Назад", callback_data=f"{prefix}:back:{day}")])
    rows.append([InlineKeyboardButton("Меню", callback_data="menu:back")])
    return InlineKeyboardMarkup(rows)


def kb_back_to_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Меню", callback_data="menu:back")]])


# =========================
# 6) Helpers (safe edit)
# =========================
async def safe_edit_message(query, text: str, reply_markup=None) -> None:
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


# =========================
# 7) Formatting
# =========================
def format_day(schedule: Dict[str, List[str]], day: str) -> str:
    pairs = schedule.get(day, ["—"] * PAIR_COUNT)
    lines = [f"📅 {WEEKDAY_RU[day]}"]
    for idx, item in enumerate(pairs, start=1):
        lines.append(f"{idx}) {item}")
    lines.append("")
    lines.append("Суббота и воскресенье — выходной.")
    return "\n".join(lines)


# =========================
# 8) Commands
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    _ = get_user_schedule(user_id)

    await update.message.reply_text(
        "Меню.\nУ каждого пользователя своё расписание (Пн–Пт, 4 пары).",
        reply_markup=kb_menu(),
    )
    return STATE_MENU


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Команды:\n/start — меню\n/help — помощь\n\n"
        "Расписание персональное для каждого пользователя.\n"
        "Пн–Пт: 4 пары. Сб/Вс: выходной.",
        reply_markup=kb_menu(),
    )


# =========================
# 9) Menu router
# =========================
async def on_menu_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu:back":
        await safe_edit_message(query, "Меню:", reply_markup=kb_menu())
        return STATE_MENU

    if data == "menu:view":
        await safe_edit_message(query, "Выберите день (Пн–Пт):", reply_markup=kb_weekdays("viewday"))
        return STATE_VIEW_DAY

    if data == "menu:build":
        await safe_edit_message(query, "Составление. Выберите день (Пн–Пт):", reply_markup=kb_weekdays("buildday"))
        return STATE_BUILD_DAY

    if data == "menu:edit":
        await safe_edit_message(query, "Редактирование. Выберите день (Пн–Пт):", reply_markup=kb_weekdays("editday"))
        return STATE_EDIT_DAY

    await safe_edit_message(query, "Меню:", reply_markup=kb_menu())
    return STATE_MENU


# =========================
# 10) View
# =========================
async def on_view_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    _, day = query.data.split(":", 1)
    user_id = query.from_user.id
    schedule = get_user_schedule(user_id)

    await safe_edit_message(query, format_day(schedule, day), reply_markup=kb_back_to_menu())
    return STATE_MENU


# =========================
# 11) Build
# =========================
async def on_build_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    _, day = query.data.split(":", 1)
    context.user_data["build_day"] = day

    user_id = query.from_user.id
    schedule = get_user_schedule(user_id)

    text = format_day(schedule, day) + "\n\nВыберите, какую пару заполнить:"
    await safe_edit_message(query, text, reply_markup=kb_slots("buildslot", day))
    return STATE_BUILD_SLOT


async def on_build_slot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) >= 3 and parts[1] == "back":
        await safe_edit_message(query, "Составление. Выберите день (Пн–Пт):", reply_markup=kb_weekdays("buildday"))
        return STATE_BUILD_DAY

    _, day, slot_index_str = parts
    slot_index = int(slot_index_str)

    context.user_data["build_day"] = day
    context.user_data["build_slot"] = slot_index

    await safe_edit_message(
        query,
        f"Введите предмет для:\n{WEEKDAY_RU[day]}, {slot_index + 1} пара\n\n"
        "Пример: Математика (ауд. 305)\n"
        "Можно отправить «—», чтобы оставить пусто.",
        reply_markup=kb_back_to_menu(),
    )
    return STATE_BUILD_TEXT


async def on_build_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    day = context.user_data.get("build_day")
    slot = context.user_data.get("build_slot")

    if day not in WEEKDAYS or slot not in range(PAIR_COUNT):
        await update.message.reply_text("Состояние сбилось. Нажмите /start.", reply_markup=kb_menu())
        return STATE_MENU

    user_id = update.effective_user.id
    schedule = set_user_day_slot(user_id, day, slot, text if text else "—")

    await update.message.reply_text("Сохранено.\n\n" + format_day(schedule, day), reply_markup=kb_menu())
    return STATE_MENU


# =========================
# 12) Edit
# =========================
async def on_edit_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    _, day = query.data.split(":", 1)
    context.user_data["edit_day"] = day

    user_id = query.from_user.id
    schedule = get_user_schedule(user_id)

    text = format_day(schedule, day) + "\n\nВыберите пару для редактирования:"
    await safe_edit_message(query, text, reply_markup=kb_slots("editslot", day))
    return STATE_EDIT_SLOT


async def on_edit_slot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) >= 3 and parts[1] == "back":
        await safe_edit_message(query, "Редактирование. Выберите день (Пн–Пт):", reply_markup=kb_weekdays("editday"))
        return STATE_EDIT_DAY

    _, day, slot_index_str = parts
    slot_index = int(slot_index_str)

    context.user_data["edit_day"] = day
    context.user_data["edit_slot"] = slot_index

    user_id = query.from_user.id
    schedule = get_user_schedule(user_id)
    current = schedule[day][slot_index]

    await safe_edit_message(
        query,
        f"Текущее значение:\n{current}\n\n"
        f"Введите новое для:\n{WEEKDAY_RU[day]}, {slot_index + 1} пара\n\n"
        "Можно отправить «—», чтобы очистить.",
        reply_markup=kb_back_to_menu(),
    )
    return STATE_EDIT_TEXT


async def on_edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    day = context.user_data.get("edit_day")
    slot = context.user_data.get("edit_slot")

    if day not in WEEKDAYS or slot not in range(PAIR_COUNT):
        await update.message.reply_text("Состояние сбилось. Нажмите /start.", reply_markup=kb_menu())
        return STATE_MENU

    user_id = update.effective_user.id
    schedule = set_user_day_slot(user_id, day, slot, text if text else "—")

    await update.message.reply_text("Обновлено.\n\n" + format_day(schedule, day), reply_markup=kb_menu())
    return STATE_MENU


# =========================
# 13) Error handler
# =========================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print("Unhandled error:", context.error)


# =========================
# 14) Main
# =========================
def main() -> None:
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN не найден. Проверьте .env (BOT_TOKEN=...).")

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_error_handler(error_handler)

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            STATE_MENU: [CallbackQueryHandler(on_menu_click)],
            STATE_VIEW_DAY: [
                CallbackQueryHandler(on_view_day, pattern=r"^viewday:"),
                CallbackQueryHandler(on_menu_click, pattern=r"^menu:back$"),
            ],
            STATE_BUILD_DAY: [
                CallbackQueryHandler(on_build_day, pattern=r"^buildday:"),
                CallbackQueryHandler(on_menu_click, pattern=r"^menu:back$"),
            ],
            STATE_BUILD_SLOT: [
                CallbackQueryHandler(on_build_slot, pattern=r"^buildslot:"),
                CallbackQueryHandler(on_menu_click, pattern=r"^menu:back$"),
            ],
            STATE_BUILD_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_build_text),
                CallbackQueryHandler(on_menu_click, pattern=r"^menu:back$"),
            ],
            STATE_EDIT_DAY: [
                CallbackQueryHandler(on_edit_day, pattern=r"^editday:"),
                CallbackQueryHandler(on_menu_click, pattern=r"^menu:back$"),
            ],
            STATE_EDIT_SLOT: [
                CallbackQueryHandler(on_edit_slot, pattern=r"^editslot:"),
                CallbackQueryHandler(on_menu_click, pattern=r"^menu:back$"),
            ],
            STATE_EDIT_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_edit_text),
                CallbackQueryHandler(on_menu_click, pattern=r"^menu:back$"),
            ],
        },
        fallbacks=[CommandHandler("help", cmd_help)],
        allow_reentry=True,
        per_chat=True,
        per_user=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("help", cmd_help))

    print("Bot started (multi-user schedules)...")
    app.run_polling()


if __name__ == "__main__":
    main()
