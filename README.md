# KnowLearn 智能教育知识库

面向教育培训场景的 RAG 知识库：导入课程目录、题库、课程讲义和项目文档，提供课程介绍、课程详情、文档检索、题目检索和基于知识库的问答（流式输出、多轮对话、带引用来源）。

## 功能

| 模块 | 说明 |
| --- | --- |
| 内容导入 | 支持 `.md` / `.docx` / `.pdf` / `.zip`（1 个 md + 其引用的图片）。后台排队执行，导入页可以看到每个步骤的状态、进度和失败原因；同名文件覆盖导入 |
| 课程介绍 / 详情 | 课程目录按系列解析，自动推导先修要求、项目实战内容和关联题库 |
| 文档检索 | 讲义、项目文档按章节切分，来源精确到章节路径；图片由视觉模型生成摘要后参与检索，回答中可以展示 |
| 题库检索 | 按题目解析题型、选项、答案、解析和课程归属，支持按题型过滤（「选择题」= 单选题 + 多选题） |
| 知识问答 | 问题改写 → 知识库混合检索 / HyDE / 联网搜索三路召回 → RRF 融合 → 重排 → 流式生成；回答带 `[n]` 引用和来源卡片 |
| 会话 | 多会话列表、历史记录、停止生成、失败重试 |

问课程、问题目时只使用本平台资料，不联网；其他问题以知识库为主、联网结果作为补充，联网失败时自动降级为只用知识库。

## 架构

```text
浏览器（static/import.html、chat.html）
        │ HTTP / SSE
FastAPI 单服务（端口 8010）
 ├─ api/        路由层
 ├─ services/   导入服务 + 后台导入线程、会话服务、问答服务
 ├─ pipelines/  LangGraph：导入图（6 个节点）、问答图（改写 → 三路检索 → 融合 → 重排 → 生成）
 ├─ parsers/    纯函数解析器：课程目录、题库、Markdown 切分、图片引用
 └─ infra/      Milvus、MongoDB、MinIO、本机模型、硅基流动、MinerU、百炼 WebSearch 客户端
```

| 依赖 | 用途 |
| --- | --- |
| Milvus 2.5 | 知识内容 + 稠密/稀疏向量（集合 `knowlearn_chunks`） |
| MongoDB 7 | 导入任务、文件登记、会话、消息（库 `knowlearn`） |
| MinIO | 原文件、转换后的 Markdown、图片（桶 `knowlearn`，公开只读） |
| 本机 GPU 模型 | `BAAI/bge-m3`（向量化）、`BAAI/bge-reranker-large`（重排），fp16 合计约 2.1 GB 显存 |
| 硅基流动 | 对话 `deepseek-ai/DeepSeek-V4-Flash`、视觉 `deepseek-ai/DeepSeek-V4.1-Flash` |
| MinerU 云端 API | docx / pdf 转 Markdown（PDF 单文件不超过 200 页） |
| 阿里云百炼 | WebSearch MCP 联网搜索 |

## 环境要求

- Windows / Linux，Python 3.13，[uv](https://docs.astral.sh/uv/)
- NVIDIA GPU（CUDA 12.6），空闲显存 ≥ 2.5 GB；**只在 GPU 上运行**，没有 GPU 时启动会直接报错
- 可以访问 Milvus、MongoDB、MinIO（本项目部署在虚拟机，见 `.env.example`）

## 快速开始

1. 准备本机模型，并在 `.env` 中配置路径：`bge-m3`、`bge-reranker-large`。
2. torch 使用本地 wheel（`pyproject.toml` 的 `[tool.uv.sources]`），按实际路径修改后安装依赖：

```bash
uv sync
```

3. 复制配置并填写密钥（`.env` 已被 `.gitignore` 忽略）：

```bash
cp .env.example .env
```

| 配置项 | 说明 |
| --- | --- |
| `SILICONFLOW_API_KEY` | 硅基流动 |
| `DASHSCOPE_API_KEY` | 阿里云百炼（WebSearch MCP） |
| `MINERU_API_TOKEN` | MinerU（mineru.net 的 API 管理页面创建） |
| `MONGO_URL`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY` | 中间件账号 |

4. 启动服务（首次启动会自动创建集合、索引和桶）：

```bash
uv run python -m app
```

5. 打开导入页 http://127.0.0.1:8010/import.html ，问答页 http://127.0.0.1:8010/chat.html 。

## 导入资料

可以在导入页逐个上传，也可以批量导入整个资料目录：

```bash
uv run python scripts/import_all.py "<资料目录>/1.资料/教育/数据"
```

顺序：先导入课程目录（题库的课程归属依赖它），再导入题库、讲义和项目文档。`项目文档/<项目名>/*.md` 会和各自引用的图片一起自动打成 zip 上传。

## 测试

```bash
# 单元测试（不依赖中间件）
uv run pytest

# 集成测试（需要先启动服务；只创建、删除自己的会话，不改动知识库）
uv run pytest tests/integration -m integration

# 评测与性能基线（15 个典型问题）
uv run python scripts/eval.py --out docs/05_测试/评测结果.md
```

## 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/health` | 健康检查 |
| GET | `/api/v1/imports/suggest` | 根据文件名生成默认课程名或项目名 |
| POST | `/api/v1/imports` | 上传并创建导入任务 |
| GET | `/api/v1/imports/{task_id}` / `/api/v1/imports` | 任务详情 / 任务列表 |
| POST / GET | `/api/v1/sessions` | 新建会话 / 会话列表 |
| DELETE | `/api/v1/sessions/{id}` | 删除会话 |
| GET | `/api/v1/sessions/{id}/messages` | 消息记录 |
| POST | `/api/v1/sessions/{id}/chat` | 提问（`stream=true` 时返回 SSE：`progress` → `sources` → `delta` → `done` / `error`） |

统一响应格式：`{"code": 0, "message": "ok", "data": ...}`，错误码见 `app/core/errors.py`。

## 目录

```text
app/            后端代码（见「架构」）
static/         前端页面、公共样式与脚本、本地化的 marked / DOMPurify
scripts/        批量导入、评测、技术验证脚本（spikes/）
tests/          单元测试、集成测试（integration/）、真实资料样本（fixtures/）
```
