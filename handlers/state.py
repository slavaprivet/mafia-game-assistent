# Общее состояние между handlers — избегаем circular imports
# text_tasks и callbacks оба импортируют отсюда

# task_id -> {"change": {...}, ...}
pending_changes: dict[int, dict] = {}

# task_id -> {"preview_path": str, "target_path": str, "new_content": str, "user_id": int, ...}
pending_previews: dict[int, dict] = {}
