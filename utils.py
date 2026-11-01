from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from models import Note, Notebook, PracticeRecord, Tag, db


def calculate_next_review_date(difficulty_level: int, review_count: int) -> datetime:
    """Return the next review time using a spaced-repetition curve.

    ``difficulty_level`` is 1 (easy) to 5 (hard). Harder notes come back
    sooner. The first reviews are deliberately close together, then the
    interval grows quickly.
    """
    difficulty = min(max(int(difficulty_level or 1), 1), 5)
    intervals = [1, 2, 4, 8, 16, 32, 64, 128]
    factor = 1.0 + (difficulty - 1) * 0.2

    if review_count < len(intervals):
        hours = intervals[review_count] / factor
    else:
        hours = intervals[-1] * (1.5 ** (review_count - len(intervals) + 1)) / factor

    return datetime.now() + timedelta(hours=hours)


def update_review_schedule(note_id: int, difficulty_level: int | None = None) -> Note | None:
    note = db.session.get(Note, note_id)
    if note is None:
        return None

    if difficulty_level is not None:
        note.difficulty_level = min(max(int(difficulty_level), 1), 5)

    note.review_count = (note.review_count or 0) + 1
    note.next_review_date = calculate_next_review_date(
        note.difficulty_level, note.review_count
    )
    db.session.commit()
    return note


def get_user_notes(user_id: int) -> list[Note]:
    notebooks = Notebook.query.filter_by(user_id=user_id).all()
    notes: list[Note] = []
    for notebook in notebooks:
        notes.extend(notebook.notes)
    return notes


def get_todays_reviews(user_id: int) -> list[Note]:
    now = datetime.now()
    return sorted(
        [
            note
            for note in get_user_notes(user_id)
            if note.next_review_date is not None and note.next_review_date <= now
        ],
        key=lambda note: (note.next_review_date, note.id),
    )


def get_note_statistics(user_id: int) -> dict:
    notes = get_user_notes(user_id)
    now = datetime.now()
    week_ago = now - timedelta(days=7)
    practice_dates = Counter(
        record.created_at.date()
        for record in PracticeRecord.query.filter_by(user_id=user_id).all()
    )

    category_stats = Counter(note.category or "未分类" for note in notes)
    daily_stats = []
    for offset in range(13, -1, -1):
        day = (now - timedelta(days=offset)).date()
        daily_stats.append(
            {
                "date": day.isoformat(),
                "label": day.strftime("%m/%d"),
                "new_notes": sum(1 for note in notes if note.created_at.date() == day),
                "practiced": practice_dates.get(day, 0),
            }
        )

    return {
        "total_notes": len(notes),
        "category_stats": dict(category_stats.most_common()),
        "weekly_new": sum(1 for note in notes if note.created_at >= week_ago),
        "todays_reviews": sum(
            1
            for note in notes
            if note.next_review_date is not None and note.next_review_date <= now
        ),
        "daily_stats": daily_stats,
    }


def get_practice_statistics(user_id: int) -> dict:
    records = PracticeRecord.query.filter_by(user_id=user_id).all()
    total = len(records)
    correct = sum(1 for record in records if record.is_correct)
    return {
        "total_practices": total,
        "correct_answers": correct,
        "accuracy_rate": round(correct / total * 100, 1) if total else 0,
    }


def get_user_tags(user_id: int) -> list[Tag]:
    return Tag.query.filter_by(user_id=user_id).order_by(Tag.name).all()


def create_tag(user_id: int, name: str, color: str = "#b23a2b") -> Tag:
    tag = Tag(name=name.strip(), color=color, user_id=user_id)
    db.session.add(tag)
    db.session.commit()
    return tag


def parse_tags(tags_string: str | None) -> list[str]:
    if not tags_string:
        return []
    return [tag.strip() for tag in tags_string.split(",") if tag.strip()]
