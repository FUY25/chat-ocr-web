"""
聊天截图 OCR Web 应用
====================
Flask + SSE 实现实时进度更新
"""

import os
import json
import queue
import threading
from flask import Flask, render_template, request, Response, send_file, jsonify
from werkzeug.utils import secure_filename
from io import BytesIO

from ocr_core import process_ocr_workflow, get_today_beijing

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max

# 存储每个任务的进度队列
progress_queues = {}


@app.route('/')
def index():
    """主页"""
    today = get_today_beijing()
    return render_template('index.html', today=today)


@app.route('/api/process', methods=['POST'])
def process():
    """处理 OCR 请求"""
    try:
        # 获取表单数据
        city = request.form.get('city', '城市')
        house_type = request.form.get('house_type', '二手房')
        community = request.form.get('community', '小区')
        recipient = request.form.get('recipient', '经纪人')
        screenshot_date = request.form.get('screenshot_date', get_today_beijing())
        
        # 获取上传的图片
        files = request.files.getlist('images')
        if not files:
            return jsonify({'error': '请上传图片'}), 400
        
        if len(files) > 30:
            return jsonify({'error': '最多支持30张图片'}), 400
        
        # 读取图片
        images = []
        for f in files:
            filename = secure_filename(f.filename) or 'image.png'
            img_bytes = f.read()
            images.append((filename, img_bytes))
        
        # 生成任务 ID
        task_id = os.urandom(8).hex()
        progress_queues[task_id] = queue.Queue()
        
        # 进度回调
        def progress_callback(msg, current, total):
            progress_queues[task_id].put({
                'type': 'progress',
                'message': msg,
                'current': current,
                'total': total
            })
        
        # 获取 API Key
        api_key = os.environ.get('GEMINI_API_KEY')
        if not api_key:
            return jsonify({'error': '服务器未配置 GEMINI_API_KEY'}), 500
        
        # 在后台线程处理
        def process_task():
            try:
                zip_bytes = process_ocr_workflow(
                    images=images,
                    city=city,
                    house_type=house_type,
                    community=community,
                    recipient=recipient,
                    screenshot_date=screenshot_date,
                    api_key=api_key,
                    progress_callback=progress_callback
                )
                
                # 发送完成信号和下载链接
                progress_queues[task_id].put({
                    'type': 'complete',
                    'download_id': task_id
                })
                
                # 存储 ZIP 数据供下载
                app.config[f'zip_{task_id}'] = zip_bytes
                
            except Exception as e:
                progress_queues[task_id].put({
                    'type': 'error',
                    'message': str(e)
                })
        
        thread = threading.Thread(target=process_task)
        thread.start()
        
        return jsonify({'task_id': task_id})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/progress/<task_id>')
def progress(task_id):
    """SSE 进度流"""
    def generate():
        q = progress_queues.get(task_id)
        if not q:
            yield f"data: {json.dumps({'type': 'error', 'message': '任务不存在'})}\n\n"
            return
        
        while True:
            try:
                data = q.get(timeout=30)
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                
                if data.get('type') in ('complete', 'error'):
                    break
            except queue.Empty:
                # 发送心跳保持连接
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                break
    
    response = Response(generate(), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'  # 禁用 nginx 缓冲
    response.headers['Connection'] = 'keep-alive'
    return response


@app.route('/api/download/<download_id>')
def download(download_id):
    """下载 ZIP 文件"""
    zip_bytes = app.config.get(f'zip_{download_id}')
    if not zip_bytes:
        return jsonify({'error': '下载链接已过期'}), 404
    
    # 清理数据
    del app.config[f'zip_{download_id}']
    if download_id in progress_queues:
        del progress_queues[download_id]
    
    return send_file(
        BytesIO(zip_bytes),
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'chat_ocr_result.zip'
    )


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
