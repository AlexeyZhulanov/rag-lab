from aiogram.fsm.state import State, StatesGroup

# --- МАШИНА СОСТОЯНИЙ (FSM) ---
class QuizState(StatesGroup):
    waiting_for_article_choice = State() # Выбор статьи
    waiting_for_count_choice = State()   # Выбор количества вопросов
    waiting_for_answer = State()         # Сама игра


class ReportState(StatesGroup):
    showing_report = State()  # состояние, когда показываем /report и храним список статей