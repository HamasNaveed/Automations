# session_manager.py
# Simple in-memory storage for user session states

_sessions = {}

def get_state(user_id: str) -> str:
    """Retrieve the current state of a user session. Default is None."""
    return _sessions.get(user_id)

def set_state(user_id: str, state: str):
    """Set the state of a user session."""
    _sessions[user_id] = state

def clear_state(user_id: str):
    """Clear the user session state."""
    if user_id in _sessions:
        del _sessions[user_id]
