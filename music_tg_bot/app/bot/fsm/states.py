from aiogram.fsm.state import StatesGroup, State


class TrackStates(StatesGroup):
    waiting_for_brief = State()
    waiting_for_user_lyrics_brief = State()
    waiting_for_user_lyrics_text = State()
    waiting_for_review = State()
    waiting_for_edit = State()
    waiting_for_title = State()
    waiting_for_audio_confirm = State()
