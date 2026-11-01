from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["CUOTIBU_DATA_DIR"] = tempfile.mkdtemp(prefix="cuotibu-tests-")
os.environ["SECRET_KEY"] = "test-secret-key"

import app as app_module  # noqa: E402
from database import db  # noqa: E402
from models import Notebook, Question, StudentAssignment, Textbook  # noqa: E402

TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII="
)
CSRF_FORM = re.compile(r'name="_csrf" value="([^"]+)"')
CSRF_META = re.compile(r'name="csrf-token" content="([^"]+)"')


class CuotibuSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app_module.app.config.update(TESTING=True)
        cls.teacher = app_module.app.test_client()
        cls.student = app_module.app.test_client()
        cls.anon = app_module.app.test_client()
        cls.teacher_token = cls._form_token(cls.teacher, "/register")
        response = cls.teacher.post(
            "/register",
            data={
                "_csrf": cls.teacher_token,
                "username": "teacher_test",
                "email": "teacher@example.com",
                "password": "password123",
                "confirm_password": "password123",
                "role": "teacher",
                "api_key": "sk-or-test",
            },
        )
        assert response.status_code == 302, response.get_data(as_text=True)

        student_token = cls._form_token(cls.student, "/register")
        response = cls.student.post(
            "/register",
            data={
                "_csrf": student_token,
                "username": "student_test",
                "email": "student@example.com",
                "password": "password123",
                "confirm_password": "password123",
                "role": "student",
                "api_key": "",
            },
        )
        assert response.status_code == 302, response.get_data(as_text=True)

        cls._login(cls.teacher, "teacher_test")
        cls.teacher_token = cls._meta_token(cls.teacher, "/teacher_dashboard")
        response = cls.teacher.post(
            "/add_student",
            data={"_csrf": cls.teacher_token, "username": "student_test"},
        )
        assert response.status_code == 302

        response = cls.teacher.post(
            "/upload_textbook",
            data={
                "_csrf": cls.teacher_token,
                "title": "测试教材页",
                "description": "用于冒烟测试",
                "image_file": (io.BytesIO(TINY_PNG), "page.png"),
            },
            content_type="multipart/form-data",
        )
        assert response.status_code == 302, response.get_data(as_text=True)
        cls.textbook_id = int(response.headers["Location"].rstrip("/").split("/")[-1])

        response = cls.teacher.post(
            "/create_assignment",
            data={
                "_csrf": cls.teacher_token,
                "title": "测试作业",
                "description": "冒烟测试",
                "textbook_id": str(cls.textbook_id),
                "due_date": "2030-01-01",
            },
        )
        assert response.status_code == 302, response.get_data(as_text=True)
        cls.assignment_id = int(response.headers["Location"].rstrip("/").split("/")[-1])

        with app_module.app.app_context():
            question = Question(
                question_type="single_choice",
                content="1 + 1 = ?",
                options=json.dumps(["A. 1", "B. 2"], ensure_ascii=False),
                correct_answer="B. 2",
                explanation="一加一等于二。",
                points=2,
                assignment_id=cls.assignment_id,
            )
            db.session.add(question)
            db.session.commit()
            cls.question_id = question.id
            cls.student_id = app_module.User.query.filter_by(
                username="student_test"
            ).first().id

        response = cls.teacher.post(
            "/assign_to_students",
            data={
                "_csrf": cls.teacher_token,
                "assignment_id": str(cls.assignment_id),
                "student_ids": str(cls.student_id),
            },
        )
        assert response.status_code == 302

        cls._login(cls.student, "student_test")
        cls.student_token = cls._meta_token(cls.student, "/student_dashboard")

    @classmethod
    def tearDownClass(cls):
        with app_module.app.app_context():
            db.session.remove()
            db.engine.dispose()
        root_logger = logging.getLogger()
        for handler in list(root_logger.handlers):
            if isinstance(handler, RotatingFileHandler):
                handler.close()
                root_logger.removeHandler(handler)
        shutil.rmtree(os.environ["CUOTIBU_DATA_DIR"], ignore_errors=True)

    @staticmethod
    def _form_token(client, path: str) -> str:
        html = client.get(path).get_data(as_text=True)
        match = CSRF_FORM.search(html)
        assert match, f"no form token on {path}"
        return match.group(1)

    @staticmethod
    def _meta_token(client, path: str) -> str:
        html = client.get(path).get_data(as_text=True)
        match = CSRF_META.search(html)
        assert match, f"no meta token on {path}"
        return match.group(1)

    @staticmethod
    def _login(client, username: str):
        token = CuotibuSmokeTest._form_token(client, "/login")
        response = client.post(
            "/login",
            data={
                "_csrf": token,
                "identifier": username,
                "password": "password123",
            },
        )
        assert response.status_code == 302, response.get_data(as_text=True)

    def test_public_pages(self):
        for path in ("/", "/login", "/register"):
            self.assertEqual(self.anon.get(path).status_code, 200, path)
        self.assertEqual(self.anon.get("/health").json["app"], "cuotibu")

    def test_dashboards_render(self):
        for path in ("/teacher_dashboard", "/view_textbook/%s" % self.textbook_id, "/view_assignment/%s" % self.assignment_id, "/manage_students", "/settings"):
            response = self.teacher.get(path)
            self.assertEqual(response.status_code, 200, path)
        for path in ("/student_dashboard", "/settings"):
            response = self.student.get(path)
            self.assertEqual(response.status_code, 200, path)

    def test_student_cannot_open_teacher_assignment(self):
        response = self.student.get(
            f"/view_assignment/{self.assignment_id}",
            headers={"X-Requested-With": "fetch"},
        )
        self.assertEqual(response.status_code, 403)

    def test_csrf_is_required(self):
        response = self.student.post(
            "/create_notebook",
            data={"title": "没有令牌"},
        )
        self.assertEqual(response.status_code, 302)
        response = self.student.post(
            "/create_notebook",
            json={"title": "没有令牌"},
            headers={"X-Requested-With": "fetch"},
        )
        self.assertEqual(response.status_code, 400)

    def test_private_links_are_rejected(self):
        response = self.student.post(
            "/analyze_error_link",
            json={"link": "http://127.0.0.1:88/health"},
            headers={
                "X-CSRF-Token": self.student_token,
                "X-Requested-With": "fetch",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("公开", response.json["error"])

    def test_invalid_image_is_rejected(self):
        with app_module.app.app_context():
            before = Textbook.query.count()
        response = self.teacher.post(
            "/upload_textbook",
            data={
                "_csrf": self.teacher_token,
                "title": "不是图片",
                "image_file": (io.BytesIO(b"not an image"), "fake.png"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        with app_module.app.app_context():
            self.assertEqual(Textbook.query.count(), before)

    def test_notebook_flow_and_share(self):
        response = self.student.post(
            "/create_notebook",
            data={
                "_csrf": self.student_token,
                "title": "函数错题",
                "description": "测试用",
            },
        )
        self.assertEqual(response.status_code, 302)
        notebook_id = int(response.headers["Location"].rstrip("/").split("/")[-1])
        response = self.student.get(f"/notebook/{notebook_id}")
        self.assertEqual(response.status_code, 200)

        response = self.student.post(
            f"/notebook/{notebook_id}/add_note",
            data={
                "_csrf": self.student_token,
                "title": "二次函数",
                "content": "忘记讨论定义域。",
            },
        )
        self.assertEqual(response.status_code, 302)
        with app_module.app.app_context():
            notebook = db.session.get(Notebook, notebook_id)
            self.assertEqual(len(notebook.notes), 1)
            note_id = notebook.notes[0].id
            share_link = notebook.share_link

        self.assertEqual(self.student.get(f"/note/{note_id}").status_code, 200)
        self.assertEqual(self.anon.get(f"/share/{share_link}").status_code, 200)
        response = self.teacher.get(
            f"/note/{note_id}", headers={"X-Requested-With": "fetch"}
        )
        self.assertEqual(response.status_code, 403)

        response = self.student.post(
            "/update_review_schedule",
            json={"note_id": note_id, "difficulty_level": 4},
            headers={
                "X-CSRF-Token": self.student_token,
                "X-Requested-With": "fetch",
            },
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertIsNotNone(response.json["next_review_date"])

    def test_assignment_submission_and_grading(self):
        response = self.student.get(f"/view_student_assignment/{self.assignment_id}")
        self.assertEqual(response.status_code, 200)
        response = self.student.post(
            "/submit_assignment_answers",
            json={
                "assignment_id": self.assignment_id,
                "answers": [{"question_id": self.question_id, "answer": "A. 1"}],
            },
            headers={
                "X-CSRF-Token": self.student_token,
                "X-Requested-With": "fetch",
            },
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

        with app_module.app.app_context():
            submission = StudentAssignment.query.filter_by(
                student_id=self.student_id, assignment_id=self.assignment_id
            ).first()
            submission_id = submission.id
            self.assertEqual(submission.status, "submitted")

        response = self.teacher.get(f"/grade_assignment/{submission_id}")
        self.assertEqual(response.status_code, 200)
        response = self.teacher.post(
            "/submit_grades",
            data={
                "_csrf": self.teacher_token,
                "submission_id": str(submission_id),
                ("score_%s" % self.question_id): "0",
                ("feedback_%s" % self.question_id): "再看一遍题目",
                "feedback": "整体需要复习。",
            },
        )
        self.assertEqual(response.status_code, 302)
        with app_module.app.app_context():
            submission = db.session.get(StudentAssignment, submission_id)
            self.assertEqual(submission.status, "graded")
            auto_notebook = Notebook.query.filter_by(
                user_id=self.student_id, title=app_module.AUTO_NOTEBOOK_TITLE
            ).first()
            self.assertIsNotNone(auto_notebook)
            self.assertEqual(len(auto_notebook.notes), 1)


if __name__ == "__main__":
    unittest.main()
