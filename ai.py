from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import time
from pathlib import Path

import requests


class AIError(RuntimeError):
    """Raised when the AI provider cannot return a usable answer."""


OPENROUTER_URL = os.environ.get(
    "OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions"
)
TEXT_MODEL = os.environ.get(
    "OPENROUTER_TEXT_MODEL", "qwen/qwen2.5-72b-instruct:free"
)
VISION_MODEL = os.environ.get(
    "OPENROUTER_VISION_MODEL", "qwen/qwen2.5-vl-72b-instruct:free"
)
MAX_RETRIES = max(1, int(os.environ.get("AI_MAX_RETRIES", "3")))
REQUEST_TIMEOUT = (10, 90)

QUESTION_TYPE_LABELS = {
    "single_choice": "单选题",
    "multiple_choice": "多选题",
    "true_false": "判断题",
    "fill_blank": "填空题",
    "short_answer": "简答题",
    "essay": "论述题",
}

_TYPE_ALIASES = {
    "单选": "single_choice",
    "单选题": "single_choice",
    "single_choice": "single_choice",
    "多选": "multiple_choice",
    "多选题": "multiple_choice",
    "multiple_choice": "multiple_choice",
    "判断": "true_false",
    "判断题": "true_false",
    "true_false": "true_false",
    "填空": "fill_blank",
    "填空题": "fill_blank",
    "fill_blank": "fill_blank",
    "简答": "short_answer",
    "简答题": "short_answer",
    "short_answer": "short_answer",
    "论述": "essay",
    "论述题": "essay",
    "essay": "essay",
}


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.environ.get("OPENROUTER_SITE_URL", "http://127.0.0.1"),
        "X-Title": os.environ.get("OPENROUTER_APP_NAME", "Cuotibu"),
    }


def _chat(
    api_key: str,
    messages: list[dict],
    *,
    model: str | None = None,
    timeout: tuple[int, int] = REQUEST_TIMEOUT,
) -> str:
    """Call OpenRouter once, with a small number of retries.

    TLS verification is intentionally left on. The previous version disabled
    it with ``verify=False``, which made every request vulnerable to a
    man-in-the-middle attack.
    """
    if not api_key or not isinstance(api_key, str):
        raise AIError("未设置 OpenRouter API 密钥")

    payload = {
        "model": model or TEXT_MODEL,
        "messages": messages,
        "temperature": 0.2,
    }
    last_error = "未知错误"

    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(
                OPENROUTER_URL,
                headers=_headers(api_key),
                json=payload,
                timeout=timeout,
                verify=True,
            )
        except requests.RequestException as exc:
            last_error = f"网络请求失败：{exc.__class__.__name__}"
            if attempt + 1 < MAX_RETRIES:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise AIError(last_error) from exc

        if response.status_code == 200:
            try:
                data = response.json()
                content = data["choices"][0]["message"]["content"]
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise AIError("AI 返回了无法解析的响应") from exc
            if not isinstance(content, str) or not content.strip():
                raise AIError("AI 返回了空内容")
            return content.strip()

        if response.status_code in {408, 409, 429, 500, 502, 503, 504}:
            last_error = f"AI 服务暂时不可用（HTTP {response.status_code}）"
            if attempt + 1 < MAX_RETRIES:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise AIError(last_error)

        detail = response.text[:240].replace(api_key, "***")
        raise AIError(f"AI 请求失败（HTTP {response.status_code}）：{detail}")

    raise AIError(last_error)


def _extract_json(text: str):
    """Extract the first JSON object/array from a model response."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    starts = [pos for pos in (cleaned.find("{"), cleaned.find("[")) if pos >= 0]
    if not starts:
        raise AIError("AI 没有返回 JSON 结果")
    start = min(starts)
    end_char = "}" if cleaned[start] == "{" else "]"
    end = cleaned.rfind(end_char)
    if end < start:
        raise AIError("AI 返回的 JSON 不完整")
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AIError("AI 返回的 JSON 无法解析") from exc


def _as_text(value, fallback: str = "") -> str:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _normalise_type(value: str | None) -> str:
    if not value:
        return "short_answer"
    key = str(value).strip()
    if key in _TYPE_ALIASES:
        return _TYPE_ALIASES[key]
    for alias, code in _TYPE_ALIASES.items():
        if alias in key:
            return code
    return "short_answer"


def _image_data_url(path: str) -> str:
    file_path = Path(path)
    if not file_path.is_file():
        raise AIError("图片文件不存在")
    mime = mimetypes.guess_type(file_path.name)[0] or "image/png"
    encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def analyze_error_with_ai(
    api_key: str, error_content: str, image_path: str | None = None
) -> dict:
    """Analyse a wrong answer and return category, reason, answer and tags."""
    prompt = (
        "你是一位耐心的学科老师。请分析下面这道错题，并只返回 JSON。\n\n"
        f"错题内容：\n{error_content or '（见图片）'}\n\n"
        "JSON 字段：\n"
        '  "category": 简短学科分类，例如「函数」「文言文」「力学」\n'
        '  "improvement_suggestions": 具体、可执行的改进建议\n'
        '  "error_reason": 学生出错的原因\n'
        '  "answer": 正确答案与关键步骤\n'
        '  "tags": 3 到 5 个逗号分隔的标签\n'
        "不要输出 JSON 以外的任何内容。"
    )

    if image_path:
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": _image_data_url(image_path)}},
        ]
        model = VISION_MODEL
    else:
        content = prompt
        model = TEXT_MODEL

    raw = _chat(api_key, [{"role": "user", "content": content}], model=model)
    data = _extract_json(raw)
    if not isinstance(data, dict):
        raise AIError("AI 返回的分析结果格式不正确")

    return {
        "category": _as_text(data.get("category"), "未分类").strip() or "未分类",
        "improvement_suggestions": _as_text(data.get("improvement_suggestions")),
        "error_reason": _as_text(data.get("error_reason")),
        "answer": _as_text(data.get("answer")),
        "tags": _as_text(data.get("tags")),
    }


def ocr_with_ai(api_key: str, image_path: str) -> str:
    """Read the text in an image with a vision model."""
    prompt = (
        "请逐字识别图片中的题目文字，保留公式、数字和选项。"
        "只返回识别出的文字，不要解释，不要使用 Markdown 代码块。"
    )
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": _image_data_url(image_path)}},
    ]
    return _chat(api_key, [{"role": "user", "content": content}], model=VISION_MODEL)


def generate_practice_question_with_ai(api_key: str, note) -> dict:
    prompt = (
        "基于下面这道错题，生成一道考查同一知识点的新题。只返回 JSON。\n\n"
        f"标题：{note.title}\n"
        f"内容：{note.content}\n"
        f"分类：{note.category or '未分类'}\n"
        f"错误原因：{note.error_reason or '未知'}\n"
        f"原题答案：{note.answer or '未知'}\n\n"
        "要求：难度与原题相当；选择题给 4 个选项；给出正确答案和解析。\n"
        "JSON 字段：question_type（single_choice / multiple_choice / true_false / "
        "fill_blank / short_answer）、difficulty（简单/中等/困难）、question、"
        "options（数组，非选择题为 []）、correct_answer、explanation。\n"
        "不要输出 JSON 以外的任何内容。"
    )
    raw = _chat(api_key, [{"role": "user", "content": prompt}])
    data = _extract_json(raw)
    if not isinstance(data, dict):
        raise AIError("AI 返回的练习题格式不正确")

    question_type = _normalise_type(data.get("question_type"))
    options = data.get("options") or []
    if isinstance(options, str):
        try:
            options = json.loads(options)
        except ValueError:
            options = [options]
    options = [_as_text(item).strip() for item in options if _as_text(item).strip()]
    if question_type in {"single_choice", "multiple_choice"} and not options:
        raise AIError("AI 生成的选择题缺少选项")
    if question_type == "true_false" and not options:
        options = ["正确", "错误"]

    question = _as_text(data.get("question")).strip()
    correct_answer = _as_text(data.get("correct_answer")).strip()
    if not question or not correct_answer:
        raise AIError("AI 生成的练习题不完整")

    return {
        "question_type": question_type,
        "question_type_label": QUESTION_TYPE_LABELS.get(question_type, question_type),
        "difficulty": _as_text(data.get("difficulty"), "中等"),
        "question": question,
        "options": options,
        "correct_answer": correct_answer,
        "explanation": _as_text(data.get("explanation")),
    }


def _option_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return [value]
    if isinstance(value, list):
        return [_as_text(item) for item in value]
    return []


def check_answer_with_ai(api_key: str, question_data: dict, user_answer: str) -> dict:
    options = _option_list(question_data.get("options"))
    prompt = (
        "请判断学生的答案是否正确。只返回 JSON。\n\n"
        f"题目：{question_data.get('question', '')}\n"
        f"题型：{question_data.get('question_type', '')}\n"
        f"选项：{', '.join(options) if options else '无'}\n"
        f"标准答案：{question_data.get('correct_answer', '')}\n"
        f"解析：{question_data.get('explanation', '')}\n"
        f"学生答案：{user_answer}\n\n"
        "JSON 字段：is_correct（布尔值）、correct_answer、explanation。\n"
        "不要输出 JSON 以外的任何内容。"
    )
    raw = _chat(api_key, [{"role": "user", "content": prompt}])
    data = _extract_json(raw)
    if not isinstance(data, dict):
        raise AIError("AI 返回的判题结果格式不正确")

    is_correct = data.get("is_correct")
    if isinstance(is_correct, str):
        is_correct = is_correct.strip().lower() in {"true", "1", "yes", "是", "正确"}

    return {
        "is_correct": bool(is_correct),
        "correct_answer": _as_text(
            data.get("correct_answer"), _as_text(question_data.get("correct_answer"))
        ),
        "explanation": _as_text(data.get("explanation"), "暂无解析"),
    }


def generate_questions_from_pdf_with_ai(
    api_key: str, page_content: str, question_types: list[str], question_count: int
) -> list[dict]:
    """Generate a mixed set of questions from one textbook page."""
    types = []
    for item in question_types:
        code = _normalise_type(item)
        if code not in types:
            types.append(code)
    if not types:
        raise AIError("至少选择一种题型")
    if not page_content or not page_content.strip():
        raise AIError("没有识别到页面文字，无法出题")

    labels = "、".join(QUESTION_TYPE_LABELS.get(item, item) for item in types)
    prompt = (
        f"根据下面的教材页面内容，生成 {question_count} 道题目。\n"
        f"题型范围：{labels}。\n"
        "要求：题目必须来自页面内容，不要编造；选择题给 4 个选项；"
        "每题给出正确答案和解析。\n\n"
        f"页面内容：\n{page_content[:6000]}\n\n"
        "只返回 JSON 数组，每项字段：question_type（single_choice / "
        "multiple_choice / true_false / fill_blank / short_answer）、"
        "content、options（数组）、correct_answer、explanation。\n"
        "不要输出 JSON 以外的任何内容。"
    )
    raw = _chat(api_key, [{"role": "user", "content": prompt}])
    data = _extract_json(raw)
    if not isinstance(data, list):
        raise AIError("AI 返回的题目格式不正确")

    questions: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        question_type = _normalise_type(item.get("question_type"))
        content = _as_text(item.get("content")).strip()
        correct_answer = _as_text(item.get("correct_answer")).strip()
        if not content or not correct_answer:
            continue

        options = item.get("options") or []
        if isinstance(options, str):
            try:
                options = json.loads(options)
            except ValueError:
                options = [options]
        options = [
            _as_text(option).strip()
            for option in options
            if _as_text(option).strip()
        ]
        if question_type in {"single_choice", "multiple_choice"} and len(options) < 2:
            continue
        if question_type == "true_false" and not options:
            options = ["正确", "错误"]

        questions.append(
            {
                "question_type": question_type,
                "content": content,
                "options": options,
                "correct_answer": correct_answer,
                "explanation": _as_text(item.get("explanation")),
            }
        )

    if not questions:
        raise AIError("AI 没有生成有效题目，请重试或换一张更清晰的图片")
    return questions[:question_count]


def generate_review_plan_with_ai(api_key: str, todays_notes: list) -> dict:
    notes_info = [
        {
            "id": note.id,
            "title": note.title,
            "content": (note.content or "")[:500],
            "category": note.category or "未分类",
            "error_reason": note.error_reason or "未知",
            "answer": note.answer or "未知",
        }
        for note in todays_notes
    ]
    prompt = (
        "根据下面的错题，安排今天的复习顺序。只返回 JSON。\n\n"
        f"错题：{json.dumps(notes_info, ensure_ascii=False)}\n\n"
        "JSON 字段：review_order（数组，每项含 note_id、priority（高/中/低）、"
        "review_suggestion）、overall_strategy、estimated_time（分钟，整数）。\n"
        "不要输出 JSON 以外的任何内容。"
    )
    raw = _chat(api_key, [{"role": "user", "content": prompt}])
    data = _extract_json(raw)
    if not isinstance(data, dict):
        raise AIError("AI 返回的复习计划格式不正确")

    order = data.get("review_order") or []
    clean_order = []
    if isinstance(order, list):
        for item in order:
            if not isinstance(item, dict):
                continue
            try:
                note_id = int(item.get("note_id"))
            except (TypeError, ValueError):
                continue
            clean_order.append(
                {
                    "note_id": note_id,
                    "priority": _as_text(item.get("priority"), "中"),
                    "review_suggestion": _as_text(item.get("review_suggestion")),
                }
            )

    try:
        estimated_time = int(data.get("estimated_time") or len(todays_notes) * 8)
    except (TypeError, ValueError):
        estimated_time = len(todays_notes) * 8

    return {
        "review_order": clean_order,
        "overall_strategy": _as_text(
            data.get("overall_strategy"), "按错题顺序逐题复习"
        ),
        "estimated_time": max(estimated_time, 0),
    }
