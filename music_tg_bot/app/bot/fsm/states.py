from aiogram.fsm.state import StatesGroup, State


class TrackStates(StatesGroup):
    waiting_for_brief = State()
    waiting_for_review = State()
    waiting_for_edit = State()
    waiting_for_title = State()
