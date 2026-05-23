from enum import Enum, auto
from typing import Any


class State(Enum):
    IDLE = auto()
    WAIT_NAME = auto()
    WAIT_DUP_CONFIRM = auto()  # 重複店名確認
    WAIT_CATEGORY = auto()
    WAIT_PRICE = auto()        # 價位選擇
    WAIT_IMAGE = auto()
    WAIT_REVIEW = auto()
    WAIT_PICK = auto()
    WAIT_LIKE = auto()         # 看完店家後是否按讚
    MANAGE_PICK = auto()
    MANAGE_ACTION = auto()
    EDIT_NAME = auto()
    EDIT_REVIEW = auto()
    EDIT_IMAGE = auto()
    EDIT_CATEGORY = auto()
    EDIT_PRICE = auto()


class StateManager:
    def __init__(self):
        self._states: dict[str, State] = {}
        self._data: dict[str, dict] = {}

    def get(self, user_id: str) -> State:
        return self._states.get(user_id, State.IDLE)

    def set(self, user_id: str, state: State):
        self._states[user_id] = state

    def reset(self, user_id: str):
        self._states.pop(user_id, None)
        self._data.pop(user_id, None)

    def set_data(self, user_id: str, key: str, value: Any):
        if user_id not in self._data:
            self._data[user_id] = {}
        self._data[user_id][key] = value

    def get_data(self, user_id: str) -> dict:
        return self._data.get(user_id, {})
