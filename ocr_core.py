"""
聊天截图 OCR 核心模块
====================
提供可复用的 OCR 处理函数，支持进度回调。
"""

from datetime import datetime
from pathlib import Path
import re
import os
import shutil
import tempfile
import zipfile
from collections import defaultdict
from typing import Callable, Optional
from io import BytesIO

from PIL import Image
from google import genai
from google.genai import types

try:
    import pytz
    BEIJING_TZ = pytz.timezone('Asia/Shanghai')
except ImportError:
    BEIJING_TZ = None

# 本地 OCR（可通过环境变量禁用，用于云端部署）
LOCAL_OCR = None
if os.environ.get('DISABLE_LOCAL_OCR', '').lower() not in ('1', 'true', 'yes'):
    try:
        from rapidocr_onnxruntime import RapidOCR
        LOCAL_OCR = RapidOCR()
        print("✅ 本地 OCR 已启用 (RapidOCR)")
    except Exception as e:
        print(f"⚠️ 本地 OCR 加载失败: {e}")
        LOCAL_OCR = None
else:
    print("ℹ️ 本地 OCR 已禁用 (DISABLE_LOCAL_OCR=true)")


# =============================================================================
# 工具函数
# =============================================================================

def sanitize(text: str, fallback: str) -> str:
    """清理文件名中的非法字符"""
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", text.strip())
    cleaned = cleaned.replace(" ", "")
    return cleaned or fallback


def is_time_like(text: str) -> bool:
    return bool(re.fullmatch(r"\d{1,2}:\d{2}", text))


def mostly_digits(text: str) -> bool:
    if not text:
        return False
    digits = sum(ch.isdigit() for ch in text)
    chinese = sum("\u4e00" <= ch <= "\u9fff" for ch in text)
    return digits >= max(3, int(len(text) * 0.7)) or digits >= chinese * 2


def pick_top_name(results) -> str:
    """从 OCR 结果中提取最顶部的人名"""
    if not results:
        return "对方姓名"

    candidates = []
    for item in results:
        box, text = item[0], str(item[1]).strip()
        if not text or is_time_like(text) or mostly_digits(text):
            continue
        top_y = min(point[1] for point in box)
        candidates.append((top_y, text))

    if not candidates:
        top_item = min(results, key=lambda item: min(point[1] for point in item[0]))
        return str(top_item[1]).strip() or "对方姓名"

    candidates.sort(key=lambda t: t[0])
    return candidates[0][1]


def get_today_beijing() -> str:
    """获取北京时间今天日期 MM-DD"""
    if BEIJING_TZ:
        return datetime.now(BEIJING_TZ).strftime("%m-%d")
    return datetime.now().strftime("%m-%d")


def get_today_full() -> str:
    """获取北京时间今天完整日期 YYYYMMDD"""
    if BEIJING_TZ:
        return datetime.now(BEIJING_TZ).strftime("%Y%m%d")
    return datetime.now().strftime("%Y%m%d")


# =============================================================================
# OCR 处理
# =============================================================================

def detect_name_with_gemini(img_bytes: bytes, client) -> str:
    """使用 Gemini 识别聊天截图中的对方姓名（仅返回姓名，用最快的模型）"""
    try:
        img = Image.open(BytesIO(img_bytes))
        response = client.models.generate_content(
            model="gemini-2.0-flash-lite",  # 最快最便宜的模型，适合简单任务
            contents=[
                "这是一个聊天截图。请识别屏幕顶部显示的对方姓名/昵称，只返回这个名字，不要返回任何其他内容。如果无法识别，返回'对方'。",
                img
            ]
        )
        name = response.text.strip()
        # 清理可能的多余内容
        name = name.split('\n')[0].strip()
        return sanitize(name, "对方") if name else "对方"
    except Exception as e:
        print(f"Gemini 姓名识别失败: {e}")
        return "对方"


def detect_name_from_image(img_bytes: bytes, gemini_client=None) -> str:
    """从图片中检测对方姓名（优先本地 OCR，失败则用 Gemini）"""
    # 尝试本地 OCR
    if LOCAL_OCR is not None:
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(img_bytes)
                temp_path = f.name
            
            results, _ = LOCAL_OCR(temp_path)
            name = sanitize(pick_top_name(results), "")
            if name:
                return name
        except Exception as e:
            print(f"本地 OCR 失败: {e}")
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except:
                    pass
    
    # Fallback: 使用 Gemini
    if gemini_client:
        return detect_name_with_gemini(img_bytes, gemini_client)
    
    return "对方"


def ocr_images_with_gemini(
    images: list[tuple[str, bytes]],  # [(filename, bytes), ...]
    client,
    screenshot_date: str
) -> str:
    """
    使用 Gemini API OCR 多张图片，返回格式化的聊天记录
    """
    prompt = f"""请 OCR 这些聊天截图并按以下格式输出聊天记录：

格式要求：
1. 每条消息格式: "我: 消息内容" 或 "对方: 消息内容"
2. 发送者识别规则：
   - "我"：绿色气泡，位于屏幕右侧，右对齐
   - "对方"：灰色/白色气泡，位于屏幕左侧，左对齐，左边通常有头像
   - 注意：即使是系统卡片消息，也要根据位置判断是谁触发的
3. 卡片式消息（房源信息等）标记为【平台自动信息】
4. 如果消息显示 "(已读)"，保留这个标记
5. 聊天框中出现的时间用单独一行记录，格式: MM-DD HH:MM（例如 {screenshot_date} 16:32）
6. 按时间顺序输出，从最早到最新
7. 只输出聊天内容，不要添加任何解释或标题

示例输出:
{screenshot_date} 16:32
我: 保利恒尊•崇璟和颂府【平台自动信息】 (已读)
我: 我刚浏览了这个楼盘，请问可以介绍下吗？【平台自动信息】 (已读)
对方: 您好
{screenshot_date} 17:30
我: 请问这个小区周边有污染问题吗？
对方: 没听说过这个事情

【重要补充说明】

截图日期：{screenshot_date}（北京时间）
- 聊天中显示的时间如 "11:34"，请输出为 "{screenshot_date} 11:34"

平台自动信息的识别标准 - 以下类型都必须标注【平台自动信息】：
- 房源卡片（带图片、价格、小区名的卡片）(如果是出现在chat的第一条，一般是"我"发的，注意分辨)
- 授权请求（灰色框 + "同意"/"授权"按钮，如"是否同意我帮您找房？"）
- 微信聊天邀请（带"立即加入"按钮）
- 经纪人名片（带头像、神奇分、门店信息）
- 任何带交互按钮的系统卡片

请现在开始 OCR 这些图片："""

    # 构建内容：prompt + 所有图片
    contents = [prompt]
    for filename, img_bytes in images:
        try:
            img = Image.open(BytesIO(img_bytes))
            contents.append(img)
        except Exception as e:
            print(f"警告: 无法加载图片 {filename}: {e}")

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents
    )

    return response.text


# =============================================================================
# 主处理流程
# =============================================================================

def process_ocr_workflow(
    images: list[tuple[str, bytes]],  # [(original_filename, bytes), ...]
    city: str,
    house_type: str,
    community: str,
    recipient: str,
    screenshot_date: str,
    api_key: str,
    progress_callback: Optional[Callable[[str, int, int], None]] = None
) -> bytes:
    """
    处理 OCR 工作流，返回 ZIP 文件的 bytes
    
    Args:
        images: 图片列表 [(filename, bytes), ...]
        city: 城市
        house_type: 房源类型
        community: 小区
        recipient: 发送对象
        screenshot_date: 截图日期 MM-DD
        api_key: Gemini API Key
        progress_callback: 进度回调 (message, current, total)
    
    Returns:
        ZIP 文件的 bytes
    """
    def report(msg: str, current: int = 0, total: int = 0):
        if progress_callback:
            progress_callback(msg, current, total)
    
    # 清理输入
    city = sanitize(city, "城市")
    house_type = sanitize(house_type, "房源")
    community = sanitize(community, "小区")
    recipient = sanitize(recipient, "对象")
    
    total_images = len(images)
    report(f"开始处理 {total_images} 张图片", 0, total_images)
    
    # 初始化 Gemini 客户端（用于 OCR 和可能的姓名识别 fallback）
    report("正在连接 Gemini...", 0, total_images)
    try:
        client = genai.Client(api_key=api_key)
    except Exception as e:
        report(f"❌ Gemini 连接失败: {e}", 0, 0)
        raise Exception(f"Gemini API 连接失败: {e}")
    
    # 步骤1: 检测每张图片的对方姓名并重命名
    ocr_method = "本地识别" if LOCAL_OCR else "Gemini识别"
    report(f"正在识别对方姓名（{ocr_method}）...", 0, total_images)
    
    renamed_images = []  # [(new_filename, bytes, person_name), ...]
    per_name_counter = {}
    
    for i, (orig_name, img_bytes) in enumerate(images):
        try:
            person_name = detect_name_from_image(img_bytes, gemini_client=client)
        except Exception as e:
            report(f"⚠️ 姓名识别异常: {e}", i + 1, total_images)
            person_name = "对方"
        
        order = per_name_counter.get(person_name, 0) + 1
        per_name_counter[person_name] = order
        
        # 获取扩展名
        ext = Path(orig_name).suffix.lower() or ".png"
        new_name = f"{city}-{house_type}-{community}-{recipient}-{person_name}-{order}{ext}"
        
        report(f"→ {new_name}", i + 1, total_images)
        
        renamed_images.append((new_name, img_bytes, person_name))
        
    
    # 步骤2: 按对方姓名分组
    report("正在分组图片...", 0, 0)
    
    groups = defaultdict(list)
    for new_name, img_bytes, person_name in renamed_images:
        groups[person_name].append((new_name, img_bytes))
    
    report(f"共 {len(groups)} 个对话", 0, len(groups))
    
    # 步骤3: 对每组调用 Gemini 分析
    report("Gemini 正在分析聊天内容...", 0, len(groups))
    
    txt_files = {}  # {filename: content}
    group_list = list(groups.items())
    
    for i, (person_name, imgs) in enumerate(group_list):
        report(f"正在分析: {person_name}（{len(imgs)} 张图片）", i + 1, len(groups))
        
        chat_content = ocr_images_with_gemini(imgs, client, screenshot_date)
        txt_name = f"{city}-{house_type}-{community}-{recipient}-{person_name}.txt"
        txt_files[txt_name] = chat_content
    
    # 步骤4: 打包 ZIP
    report("正在打包下载文件...", 0, 0)
    
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        # 添加图片
        for new_name, img_bytes, _ in renamed_images:
            zf.writestr(new_name, img_bytes)
        
        # 添加 TXT
        for txt_name, content in txt_files.items():
            zf.writestr(txt_name, content.encode('utf-8'))
    
    report("完成！", len(groups), len(groups))
    
    return zip_buffer.getvalue()
