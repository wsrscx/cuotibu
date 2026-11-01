"""Create a throw-away demo database for UI checks.

Usage:
    python scripts/seed_demo.py
    python app.py

The demo data lives in ``.demo-data`` and is ignored by git. Demo passwords are
intentionally public: ``demo12345``.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CUOTIBU_DATA_DIR", str(ROOT / ".demo-data"))

from werkzeug.security import generate_password_hash  # noqa: E402

import app as app_module  # noqa: E402
from database import db  # noqa: E402
from models import (  # noqa: E402
    Assignment,
    Note,
    Notebook,
    PracticeRecord,
    Question,
    StudentAnswer,
    StudentAssignment,
    Textbook,
    User,
)


def make_page_image(path: Path) -> None:
    image = Image.new("RGB", (1200, 800), "#fbf9f3")
    draw = ImageDraw.Draw(image)
    draw.rectangle((45, 45, 1155, 755), outline="#d6cfc0", width=2)
    draw.text((85, 90), "MATHEMATICS / PAGE 03", fill="#746e62")
    draw.line((85, 120, 1115, 120), fill="#d6cfc0", width=1)
    draw.text((85, 160), "3.2  Monotonicity of quadratic functions", fill="#1e1d1a")
    for index, line in enumerate(
        [
            "For f(x) = x^2 - 4x + 3, rewrite as (x - 2)^2 - 1.",
            "The axis of symmetry is x = 2.",
            "The function decreases on (-inf, 2] and increases on [2, +inf).",
            "",
            "Example 1  Find the interval on which f(x) = x^2 - 6x + 5 decreases.",
            "Example 2  Compare f(1) and f(4) for the function above.",
        ]
    ):
        draw.text((85, 220 + index * 52), line, fill="#3f3c35")
    draw.line((85, 650, 1115, 650), fill="#d6cfc0", width=1)
    draw.text((85, 680), "Exercises 1 - 5", fill="#b23a2b")
    image.save(path)


def seed() -> None:
    with app_module.app.app_context():
        db.drop_all()
        db.create_all()

        teacher = User(
            username="demo_teacher",
            email="teacher@demo.local",
            password=generate_password_hash("demo12345"),
            role="teacher",
            openrouter_api_key="sk-or-demo-not-a-real-key",
        )
        student = User(
            username="demo_student",
            email="student@demo.local",
            password=generate_password_hash("demo12345"),
            role="student",
            teacher=teacher,
        )
        db.session.add_all([teacher, student])
        db.session.flush()

        upload_dir = Path(app_module.app.config["UPLOAD_FOLDER"])
        upload_dir.mkdir(parents=True, exist_ok=True)
        page_file = "demo-page-03.png"
        make_page_image(upload_dir / page_file)

        textbook = Textbook(
            title="高中数学必修一 · 函数",
            description="第 3 页：二次函数的单调性",
            file_path=page_file,
            page_count=1,
            user_id=teacher.id,
        )
        db.session.add(textbook)
        db.session.flush()

        notebook = Notebook(
            title="函数与导数",
            description="课堂例题、周测和作业里反复出错的函数题。",
            user_id=student.id,
            share_link="demo-share-functions",
        )
        notebook2 = Notebook(
            title="文言文实词",
            description="一词多义和课内翻译。",
            user_id=student.id,
            share_link="demo-share-classical",
        )
        db.session.add_all([notebook, notebook2])
        db.session.flush()

        now = datetime.now()
        notes = [
            Note(
                title="二次函数单调区间忘记写端点",
                content=(
                    "题目：求 f(x) = x² − 4x + 3 的单调区间。\n"
                    "我的答案：在 (2, +∞) 上递增，在 (−∞, 2) 上递减。\n"
                    "问题：把对称轴所在的点漏掉了，区间端点写错。"
                ),
                category="函数 · 单调性",
                error_reason="配方后直接看开口方向，忽略了对称轴属于两个单调区间的端点。",
                answer="f(x) = (x − 2)² − 1，所以在 (−∞, 2] 上递减，在 [2, +∞) 上递增。",
                improvement_suggestions="先配方写出顶点，再画草图；写区间时检查端点是否闭合。",
                tags="二次函数,单调性,端点",
                notebook_id=notebook.id,
                next_review_date=now - timedelta(hours=2),
                review_count=2,
                difficulty_level=3,
            ),
            Note(
                title="定义域优先：分式函数漏条件",
                content="题目：求 y = 1 / (x − 1) 的定义域与单调性。\n我只写了 x ≠ 0，忘记了分母不能为 1。",
                category="函数 · 定义域",
                error_reason="套用反比例函数结论，没有先解分母不为零。",
                answer="定义域为 x ≠ 1；在 (−∞, 1) 与 (1, +∞) 上分别递减。",
                improvement_suggestions="遇到分式先写分母不为零，再谈单调性。",
                tags="定义域,分式",
                notebook_id=notebook.id,
                next_review_date=now - timedelta(days=1),
                review_count=1,
                difficulty_level=2,
            ),
            Note(
                title="复合函数单调性判断方向反了",
                content="题目：判断 y = log₂(x² − 2x) 的单调性。\n内外层都判断了，但把同增异减记反。",
                category="函数 · 复合函数",
                error_reason="同增异减的口诀记混，没有分区间讨论。",
                answer="定义域 x < 0 或 x > 2；在 (2, +∞) 上递增，在 (−∞, 0) 上递减。",
                improvement_suggestions="先求定义域，再分别判断内外层单调性，列表整理。",
                tags="复合函数,对数",
                notebook_id=notebook.id,
                next_review_date=now + timedelta(days=1),
                review_count=0,
                difficulty_level=4,
            ),
            Note(
                title="「之」的用法：结构助词与代词",
                content="“辍耕之垄上”中的“之”是动词，我把它当成了代词。",
                category="文言文 · 实词",
                error_reason="只根据现代语感判断，没有结合句式。",
                answer="“之”在这里是动词，意为“到、往”。",
                improvement_suggestions="把课内例句按词性整理成表格，每周回看一次。",
                tags="文言文,实词,之",
                notebook_id=notebook2.id,
                next_review_date=now - timedelta(hours=5),
                review_count=3,
                difficulty_level=2,
            ),
            Note(
                title="翻译题漏掉省略成分",
                content="“竖子不足与谋”翻译时没有补出“与之”。",
                category="文言文 · 翻译",
                error_reason="没有识别省略句，直译导致成分残缺。",
                answer="这小子不值得和他共谋大事。",
                improvement_suggestions="翻译前先标出省略成分，再逐字对应。",
                tags="翻译,省略句",
                notebook_id=notebook2.id,
                next_review_date=now + timedelta(days=2),
                review_count=1,
                difficulty_level=3,
            ),
        ]
        db.session.add_all(notes)
        db.session.flush()

        for index, note in enumerate(notes[:3]):
            db.session.add(
                PracticeRecord(
                    user_id=student.id,
                    note_id=note.id,
                    is_correct=index != 2,
                    created_at=now - timedelta(days=index),
                )
            )

        assignment = Assignment(
            title="函数单调性 · 课后练习",
            description="从教材第 3 页生成，覆盖单调区间与定义域。",
            textbook_id=textbook.id,
            user_id=teacher.id,
            due_date=now + timedelta(days=3),
        )
        db.session.add(assignment)
        db.session.flush()

        questions = [
            Question(
                question_type="single_choice",
                content="函数 f(x) = x² − 6x + 5 的单调递减区间是？",
                options=json.dumps(
                    ["A. (−∞, 3]", "B. [3, +∞)", "C. (−∞, 5]", "D. [5, +∞)"],
                    ensure_ascii=False,
                ),
                correct_answer="A. (−∞, 3]",
                explanation="对称轴为 x = 3，开口向上，因此左侧递减。",
                points=2,
                page_number=1,
                assignment_id=assignment.id,
            ),
            Question(
                question_type="true_false",
                content="二次函数的对称轴一定属于它的单调区间端点。",
                options=json.dumps(["正确", "错误"], ensure_ascii=False),
                correct_answer="正确",
                explanation="对称轴把定义域分成两个单调区间，端点是闭的。",
                points=1,
                page_number=1,
                assignment_id=assignment.id,
            ),
            Question(
                question_type="short_answer",
                content="求 y = log₂(x² − 2x) 的定义域，并指出单调递增区间。",
                options=json.dumps([], ensure_ascii=False),
                correct_answer="定义域 (−∞, 0) ∪ (2, +∞)；递增区间 (2, +∞)。",
                explanation="先令 x² − 2x > 0，再结合内外层单调性。",
                points=5,
                page_number=1,
                assignment_id=assignment.id,
            ),
        ]
        db.session.add_all(questions)
        db.session.flush()

        graded = StudentAssignment(
            student_id=student.id,
            assignment_id=assignment.id,
            status="graded",
            submitted_at=now - timedelta(days=2),
            score=75,
            feedback="定义域和端点还需要再检查一遍，整体思路清楚。",
        )
        db.session.add(graded)
        db.session.flush()
        db.session.add_all(
            [
                StudentAnswer(
                    student_assignment_id=graded.id,
                    question_id=questions[0].id,
                    answer_content="A. (−∞, 3]",
                    is_correct=True,
                    score=2,
                    feedback="正确。",
                ),
                StudentAnswer(
                    student_assignment_id=graded.id,
                    question_id=questions[1].id,
                    answer_content="错误",
                    is_correct=False,
                    score=0,
                    feedback="对称轴是端点，区间应写成闭区间。",
                ),
                StudentAnswer(
                    student_assignment_id=graded.id,
                    question_id=questions[2].id,
                    answer_content="定义域是 x > 2，递增区间是 (2, +∞)。",
                    is_correct=False,
                    score=3.5,
                    feedback="漏掉了 x < 0 的一段。",
                ),
            ]
        )

        assignment2 = Assignment(
            title="文言文实词 · 周测",
            description="一词多义与省略句翻译。",
            textbook_id=textbook.id,
            user_id=teacher.id,
            due_date=now + timedelta(days=1),
        )
        db.session.add(assignment2)
        db.session.flush()
        db.session.add(
            Question(
                question_type="short_answer",
                content="解释“辍耕之垄上”中“之”的意思。",
                options=json.dumps([], ensure_ascii=False),
                correct_answer="动词，到、往。",
                explanation="结合“辍耕”与“垄上”的语境判断。",
                points=2,
                assignment_id=assignment2.id,
            )
        )
        db.session.flush()
        db.session.add(
            StudentAssignment(
                student_id=student.id,
                assignment_id=assignment2.id,
                status="assigned",
            )
        )

        db.session.commit()

    print(f"Demo database ready: {app_module.DATA_DIR}")
    print("Teacher: demo_teacher / demo12345")
    print("Student: demo_student / demo12345")


if __name__ == "__main__":
    seed()
