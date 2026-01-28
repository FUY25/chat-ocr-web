# 聊天截图 OCR Web 应用

上传聊天截图，自动识别对话内容并生成格式化的 TXT 文件。

## 功能特性

- 📤 支持批量上传（最多30张图片）
- 🔍 本地 OCR 自动识别对方姓名
- 🤖 Gemini AI 精准识别聊天内容
- 📦 打包下载（重命名图片 + TXT 文件）
- 📊 实时进度显示（SSE）

## 部署到 Render

### 1. Fork 或 Clone 此仓库

### 2. 在 Render 创建 Web Service
- 选择 "Web Service"
- 连接此 GitHub 仓库
- 使用以下配置：
  - **Environment**: Docker
  - **Instance Type**: Free (或更高)

### 3. 设置环境变量
在 Render Dashboard → Environment 中添加：
```
GEMINI_API_KEY=你的Gemini_API_Key
```

### 4. 部署
点击 Deploy，等待构建完成即可访问。

## 本地运行

```bash
# 安装依赖
pip install -r requirements.txt

# 设置 API Key
export GEMINI_API_KEY="your-api-key"

# 启动服务
python app.py
```

访问 http://localhost:5000

## 技术栈

- Flask + SSE 实时通信
- Gemini 2.5 Flash OCR
- RapidOCR 本地姓名识别
- Docker 容器化部署

## 文件结构

```
├── app.py              # Flask 主应用
├── ocr_core.py         # OCR 核心处理模块
├── templates/
│   └── index.html      # 前端页面
├── requirements.txt    # Python 依赖
├── Dockerfile          # Docker 配置
└── README.md           # 说明文档
```
