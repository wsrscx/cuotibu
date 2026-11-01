# 错题簿 · Cuotibu

一个本地优先的错题本与作业管理工具。学生记录错题、拆解错因、按遗忘曲线复习；教师上传教材页面、生成题目、布置作业并批改。界面是服务端渲染的 HTML/CSS，少量原生 JavaScript，无前端框架；数据默认只存在你自己的电脑上。

> 这是一个单机应用，不是云端 SaaS。默认监听 `127.0.0.1`，需要 OpenRouter API 密钥时才调用外部 AI。

## 功能

**学生**

- 多个错题本，支持图片、OCR 文字识别、分类、标签和搜索
- AI 分析错因、订正答案、改进建议
- 遗忘曲线复习计划、今日到期清单
- 基于错题生成练习题，AI 判题并记录正确率
- 查看教师布置的作业、提交答案、查看成绩和反馈
- 只读分享链接，可随时重新生成

**教师**

- 上传教材页面图片
- 从页面图片识别文字并生成单选、多选、判断、填空、简答题
- 创建作业、分配给自己班级的学生
- 手动批改或 AI 自动判题
- 错误答案自动收进学生的错题本
- 班级管理与学生绑定

## 快速开始

需要 Python 3.10 或更高版本（开发与打包使用 3.12+）。

```powershell
git clone https://github.com/wsrscx/cuotibu.git
cd cuotibu
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

浏览器会自动打开 <http://127.0.0.1:88>。第一次运行会在项目目录下创建 `data/`：

```text
data/
├─ notebook.db     SQLite 数据库
├─ uploads/        上传的图片
├─ secret.key      本地会话签名密钥
└─ app.log         运行日志
```

也可以在 PowerShell 里直接运行：

```powershell
python app.py
```

如果 88 端口被占用，程序会自动尝试 89–107。设置 `PORT` 可以指定起始端口。

### AI 配置

1. 在 <https://openrouter.ai/keys> 创建 API 密钥。
2. 注册教师账户时填入密钥，或登录后在「设置」中保存。
3. 学生账户可以不填密钥；教师把学生加入班级后，学生会使用教师的密钥。

默认模型可以通过环境变量覆盖：

```powershell
$env:OPENROUTER_TEXT_MODEL = "qwen/qwen2.5-72b-instruct:free"
$env:OPENROUTER_VISION_MODEL = "qwen/qwen2.5-vl-72b-instruct:free"
python app.py
```

免费模型可能限流或下线。可在 OpenRouter 控制台换成你账户可用的模型；模型名只影响 AI 功能，不影响错题本、作业和本地数据。

## 打包成单个 EXE

```powershell
pip install -r requirements-dev.txt
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
```

产物：

```text
dist\Cuotibu.exe
```

`data/` 会创建在 exe 所在目录旁边，所以可以把 exe 放进一个可写文件夹后双击运行。程序是单文件 PyInstaller 构建，启动时会解压模板和静态资源到临时目录；源代码、数据库和上传文件不打包进 exe。

注意事项：

- 未签名的 exe 可能触发 Windows SmartScreen 或杀毒软件误报，这是 PyInstaller 单文件程序的常见现象。
- 想换图标，运行 `python scripts/make_icon.py` 后重新构建；没有图标也能构建。
- 如果把 exe 放在 `Program Files` 等不可写目录，程序会回退到 `%LOCALAPPDATA%\Cuotibu` 保存数据。

## 测试

```powershell
python -m unittest discover -s tests -v
```

测试使用临时目录，不会污染 `data/`。覆盖注册登录、CSRF、角色隔离、错题本、分享、作业提交、批改、自动收录错题、文件校验和内网链接拦截。

## 项目结构

```text
.
├─ app.py                 Flask 应用、路由、权限、CSRF、文件与链接安全
├─ ai.py                  OpenRouter 客户端与提示词
├─ models.py              SQLAlchemy 模型
├─ database.py            SQLAlchemy 实例
├─ utils.py               复习曲线、统计、标签
├─ templates/             Jinja2 页面
├─ static/
│  ├─ app.css             设计系统与响应式样式
│  └─ app.js              原生 JavaScript 交互
├─ tests/test_app.py      冒烟与回归测试
├─ scripts/
│  ├─ seed_demo.py        可选的演示数据（生成 .demo-data/）
│  ├─ make_icon.py        生成 Windows 图标
│  └─ build_exe.ps1       测试 + PyInstaller 构建
├─ docs/AUDIT.md          本次代码审计与修复记录
├─ app.spec               PyInstaller 配置
├─ requirements.txt       运行时依赖
└─ requirements-dev.txt   测试与打包依赖
```

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CUOTIBU_DATA_DIR` | 开发时 `./data`，打包后 exe 同级 `data` | 数据库、上传和日志目录 |
| `SECRET_KEY` | 首次运行生成 `data/secret.key` | Flask 会话签名密钥 |
| `PORT` | `88` | 起始端口；被占用时自动递增 |
| `FLASK_DEBUG` | 关闭 | 开发者调试模式，不要在公开环境开启 |
| `CUOTIBU_NO_BROWSER` | 关闭 | 设为 `1` 时不自动打开浏览器，便于自动化测试 |
| `OPENROUTER_TEXT_MODEL` | `qwen/qwen2.5-72b-instruct:free` | 文本分析/出题模型 |
| `OPENROUTER_VISION_MODEL` | `qwen/qwen2.5-vl-72b-instruct:free` | 图片 OCR/视觉分析模型 |
| `OPENROUTER_URL` | OpenRouter 官方接口 | 兼容接口地址 |
| `AI_MAX_RETRIES` | `3` | AI 请求最大尝试次数 |

## 数据与隐私

- 数据库是本地 SQLite 文件，图片在本地 `uploads/`，不会上传到本项目的服务器（本项目没有服务器）。
- 密码使用 Werkzeug 的哈希存储。API 密钥只保存在本地 SQLite 中，界面只显示尾号；它不会写进日志。
- 分享链接是带随机 token 的只读页面；在错题本里点「重新生成」会让旧链接立即失效。
- 删除整个 `data/` 文件夹会清空所有账户、错题、作业、图片和日志。删除前请备份。
- 如果要迁移数据，复制整个 `data/` 目录即可；不要把 `data/` 或 `secret.key` 提交到 Git。
- 链接导入功能只允许公开的 `http(s)` 地址，并拦截内网、回环和保留地址，防止 SSRF。
- 这是一个本地单机应用：Flask 开发服务器只适合监听本机。如果要做多人部署，请换成正式 WSGI 服务器、启用 HTTPS、设置 `SECRET_KEY`，并自行评估 CSRF、备份和访问控制策略。

## 已知限制

- 教材按「一页一张图片」处理，不解析多页 PDF。
- SQLite 适合单机/小规模使用；并发写入多时需要换数据库。
- AI 输出质量取决于模型和密钥额度；出题后请教师检查再分配。
- 当前 UI 没有内置深浅色手动开关，跟随系统 `prefers-color-scheme`。

## 修复记录

这次审计修复了多个安全和稳定性问题，包括：取消 TLS 校验、链接导入 SSRF、越权修改复习计划、学生可进入教师作业页、缺失 CSRF、错误路径导致模板 500、缺失依赖、相对路径在 EXE 中失效、AI 请求无超时/无退避、选项字符串被逐字符拼接、重复提交与唯一约束缺失等。详见 [docs/AUDIT.md](docs/AUDIT.md)。

## License

[MIT](LICENSE)
