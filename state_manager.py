# state_manager.py
# 每個使用者的對話狀態機

from enum import Enum, auto
from typing import Any


class State(Enum):
    IDLE = auto()         # 閒置
    WAIT_NAME = auto()    # 分享流程：等店家名稱
    WAIT_IMAGE = auto()   # 分享流程：等照片
    WAIT_REVIEW = auto()  # 分享流程：等評論
    WAIT_PICK = auto()    # 推薦流程：等使用者選號碼


class StateManager:
    """
    In-memory 狀態機，以 user_id 為 key。
    如果需要持久化（重啟後保留），可改成 Redis 版本。
    """

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
