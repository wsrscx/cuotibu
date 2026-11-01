# 代码审计与修复记录

审计范围：`app.py`、`ai.py`、`models.py`、`utils.py`、`database.py`、全部 Jinja 模板、`static/app.js`、`static/app.css`、打包配置和依赖。

## 严重安全问题（已修复）

| 问题 | 影响 | 修复 |
| --- | --- | --- |
| `ai.py` 所有请求使用 `verify=False` | 关闭 TLS 证书校验，可被中间人攻击 | 改为 `verify=True`，并加入超时、有限重试和退避 |
| 链接导入直接请求用户提供的 URL | 可访问 `127.0.0.1`、内网服务、云元数据地址（SSRF） | 仅允许公网 `http(s)`，解析 DNS 后拒绝非 global IP，并逐跳校验重定向 |
| `/update_review_schedule` 只按 note_id 更新 | 任意登录学生可修改别人的错题复习计划（IDOR） | 校验错题属于当前用户 |
| `/check_answer` 接受任意 note_id | 可为他人的错题写入练习记录 | 校验来源错题所有权 |
| `/view_assignment/<id>` 只限制教师角色 | 学生可进入教师作业页，看到全班提交和分数 | 改为 `role_required("teacher")` + 作业所有者校验 |
| 教师可以把作业分配给任意学生 ID | 可把作业/成绩暴露给其他班级 | 只接受 `teacher_id == 当前教师` 的学生 |
| 教师可以用任意 textbook_id 创建作业 | 可引用其他教师的教材 | 校验教材所有者 |
| 所有 POST 都没有 CSRF 保护 | 第三方页面可诱导提交操作 | 会话 CSRF token；表单隐藏字段 + fetch `X-CSRF-Token` |
| `SECRET_KEY` 硬编码 | 会话可伪造 | 环境变量或首次运行生成 `data/secret.key` |
| `debug=True` 固定开启 | 暴露调试器/堆栈 | 默认关闭，仅 `FLASK_DEBUG=1` 时开发模式 |
| 上传只检查扩展名 | 可上传伪装文件 | 校验 PNG/JPEG/GIF/BMP/WebP 文件签名，限制 10 MB，文件名改为 UUID |
| 分享链接可长期泄露 | 无法撤回公开页面 | 增加「重新生成分享链接」，旧 token 立即失效 |

## 功能与稳定性 Bug（已修复）

| 问题 | 修复 |
| --- | --- |
| `templates/textbook.html` 缺失，教师点教材直接 500 | 新增教材详情页 |
| 开发路径写死为 `abspath("源程序")`，在源目录运行会找 `源程序/源程序/templates` | 使用 `Path(__file__).parent` 定位资源 |
| 上传目录和数据库路径相对当前工作目录 | 统一到可写 `data/` 目录；打包后位于 exe 同级，不可写时回退到 `%LOCALAPPDATA%` |
| `requirements.txt` 缺少 `beautifulsoup4` 和 `PyPDF2`，且版本过旧 | 重写依赖；删掉未使用的 PyPDF2 导入 |
| `check_answer_with_ai` 对 `options` 字符串做 `join`，会逐字符拼接 | 统一解析 JSON 数组/字符串为列表 |
| AI 请求没有 timeout，失败后 7 次立即重试 | 连接/读取超时 + 指数退避 + 最多 3 次 |
| AI 返回 Markdown 代码块时 JSON 解析失败 | 先剥离代码围栏，再提取第一个对象/数组 |
| 生成题目 prompt 把 `# 限制内容长度` 写进提示词 | 重写提示词并截断内容 |
| AI 失败被静默吞掉，返回「未分类」假结果 | 抛出 `AIError`，路由返回 502 并保留用户输入 |
| 手机端提交作业时对不存在的按钮 `addEventListener` | 只有可提交状态才渲染表单；提交逻辑整体重写 |
| 学生可提交不属于该作业的 question_id | 校验题目 ID 集合 |
| 学生可在已提交/已批改后重复提交 | 只允许 `assigned` / `in_progress` |
| 同一学生对同一作业可重复分配 | 增加 `(student_id, assignment_id)` 唯一约束 |
| 同一题可重复答案 | 增加 `(student_assignment_id, question_id)` 唯一约束 |
| 删除错题/错题本时上传图片残留 | 删除时清理对应文件 |
| 教师重复批改会把同一错题重复收进错题本 | 用内容哈希去重 |
| `datetime.utcnow` 与本地时间混用，复习日期显示偏移 | 统一使用本地时间 |
| 教师重复保存批改时总分为 0 / 越界 | 分数裁剪到 `0..题目分值`，按全部题目计算 |
| 错误处理没有 404/403/413 页面 | 增加页面和 JSON 错误响应 |
| 静态资源走 CDN，离线时页面无样式 | 设计系统和脚本全部本地化，无外部依赖 |
| 旧模板大量未使用代码、失效按钮、重复 JS | 重写全部页面和 `app.js`，删除死代码 |

## 架构与数据调整

- 通过 `database.py` 保持 `db` 单例，模型集中在 `models.py`，避免循环导入。
- SQLite 连接启用 `foreign_keys=ON` 和 WAL。
- 路径与数据目录：
  - 开发：项目根目录 `data/`
  - EXE：`Cuotibu.exe` 同级 `data/`
  - 只读目录回退：`%LOCALAPPDATA%\Cuotibu`
- 上传文件不再放在 `static/uploads`，而是 `data/uploads`，通过 `/uploads/<filename>` 路由提供，避免打包后静态目录只读。
- 错误页面、空状态、加载状态和无障碍焦点样式补齐。
- CSP 限制为 `default-src 'self'`，脚本全部外置；上传图片仅允许同源和 `data:`。
- 分享页面增加 `noindex`。

## 前端重写

- 设计方向：纸张/墨水/朱红的编辑风格，不使用渐变、玻璃拟态、Emoji 或通用 AI 紫蓝配色。
- 统一设计变量，支持系统深色模式，响应式布局从 360px 到 1440px。
- 所有页面重写：首页、登录、注册、学生工作台、教师工作台、错题本、教材、作业、批改、学生作业、班级、设置、只读分享、403/404/500。
- 原生 `<dialog>`、事件委托、CSRF fetch 封装、Toast、加载态、确认删除、图片预览、文件输入校验。
- 移除 Bootstrap、Bootstrap Icons、Chart.js 和旧 `script.js`；图表改为轻量 CSS 条形图。

## 验证

- `python -m unittest discover -s tests -v`：8 个测试通过。
- 使用演示数据库人工检查学生/教师全部页面、笔记弹窗、分享弹窗、复习计划降级、移动端导航和响应式布局。
- `python -m py_compile` 通过。
- EXE 构建后从独立目录启动，验证 `/health`、首页、登录、静态资源和数据目录创建。

## 仍存在的边界

- API 密钥以明文保存在本地 SQLite 中；这是单机应用的权衡。不要把 `data/` 提交或分享。
- 应用使用 Flask 开发服务器监听本机，定位是桌面单机工具，不是公网服务。
- 免费 OpenRouter 模型可能限流或下线，需要用户自行替换模型名。
- 教材按图片页处理，不支持完整 PDF 解析。
- 单文件 exe 未签名，Windows 可能提示未知发布者。
