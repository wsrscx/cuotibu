from __future__ import annotations

import json
from datetime import datetime

from database import db

QUESTION_TYPE_LABELS = {
    "single_choice": "单选题",
    "multiple_choice": "多选题",
    "true_false": "判断题",
    "fill_blank": "填空题",
    "short_answer": "简答题",
    "essay": "论述题",
}

STATUS_LABELS = {
    "assigned": "待完成",
    "in_progress": "进行中",
    "submitted": "待批改",
    "graded": "已批改",
}


class User(db.Model):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(30), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(10), nullable=False, default="student", index=True)
    openrouter_api_key = db.Column(db.String(255), nullable=True)
    session_version = db.Column(db.Integer, default=0, nullable=False)
    teacher_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    teacher = db.relationship(
        "User",
        remote_side=[id],
        foreign_keys=[teacher_id],
        backref=db.backref("students", lazy=True),
    )
    notebooks = db.relationship(
        "Notebook",
        backref="user",
        lazy=True,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def is_teacher(self) -> bool:
        return self.role == "teacher"

    @property
    def is_student(self) -> bool:
        return self.role == "student"

    @property
    def initial(self) -> str:
        return (self.username or "?")[:1].upper()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.username}>"


class Notebook(db.Model):
    __tablename__ = "notebook"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)
    share_link = db.Column(db.String(64), unique=True, nullable=True, index=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.now,
        onupdate=datetime.now,
        nullable=False,
    )

    notes = db.relationship(
        "Note",
        backref="notebook",
        lazy=True,
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Note.created_at.desc()",
    )

    @property
    def note_count(self) -> int:
        return len(self.notes)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Notebook {self.title}>"


class Note(db.Model):
    __tablename__ = "note"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(160), nullable=False)
    content = db.Column(db.Text, nullable=False)
    content_hash = db.Column(db.String(64), nullable=True, index=True)
    image_path = db.Column(db.String(255), nullable=True)
    category = db.Column(db.String(80), nullable=True, index=True)
    improvement_suggestions = db.Column(db.Text, nullable=True)
    error_reason = db.Column(db.Text, nullable=True)
    answer = db.Column(db.Text, nullable=True)
    tags = db.Column(db.Text, nullable=True)
    next_review_date = db.Column(db.DateTime, nullable=True, index=True)
    review_count = db.Column(db.Integer, default=0, nullable=False)
    difficulty_level = db.Column(db.Integer, default=1, nullable=False)
    notebook_id = db.Column(
        db.Integer,
        db.ForeignKey("notebook.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.now,
        onupdate=datetime.now,
        nullable=False,
    )

    practice_records = db.relationship(
        "PracticeRecord",
        backref="note",
        lazy=True,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def tags_list(self) -> list[str]:
        if not self.tags:
            return []
        return [tag.strip() for tag in self.tags.split(",") if tag.strip()]

    @property
    def summary(self) -> str:
        text = (self.content or "").strip().replace("\n", " ")
        return text[:120] + ("…" if len(text) > 120 else "")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Note {self.title}>"


class Tag(db.Model):
    __tablename__ = "tag"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    color = db.Column(db.String(7), default="#b23a2b", nullable=False)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    user = db.relationship("User", backref="tags")

    __table_args__ = (
        db.UniqueConstraint("name", "user_id", name="uq_tag_name_user"),
    )


class PracticeRecord(db.Model):
    __tablename__ = "practice_record"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    note_id = db.Column(
        db.Integer,
        db.ForeignKey("note.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    is_correct = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    user = db.relationship("User", backref="practice_records")


class Textbook(db.Model):
    __tablename__ = "textbook"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=True)
    file_path = db.Column(db.String(255), nullable=False)
    page_count = db.Column(db.Integer, nullable=True, default=1)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    user = db.relationship("User", backref="textbooks")
    assignments = db.relationship(
        "Assignment",
        backref="textbook",
        lazy=True,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Assignment(db.Model):
    __tablename__ = "assignment"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=True)
    due_date = db.Column(db.DateTime, nullable=True)
    textbook_id = db.Column(
        db.Integer,
        db.ForeignKey("textbook.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    user = db.relationship("User", backref="created_assignments")
    questions = db.relationship(
        "Question",
        backref="assignment",
        lazy=True,
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Question.id",
    )
    student_assignments = db.relationship(
        "StudentAssignment",
        backref="assignment",
        lazy=True,
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="StudentAssignment.id",
    )

    @property
    def total_points(self) -> float:
        return sum(question.points or 0 for question in self.questions)


class Question(db.Model):
    __tablename__ = "question"

    id = db.Column(db.Integer, primary_key=True)
    question_type = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    options = db.Column(db.Text, nullable=True)
    correct_answer = db.Column(db.Text, nullable=False)
    explanation = db.Column(db.Text, nullable=True)
    points = db.Column(db.Float, default=1, nullable=False)
    page_number = db.Column(db.Integer, nullable=True)
    image_path = db.Column(db.String(255), nullable=True)
    assignment_id = db.Column(
        db.Integer,
        db.ForeignKey("assignment.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    student_answers = db.relationship(
        "StudentAnswer",
        backref="question",
        lazy=True,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def type_label(self) -> str:
        return QUESTION_TYPE_LABELS.get(self.question_type, self.question_type)

    @property
    def options_list(self) -> list[str]:
        if not self.options:
            return []
        try:
            value = json.loads(self.options)
        except (TypeError, ValueError):
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        return []


class StudentAssignment(db.Model):
    __tablename__ = "student_assignment"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    assignment_id = db.Column(
        db.Integer,
        db.ForeignKey("assignment.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = db.Column(db.String(20), default="assigned", nullable=False, index=True)
    submitted_at = db.Column(db.DateTime, nullable=True)
    score = db.Column(db.Float, nullable=True)
    feedback = db.Column(db.Text, nullable=True)

    student = db.relationship("User", backref="assigned_assignments")
    answers = db.relationship(
        "StudentAnswer",
        backref="student_assignment",
        lazy=True,
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="StudentAnswer.id",
    )

    __table_args__ = (
        db.UniqueConstraint(
            "student_id", "assignment_id", name="uq_student_assignment"
        ),
    )

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def can_submit(self) -> bool:
        return self.status in {"assigned", "in_progress"}


class StudentAnswer(db.Model):
    __tablename__ = "student_answer"

    id = db.Column(db.Integer, primary_key=True)
    student_assignment_id = db.Column(
        db.Integer,
        db.ForeignKey("student_assignment.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    question_id = db.Column(
        db.Integer,
        db.ForeignKey("question.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    answer_content = db.Column(db.Text, nullable=True)
    is_correct = db.Column(db.Boolean, nullable=True)
    score = db.Column(db.Float, nullable=True)
    feedback = db.Column(db.Text, nullable=True)

    __table_args__ = (
        db.UniqueConstraint(
            "student_assignment_id", "question_id", name="uq_answer_question"
        ),
    )
