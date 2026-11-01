(() => {
  "use strict";

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const toastStack = document.getElementById("toastStack");

  function toast(message, type = "info") {
    if (!toastStack) return;
    const node = document.createElement("div");
    node.className = `toast toast-${type}`;
    node.textContent = message;
    toastStack.appendChild(node);
    window.setTimeout(() => {
      node.style.opacity = "0";
      window.setTimeout(() => node.remove(), 200);
    }, 4200);
  }

  async function api(url, { method = "GET", body = null } = {}) {
    const headers = { "X-Requested-With": "fetch" };
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
    let payload = body;
    if (body && !(body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    const response = await fetch(url, {
      method,
      headers,
      body: payload,
      credentials: "same-origin",
    });
    let data = {};
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      data = await response.json();
    }
    if (!response.ok) {
      const error = new Error(data.error || `请求失败（${response.status}）`);
      error.status = response.status;
      error.data = data;
      throw error;
    }
    return data;
  }

  function handleError(error) {
    if (error.status === 401) {
      window.location.assign("/login");
      return;
    }
    toast(error.message || "操作失败，请重试。", "error");
    if (error.data?.redirect_to_settings) {
      toast("请到「设置」填写 OpenRouter API 密钥。", "info");
    }
  }

  function setBusy(button, busy, label = "处理中…") {
    if (!button) return;
    if (busy) {
      if (!button.dataset.original) button.dataset.original = button.innerHTML;
      button.disabled = true;
      button.innerHTML = `<span class="spinner" aria-hidden="true"></span> ${label}`;
    } else {
      button.disabled = false;
      if (button.dataset.original) {
        button.innerHTML = button.dataset.original;
        delete button.dataset.original;
      }
    }
  }

  function openDialog(id) {
    const dialog = document.getElementById(id);
    if (dialog && typeof dialog.showModal === "function" && !dialog.open) {
      dialog.showModal();
    }
  }

  function closeDialog(dialog) {
    if (dialog && typeof dialog.close === "function") dialog.close();
  }

  function make(tag, className = "", text = "") {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function appendSection(parent, label, value, className = "") {
    if (value === null || value === undefined || value === "") return;
    parent.appendChild(make("dt", "", label));
    parent.appendChild(make("dd", className, String(value)));
  }

  function analysisBlock(data) {
    const block = make("dl", "note-analysis");
    appendSection(block, "分类", data.category);
    appendSection(block, "错因", data.error_reason);
    appendSection(block, "正确答案", data.answer);
    appendSection(block, "改进建议", data.improvement_suggestions);
    if (data.tags && data.tags.length) {
      const tags = Array.isArray(data.tags) ? data.tags : String(data.tags).split(",");
      appendSection(block, "标签", tags.map((tag) => String(tag).trim()).filter(Boolean).join(" · "));
    }
    return block;
  }

  function renderNote(note) {
    const dialog = document.getElementById("noteDialog");
    if (!dialog) return;
    const title = document.getElementById("noteDialogTitle");
    const meta = document.getElementById("noteDialogMeta");
    const body = document.getElementById("noteDialogBody");
    if (title) title.textContent = note.title || "错题详情";
    if (meta) {
      meta.textContent = [note.category, note.created_at, note.updated_at ? `更新 ${note.updated_at}` : ""]
        .filter(Boolean)
        .join(" · ");
    }
    if (!body) return;
    body.replaceChildren();

    if (note.content) {
      body.appendChild(make("div", "note-body", note.content));
    }
    if (note.image_url) {
      const image = document.createElement("img");
      image.className = "note-image";
      image.src = note.image_url;
      image.alt = "错题图片";
      body.appendChild(image);
    }

    const tags = Array.isArray(note.tags) ? note.tags : [];
    if (tags.length) {
      const tagRow = make("div", "cluster mt-2");
      tags.forEach((tag) => tagRow.appendChild(make("span", "tag", String(tag).trim())));
      body.appendChild(tagRow);
    }

    const hasAnalysis = note.category || note.answer || note.error_reason;
    if (hasAnalysis) {
      body.appendChild(analysisBlock(note));
    } else {
      const prompt = make("div", "note-analysis");
      prompt.appendChild(make("p", "small", "这道错题还没有 AI 分析。"));
      const button = make("button", "btn btn-sm mt-1", "开始分析");
      button.type = "button";
      button.dataset.analyzeNote = note.id;
      prompt.appendChild(button);
      body.appendChild(prompt);
    }

    if (note.notebook_id) {
      const link = document.createElement("a");
      link.className = "btn btn-sm mt-3";
      link.href = `/notebook/${note.notebook_id}`;
      link.textContent = "打开所在错题本";
      body.appendChild(link);
    }
  }

  async function showNote(noteId, analyze = false) {
    const dialog = document.getElementById("noteDialog");
    if (!dialog) return;
    const title = document.getElementById("noteDialogTitle");
    const meta = document.getElementById("noteDialogMeta");
    const body = document.getElementById("noteDialogBody");
    if (title) title.textContent = "正在加载…";
    if (meta) meta.textContent = "";
    if (body) body.replaceChildren(make("p", "muted small", "正在读取错题…"));
    openDialog("noteDialog");

    try {
      const note = await api(`/note/${noteId}`);
      renderNote(note);
      if (analyze) {
        const prompt = document.getElementById("noteDialogBody");
        if (prompt) await analyzeNote(note.id, prompt);
      }
    } catch (error) {
      if (body) body.replaceChildren(make("p", "small", error.message));
    }
  }

  async function analyzeNote(noteId, container) {
    container.querySelector("[data-analyze-note]")?.closest(".note-analysis")?.remove();
    const status = make("div", "note-analysis");
    status.appendChild(make("p", "small", "正在分析错题，请稍候…"));
    const spinner = make("span", "spinner");
    status.prepend(spinner);
    container.appendChild(status);

    try {
      const data = await api(`/analyze_note/${noteId}`, { method: "POST" });
      status.remove();
      container.appendChild(analysisBlock(data));
      toast("分析完成。", "success");
    } catch (error) {
      status.remove();
      if (error.status === 400 && error.data?.redirect_to_settings) {
        const box = make("div", "note-analysis");
        box.appendChild(make("p", "small", "还没有设置 API 密钥。可以现在填写，或到设置页保存。"));
        const form = document.createElement("form");
        form.className = "stack-sm mt-1";
        form.dataset.manualAnalysis = noteId;
        const input = document.createElement("input");
        input.className = "input";
        input.type = "password";
        input.name = "api_key";
        input.placeholder = "OpenRouter API 密钥";
        input.autocomplete = "off";
        input.required = true;
        const submit = document.createElement("button");
        submit.className = "btn btn-sm";
        submit.type = "submit";
        submit.textContent = "用此密钥分析";
        const link = document.createElement("a");
        link.className = "btn btn-sm btn-ghost";
        link.href = "/settings";
        link.textContent = "去设置";
        const actions = make("div", "cluster");
        actions.append(submit, link);
        form.append(input, actions);
        box.appendChild(form);
        container.appendChild(box);
        toast("请填写 API 密钥后重试。", "info");
      } else {
        handleError(error);
      }
    }
  }

  function copyText(text) {
    if (!text) return;
    const done = () => toast("已复制到剪贴板。", "success");
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text));
    } else {
      fallbackCopy(text);
    }
  }

  function fallbackCopy(text) {
    const helper = document.createElement("textarea");
    helper.value = text;
    helper.setAttribute("readonly", "");
    helper.style.position = "fixed";
    helper.style.opacity = "0";
    document.body.appendChild(helper);
    helper.select();
    try {
      document.execCommand("copy");
      toast("已复制到剪贴板。", "success");
    } catch {
      toast("复制失败，请手动选择文本。", "error");
    }
    helper.remove();
  }

  /* ---------- Navigation ---------- */

  document.addEventListener("click", (event) => {
    const toggle = event.target.closest("[data-nav-toggle]");
    if (toggle) {
      const nav = document.getElementById("primary-nav");
      if (!nav) return;
      const open = nav.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", String(open));
      return;
    }
    if (event.target.closest("#primary-nav a")) {
      document.getElementById("primary-nav")?.classList.remove("is-open");
    }
  });

  /* ---------- Dialogs ---------- */

  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-open-dialog]");
    if (opener) {
      event.preventDefault();
      openDialog(opener.dataset.openDialog);
      return;
    }
    const closer = event.target.closest("[data-close-dialog]");
    if (closer) {
      event.preventDefault();
      closeDialog(closer.closest("dialog"));
    }
  });

  document.querySelectorAll("dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) closeDialog(dialog);
    });
  });

  /* ---------- Generic confirms ---------- */

  document.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-confirm]");
    if (button && !window.confirm(button.dataset.confirm)) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  });

  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  /* ---------- Note actions ---------- */

  document.addEventListener("click", async (event) => {
    const view = event.target.closest("[data-view-note]");
    if (view) {
      event.preventDefault();
      await showNote(view.dataset.viewNote, false);
      return;
    }
    const analyze = event.target.closest("[data-analyze-note]");
    if (analyze) {
      event.preventDefault();
      const inDialog = analyze.closest("#noteDialogBody");
      if (inDialog) {
        analyze.disabled = true;
        await analyzeNote(analyze.dataset.analyzeNote, inDialog);
      } else {
        await showNote(analyze.dataset.analyzeNote, true);
      }
      return;
    }
    const edit = event.target.closest("[data-edit-note]");
    if (edit) {
      event.preventDefault();
      try {
        const note = await api(`/note/${edit.dataset.editNote}`);
        const form = document.getElementById("editNoteForm");
        if (!form) throw new Error("当前页面没有编辑表单");
        form.action = edit.dataset.action;
        form.querySelector('[name="title"]').value = note.title;
        form.querySelector('[name="content"]').value = note.content;
        openDialog("editNoteDialog");
      } catch (error) {
        handleError(error);
      }
      return;
    }
    const removeNote = event.target.closest("[data-delete-note]");
    if (removeNote) {
      event.preventDefault();
      try {
        await api(removeNote.dataset.deleteNote, { method: "POST" });
        toast("错题已删除。", "success");
        window.setTimeout(() => window.location.reload(), 250);
      } catch (error) {
        handleError(error);
      }
      return;
    }
    const removeNotebook = event.target.closest("[data-delete-notebook]");
    if (removeNotebook) {
      event.preventDefault();
      try {
        await api(removeNotebook.dataset.deleteNotebook, { method: "POST" });
        toast("错题本已删除。", "success");
        window.setTimeout(() => window.location.reload(), 250);
      } catch (error) {
        handleError(error);
      }
    }
  });

  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-manual-analysis]");
    if (!form) return;
    event.preventDefault();
    const input = form.querySelector('[name="api_key"]');
    const button = form.querySelector('button[type="submit"]');
    if (!input?.value.trim()) {
      toast("请填写 API 密钥。", "error");
      return;
    }
    setBusy(button, true, "分析中…");
    try {
      const data = await api("/analyze_note_with_apikey", {
        method: "POST",
        body: { note_id: Number(form.dataset.manualAnalysis), api_key: input.value.trim() },
      });
      form.closest(".note-analysis")?.replaceWith(analysisBlock(data));
      toast("分析完成。", "success");
    } catch (error) {
      handleError(error);
    } finally {
      setBusy(button, false);
    }
  });

  /* ---------- Share links ---------- */

  document.addEventListener("click", async (event) => {
    const copy = event.target.closest("[data-copy]");
    if (copy) {
      event.preventDefault();
      copyText(copy.dataset.copy);
      return;
    }
    const copyTarget = event.target.closest("[data-copy-target]");
    if (copyTarget) {
      event.preventDefault();
      const target = document.querySelector(copyTarget.dataset.copyTarget);
      copyText(target?.value || target?.textContent || "");
      return;
    }
    const rotate = event.target.closest("[data-rotate-share]");
    if (rotate) {
      event.preventDefault();
      if (!window.confirm("重新生成后，旧分享链接会立即失效。继续吗？")) return;
      try {
        const data = await api(rotate.dataset.rotateShare, { method: "POST" });
        const target = document.querySelector("[data-share-url]");
        if (target) {
          if ("value" in target) target.value = data.share_url;
          else target.textContent = data.share_url;
        }
        const copyButton = document.querySelector("[data-share-copy]");
        if (copyButton) copyButton.dataset.copy = data.share_url;
        toast("分享链接已更新。", "success");
      } catch (error) {
        handleError(error);
      }
    }
  });

  /* ---------- Analyze a link ---------- */

  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("#linkAnalyzeForm");
    if (!form) return;
    event.preventDefault();
    const link = form.querySelector('[name="link"]')?.value.trim();
    if (!link) {
      toast("请填写链接。", "error");
      return;
    }
    const button = form.querySelector('button[type="submit"]');
    setBusy(button, true, "读取中…");
    try {
      const data = await api("/analyze_error_link", { method: "POST", body: { link } });
      const titleField = document.querySelector('#addNoteForm [name="title"]');
      const contentField = document.querySelector('#addNoteForm [name="content"]');
      if (titleField && !titleField.value) titleField.value = data.title || "";
      if (contentField) contentField.value = data.content || "";
      closeDialog(form.closest("dialog"));
      openDialog("addNoteDialog");
      toast("链接内容已填入表单，检查后保存。", "success");
    } catch (error) {
      handleError(error);
    } finally {
      setBusy(button, false);
    }
  });

  /* ---------- Practice ---------- */

  const practiceState = { question: null };

  function renderPractice(question) {
    practiceState.question = question;
    const body = document.getElementById("practiceBody");
    const actions = document.getElementById("practiceActions");
    if (!body || !actions) return;
    body.replaceChildren();
    actions.replaceChildren();

    body.appendChild(make("div", "eyebrow", `练习 · ${question.question_type_label || ""} · ${question.difficulty || ""}`));
    body.appendChild(make("p", "practice-question", question.question || ""));

    const options = Array.isArray(question.options) ? question.options : [];
    if (options.length) {
      const list = make("div", "choice-list");
      const multiple = question.question_type === "multiple_choice";
      options.forEach((option, index) => {
        const label = make("label", "choice-card");
        const input = document.createElement("input");
        input.type = multiple ? "checkbox" : "radio";
        input.name = "practice_option";
        input.value = option;
        label.append(input, make("span", "", `${String.fromCharCode(65 + index)}. ${option}`));
        list.appendChild(label);
      });
      body.appendChild(list);
    } else {
      const textarea = document.createElement("textarea");
      textarea.className = "textarea";
      textarea.name = "practice_answer";
      textarea.placeholder = "写下你的答案";
      textarea.rows = 4;
      body.appendChild(textarea);
    }

    const check = make("button", "btn btn-primary", "提交答案");
    check.type = "button";
    check.id = "checkPractice";
    actions.appendChild(check);
  }

  async function generatePractice(noteId = null) {
    const body = document.getElementById("practiceBody");
    const actions = document.getElementById("practiceActions");
    if (!body) return;
    body.replaceChildren(make("p", "small muted", "正在准备练习题…"));
    if (actions) actions.replaceChildren();
    try {
      const question = await api("/generate_practice_question", {
        method: "POST",
        body: noteId ? { note_id: Number(noteId) } : {},
      });
      renderPractice(question);
    } catch (error) {
      handleError(error);
      body.replaceChildren(make("p", "small", error.message));
    }
  }

  document.addEventListener("click", async (event) => {
    const start = event.target.closest("[data-practice-start]");
    if (start) {
      event.preventDefault();
      openDialog("practiceDialog");
      await generatePractice(start.dataset.practiceStart || null);
      return;
    }
    const next = event.target.closest("#nextPractice");
    if (next) {
      event.preventDefault();
      await generatePractice();
      return;
    }
    const check = event.target.closest("#checkPractice");
    if (!check) return;
    event.preventDefault();
    const question = practiceState.question;
    if (!question) return;
    const options = question.options || [];
    let answer = "";
    if (options.length) {
      const selected = Array.from(
        document.querySelectorAll('#practiceBody input[name="practice_option"]:checked')
      ).map((input) => input.value);
      answer = selected.join(", ");
    } else {
      answer = document.getElementById("practiceBody")?.querySelector("textarea")?.value.trim() || "";
    }
    if (!answer) {
      toast("请先写下答案。", "error");
      return;
    }
    setBusy(check, true, "判题中…");
    try {
      const result = await api("/check_answer", {
        method: "POST",
        body: { question_data: question, user_answer: answer },
      });
      const resultBox = make("div", `practice-result ${result.is_correct ? "correct" : "wrong"}`);
      resultBox.appendChild(make("h4", "", result.is_correct ? "答对了" : "再看一遍"));
      resultBox.appendChild(make("p", "", `正确答案：${result.correct_answer || ""}`));
      if (result.explanation) resultBox.appendChild(make("p", "mt-1", result.explanation));
      document.getElementById("practiceBody")?.appendChild(resultBox);
      const actions = document.getElementById("practiceActions");
      if (actions) {
        actions.replaceChildren();
        const again = make("button", "btn", "下一题");
        again.type = "button";
        again.id = "nextPractice";
        actions.appendChild(again);
      }
      toast(result.is_correct ? "答对了。" : "已记录这次练习。", result.is_correct ? "success" : "info");
    } catch (error) {
      handleError(error);
    } finally {
      setBusy(check, false);
    }
  });

  /* ---------- Review plan ---------- */

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-review-plan]");
    if (!button) return;
    event.preventDefault();
    const container = document.getElementById("reviewPlanBody");
    if (!container) return;
    container.replaceChildren(make("p", "small muted", "正在生成复习计划…"));
    setBusy(button, true, "生成中…");
    try {
      const data = await api("/generate_review_plan", { method: "POST" });
      container.replaceChildren();
      if (data.overall_strategy) {
        container.appendChild(make("p", "small", data.overall_strategy));
      }
      if (data.estimated_time) {
        container.appendChild(make("p", "tiny muted mt-1", `预计 ${data.estimated_time} 分钟`));
      }
      const list = make("div", "mt-2");
      (data.review_order || []).forEach((item) => {
        const row = make("div", "review-item");
        row.appendChild(make("span", `priority priority-${item.priority || "中"}`, item.priority || "中"));
        const text = make("div");
        text.appendChild(make("div", "small", item.note_title || `错题 #${item.note_id}`));
        if (item.review_suggestion) text.appendChild(make("div", "tiny muted", item.review_suggestion));
        row.appendChild(text);
        list.appendChild(row);
      });
      if (!(data.review_order || []).length) {
        list.appendChild(make("p", "small muted", "今天没有到期错题。"));
      }
      container.appendChild(list);
      if (data.fallback) toast("AI 暂时不可用，已显示本地计划。", "info");
    } catch (error) {
      handleError(error);
      container.replaceChildren(make("p", "small", error.message));
    } finally {
      setBusy(button, false);
    }
  });

  /* ---------- Generate questions ---------- */

  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("#generateQuestionsForm");
    if (!form) return;
    event.preventDefault();
    const assignmentId = form.dataset.assignmentId;
    const types = Array.from(form.querySelectorAll('input[name="question_types"]:checked')).map((input) => input.value);
    if (!types.length) {
      toast("请至少选择一种题型。", "error");
      return;
    }
    const count = Number(form.querySelector('[name="question_count"]')?.value || 1);
    const page = Number(form.querySelector('[name="page_number"]')?.value || 1);
    const apiKey = form.querySelector('[name="api_key"]')?.value.trim() || "";
    const status = document.getElementById("generateStatus");
    const button = form.querySelector('button[type="submit"]');
    if (status) status.textContent = "正在识别页面并生成题目，这可能需要一分钟…";
    setBusy(button, true, "生成中…");
    try {
      const data = await api("/generate_questions", {
        method: "POST",
        body: {
          assignment_id: Number(assignmentId),
          question_types: types,
          question_count: count,
          page_number: page,
          api_key: apiKey || undefined,
        },
      });
      toast(`已生成 ${data.count} 道题目。`, "success");
      if (status) status.textContent = "生成完成，正在刷新…";
      window.setTimeout(() => window.location.reload(), 500);
    } catch (error) {
      handleError(error);
      if (status) status.textContent = error.message;
    } finally {
      setBusy(button, false);
    }
  });

  /* ---------- Auto grading ---------- */

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-auto-grade]");
    if (!button) return;
    event.preventDefault();
    if (!window.confirm("AI 会逐题判分并写入成绩，确定继续吗？")) return;
    setBusy(button, true, "判题中…");
    try {
      const data = await api(button.dataset.autoGrade, { method: "POST" });
      toast(data.message || "自动判题完成。", "success");
      window.setTimeout(() => window.location.reload(), 500);
    } catch (error) {
      handleError(error);
    } finally {
      setBusy(button, false);
    }
  });

  /* ---------- Grading total preview ---------- */

  const gradingForm = document.getElementById("gradingForm");
  if (gradingForm) {
    const total = document.getElementById("gradeTotal");
    const update = () => {
      let score = 0;
      let max = 0;
      gradingForm.querySelectorAll("[data-score-input]").forEach((input) => {
        score += Number(input.value || 0);
        max += Number(input.max || 0);
      });
      if (total) {
        const percent = max ? Math.round((score / max) * 1000) / 10 : 0;
        total.textContent = `${score} / ${max} 分 · ${percent}%`;
      }
    };
    gradingForm.addEventListener("input", (event) => {
      if (event.target.matches("[data-score-input]")) update();
    });
    update();
  }

  /* ---------- Student assignment submission ---------- */

  const assignmentForm = document.getElementById("assignmentForm");
  if (assignmentForm) {
    assignmentForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const assignmentId = assignmentForm.dataset.assignmentId;
      const answers = [];
      assignmentForm.querySelectorAll(".question-block[data-question-id]").forEach((block) => {
        const questionId = Number(block.dataset.questionId);
        const type = block.dataset.questionType;
        let answer = "";
        if (type === "single_choice" || type === "true_false") {
          answer = block.querySelector('input[type="radio"]:checked')?.value || "";
        } else if (type === "multiple_choice") {
          answer = Array.from(block.querySelectorAll('input[type="checkbox"]:checked'))
            .map((input) => input.value)
            .join(", ");
        } else {
          answer = block.querySelector("textarea, input[type=\"text\"]")?.value.trim() || "";
        }
        answers.push({ question_id: questionId, answer });
      });
      const unanswered = answers.filter((item) => !item.answer).length;
      if (unanswered && !window.confirm(`还有 ${unanswered} 道题没有作答，仍然提交吗？`)) return;

      const button = assignmentForm.querySelector('button[type="submit"]');
      setBusy(button, true, "提交中…");
      try {
        await api("/submit_assignment_answers", {
          method: "POST",
          body: { assignment_id: Number(assignmentId), answers },
        });
        toast("作业已提交，等待批改。", "success");
        window.setTimeout(() => window.location.reload(), 450);
      } catch (error) {
        handleError(error);
      } finally {
        setBusy(button, false);
      }
    });
  }

  /* ---------- Manage students ---------- */

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-remove-student]");
    if (!button) return;
    event.preventDefault();
    if (!window.confirm(button.dataset.confirm || "确定将这名学生移出班级吗？")) return;
    setBusy(button, true, "移除中…");
    try {
      await api(button.dataset.removeStudent, { method: "POST" });
      button.closest("tr")?.remove();
      toast("学生已移出班级。", "success");
    } catch (error) {
      handleError(error);
    } finally {
      setBusy(button, false);
    }
  });

  /* ---------- Register role toggle ---------- */

  const registerForm = document.getElementById("registerForm");
  if (registerForm) {
    const roleInputs = registerForm.querySelectorAll('input[name="role"]');
    const keyField = document.getElementById("apiKeyField");
    const keyInput = keyField?.querySelector("input");
    const keyHint = document.getElementById("apiKeyHint");
    const sync = () => {
      const role = registerForm.querySelector('input[name="role"]:checked')?.value || "student";
      if (!keyField || !keyInput) return;
      keyInput.required = role === "teacher";
      if (keyHint) {
        keyHint.textContent =
          role === "teacher"
            ? "教师账户需要密钥才能使用 AI 出题与批改。"
            : "学生可以留空，之后使用绑定教师的密钥。";
      }
    };
    roleInputs.forEach((input) => input.addEventListener("change", sync));
    sync();
  }

  /* ---------- File previews ---------- */

  document.querySelectorAll("[data-file-preview]").forEach((input) => {
    input.addEventListener("change", () => {
      const target = document.querySelector(input.dataset.filePreview);
      if (!target) return;
      const file = input.files?.[0];
      if (!file || !file.type.startsWith("image/")) {
        target.removeAttribute("src");
        target.classList.add("hide");
        return;
      }
      const url = URL.createObjectURL(file);
      target.src = url;
      target.classList.remove("hide");
    });
  });

  /* ---------- Initial page hints ---------- */

  document.querySelectorAll("[data-initial-focus]").forEach((selector) => {
    const node = document.querySelector(selector.dataset.initialFocus);
    node?.focus();
  });
})();
