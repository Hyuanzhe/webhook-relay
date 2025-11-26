#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
    🔄 Webhook 中繼站 v2.0 - 雲端部署版
================================================================================

支援部署到：
    - Railway (推薦，完全免費)
    - Render
    - Vercel
    - 任何支援 Python 的雲端平台

功能：
    - 接收多台電腦的 BOSS 通知
    - 使用輪詢(Round Robin)平均分配到多個 Discord Webhook
    - 支援圖片轉發
    - Web 管理介面
    - 密碼保護（可選）

作者: @yyv3vnn
================================================================================
"""

import json
import os
import threading
import time
import requests
import base64
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string, Response
from functools import wraps
from collections import deque
import logging

# ================================================================================
# 環境變數配置（部署時在平台設定）
# ================================================================================

# 管理密碼（可選，留空則不需要密碼）
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')

# 預設 Webhook URLs（用逗號分隔）
# 例如: "https://discord.com/xxx,https://discord.com/yyy"
DEFAULT_WEBHOOKS_STR = os.environ.get('WEBHOOKS', '')

# 連接埠（雲端平台會自動設定）
PORT = int(os.environ.get('PORT', 5000))

# ================================================================================
# 日誌設定
# ================================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ================================================================================
# Flask 應用程式
# ================================================================================

app = Flask(__name__)

# ================================================================================
# 中繼站核心類別
# ================================================================================

class WebhookRelay:
    """
    Webhook 中繼站 - 雲端版
    
    使用輪詢(Round Robin)演算法將通知平均分配到多個 Webhook
    支援多台電腦同時發送
    """
    
    def __init__(self):
        """初始化中繼站"""
        self.webhooks = []
        self.current_index = 0
        self.lock = threading.Lock()
        self.stats = {
            "total_received": 0,
            "total_sent": 0,
            "failed_count": 0,
            "webhook_stats": {},
            "source_stats": {},  # 記錄各來源 IP 的統計
        }
        self.history = deque(maxlen=100)  # 最近100條記錄
        self.start_time = datetime.now()
        
        # 從環境變數載入 Webhook
        self._load_from_env()
        
        logger.info("=" * 60)
        logger.info("🔄 Webhook 中繼站 v2.0 (雲端版) 已啟動")
        logger.info(f"📡 已配置 {len(self.webhooks)} 個 Webhook")
        logger.info(f"🔐 密碼保護: {'啟用' if ADMIN_PASSWORD else '停用'}")
        logger.info("=" * 60)
    
    def _load_from_env(self):
        """從環境變數載入 Webhook"""
        if DEFAULT_WEBHOOKS_STR:
            urls = [url.strip() for url in DEFAULT_WEBHOOKS_STR.split(',') if url.strip()]
            for url in urls:
                if url.startswith('https://'):
                    self.webhooks.append(url)
                    self.stats["webhook_stats"][url] = {"sent": 0, "failed": 0}
            logger.info(f"✅ 從環境變數載入 {len(self.webhooks)} 個 Webhook")
    
    def add_webhook(self, url):
        """添加 Webhook URL"""
        with self.lock:
            if not url or not url.startswith("https://"):
                return False, "無效的 Webhook URL（必須以 https:// 開頭）"
            
            if url in self.webhooks:
                return False, "此 Webhook 已存在"
            
            self.webhooks.append(url)
            self.stats["webhook_stats"][url] = {"sent": 0, "failed": 0}
            logger.info(f"➕ 已添加 Webhook: {url[:50]}...")
            return True, "添加成功"
    
    def remove_webhook(self, index):
        """移除 Webhook URL"""
        with self.lock:
            if 0 <= index < len(self.webhooks):
                removed = self.webhooks.pop(index)
                if self.current_index >= len(self.webhooks) and len(self.webhooks) > 0:
                    self.current_index = 0
                logger.info(f"➖ 已移除 Webhook: {removed[:50]}...")
                return True
            return False
    
    def get_next_webhook(self):
        """獲取下一個要發送的 Webhook (輪詢演算法)"""
        with self.lock:
            if not self.webhooks:
                return None, -1
            
            webhook = self.webhooks[self.current_index]
            index = self.current_index
            self.current_index = (self.current_index + 1) % len(self.webhooks)
            
            return webhook, index
    
    def relay_message(self, content, image_data=None, source_ip="unknown"):
        """
        中繼訊息到下一個 Webhook
        
        Args:
            content: 文字內容
            image_data: 圖片二進制數據（可選）
            source_ip: 來源 IP
        
        Returns:
            tuple: (success, message, webhook_index)
        """
        self.stats["total_received"] += 1
        
        # 記錄來源統計
        if source_ip not in self.stats["source_stats"]:
            self.stats["source_stats"][source_ip] = 0
        self.stats["source_stats"][source_ip] += 1
        
        webhook_url, index = self.get_next_webhook()
        
        if not webhook_url:
            logger.error("❌ 無可用的 Webhook")
            self.stats["failed_count"] += 1
            return False, "無可用的 Webhook，請先添加", -1
        
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # 發送到 Discord
            if image_data:
                files = {'file': ('boss_screenshot.png', image_data, 'image/png')}
                data = {'content': content}
                response = requests.post(webhook_url, data=data, files=files, timeout=30)
            else:
                payload = {"content": content}
                response = requests.post(webhook_url, json=payload, timeout=15)
            
            if response.status_code in [200, 204]:
                self.stats["total_sent"] += 1
                self.stats["webhook_stats"][webhook_url]["sent"] += 1
                
                self.history.appendleft({
                    "time": timestamp,
                    "content": content[:80] + "..." if len(content) > 80 else content,
                    "webhook_index": index + 1,
                    "source": source_ip[-15:] if len(source_ip) > 15 else source_ip,
                    "has_image": bool(image_data),
                    "status": "✅"
                })
                
                logger.info(f"✅ [{source_ip}] 訊息已發送到 Webhook #{index + 1}")
                return True, "發送成功", index + 1
            else:
                raise Exception(f"HTTP {response.status_code}")
                
        except Exception as e:
            self.stats["failed_count"] += 1
            self.stats["webhook_stats"][webhook_url]["failed"] += 1
            
            self.history.appendleft({
                "time": timestamp,
                "content": content[:80] + "..." if len(content) > 80 else content,
                "webhook_index": index + 1,
                "source": source_ip[-15:] if len(source_ip) > 15 else source_ip,
                "has_image": bool(image_data),
                "status": f"❌ {str(e)[:30]}"
            })
            
            logger.error(f"❌ [{source_ip}] 發送失敗: {e}")
            return False, str(e), index + 1
    
    def get_stats(self):
        """獲取統計資訊"""
        uptime = datetime.now() - self.start_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        
        return {
            "uptime": f"{hours}h {minutes}m {seconds}s",
            "webhooks_count": len(self.webhooks),
            "current_index": self.current_index,
            "total_received": self.stats["total_received"],
            "total_sent": self.stats["total_sent"],
            "failed_count": self.stats["failed_count"],
            "success_rate": f"{(self.stats['total_sent'] / max(1, self.stats['total_received']) * 100):.1f}%",
            "source_count": len(self.stats["source_stats"]),
            "webhook_details": [
                {
                    "index": i + 1,
                    "url_preview": f"...{url[-30:]}" if len(url) > 35 else url,
                    "sent": self.stats["webhook_stats"].get(url, {}).get("sent", 0),
                    "failed": self.stats["webhook_stats"].get(url, {}).get("failed", 0),
                    "is_next": i == self.current_index
                }
                for i, url in enumerate(self.webhooks)
            ],
            "sources": [
                {"ip": ip[-20:] if len(ip) > 20 else ip, "count": count}
                for ip, count in sorted(
                    self.stats["source_stats"].items(), 
                    key=lambda x: x[1], 
                    reverse=True
                )[:10]
            ]
        }


# 創建全局中繼站實例
relay = WebhookRelay()

# ================================================================================
# 密碼驗證裝飾器
# ================================================================================

def check_auth(username, password):
    """檢查用戶名和密碼"""
    return password == ADMIN_PASSWORD

def authenticate():
    """返回 401 認證請求"""
    return Response(
        '需要密碼才能訪問管理介面\n',
        401,
        {'WWW-Authenticate': 'Basic realm="Webhook Relay Admin"'}
    )

def requires_auth(f):
    """需要認證的裝飾器"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not ADMIN_PASSWORD:
            return f(*args, **kwargs)
        
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# ================================================================================
# Web 介面模板
# ================================================================================

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🔄 Webhook 中繼站</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Microsoft JhengHei', 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #0f0f23 0%, #1a1a3e 50%, #0f0f23 100%);
            min-height: 100vh;
            color: #fff;
            padding: 15px;
        }
        .container { max-width: 900px; margin: 0 auto; }
        h1 {
            text-align: center;
            margin-bottom: 20px;
            font-size: 1.8em;
            background: linear-gradient(90deg, #00d4ff, #00ff88);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            text-shadow: 0 0 30px rgba(0,212,255,0.3);
        }
        .subtitle {
            text-align: center;
            color: #888;
            margin-bottom: 25px;
            font-size: 0.9em;
        }
        .card {
            background: rgba(255,255,255,0.05);
            border-radius: 12px;
            padding: 18px;
            margin-bottom: 15px;
            border: 1px solid rgba(255,255,255,0.1);
        }
        .card h2 {
            color: #00d4ff;
            margin-bottom: 12px;
            font-size: 1.1em;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
            gap: 10px;
        }
        .stat-box {
            background: rgba(0,212,255,0.08);
            border-radius: 8px;
            padding: 12px;
            text-align: center;
        }
        .stat-box .value {
            font-size: 1.6em;
            font-weight: bold;
            color: #00d4ff;
        }
        .stat-box .label { font-size: 0.75em; opacity: 0.7; margin-top: 3px; }
        .webhook-item {
            background: rgba(255,255,255,0.03);
            border-radius: 8px;
            padding: 10px 12px;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.85em;
        }
        .webhook-item.next { 
            border-left: 3px solid #00ff88;
            background: rgba(0,255,136,0.05);
        }
        .webhook-url { font-family: monospace; opacity: 0.8; word-break: break-all; }
        .webhook-stats { font-size: 0.8em; opacity: 0.6; margin-top: 3px; }
        .btn {
            background: linear-gradient(135deg, #00d4ff, #0088ff);
            border: none;
            color: #fff;
            padding: 8px 14px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 0.85em;
            transition: all 0.2s;
        }
        .btn:hover { transform: translateY(-1px); box-shadow: 0 4px 15px rgba(0,212,255,0.3); }
        .btn-danger { background: linear-gradient(135deg, #ff4757, #ff2f2f); }
        .btn-success { background: linear-gradient(135deg, #00ff88, #00cc66); }
        .btn-sm { padding: 5px 10px; font-size: 0.8em; }
        input[type="text"] {
            width: 100%;
            padding: 10px 12px;
            border: 1px solid rgba(255,255,255,0.15);
            border-radius: 6px;
            background: rgba(255,255,255,0.05);
            color: #fff;
            font-size: 0.9em;
        }
        input[type="text"]::placeholder { color: rgba(255,255,255,0.4); }
        input[type="text"]:focus { outline: none; border-color: #00d4ff; }
        .endpoint-box {
            background: rgba(0,255,136,0.1);
            border: 1px solid rgba(0,255,136,0.3);
            border-radius: 8px;
            padding: 12px;
            font-family: monospace;
            font-size: 0.95em;
            text-align: center;
            margin-top: 8px;
            word-break: break-all;
        }
        .history-item {
            background: rgba(255,255,255,0.02);
            border-radius: 5px;
            padding: 8px 10px;
            margin-bottom: 6px;
            font-size: 0.8em;
            display: flex;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 5px;
        }
        .history-item .time { color: #00d4ff; font-family: monospace; }
        .history-item .meta { opacity: 0.6; }
        .badge {
            display: inline-block;
            padding: 2px 6px;
            border-radius: 8px;
            font-size: 0.7em;
        }
        .badge-next { background: #00ff88; color: #000; }
        .badge-img { background: #ff88ff; color: #000; }
        .source-item {
            display: inline-block;
            background: rgba(255,255,255,0.05);
            padding: 4px 8px;
            border-radius: 4px;
            margin: 3px;
            font-size: 0.8em;
        }
        .copy-btn {
            background: transparent;
            border: 1px solid rgba(255,255,255,0.3);
            color: #fff;
            padding: 3px 8px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.75em;
            margin-left: 8px;
        }
        .copy-btn:hover { background: rgba(255,255,255,0.1); }
        .flex-row { display: flex; gap: 8px; margin-bottom: 10px; }
        .flex-row input { flex: 1; }
        @media (max-width: 600px) {
            .stats-grid { grid-template-columns: repeat(2, 1fr); }
            .webhook-item { flex-direction: column; align-items: flex-start; gap: 8px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🔄 Webhook 中繼站</h1>
        <p class="subtitle">多電腦 BOSS 通知分發系統 | 運行時間: <span id="uptime">-</span></p>
        
        <!-- 統計 -->
        <div class="card">
            <h2>📊 即時統計</h2>
            <div class="stats-grid">
                <div class="stat-box">
                    <div class="value" id="received">0</div>
                    <div class="label">接收總數</div>
                </div>
                <div class="stat-box">
                    <div class="value" id="sent">0</div>
                    <div class="label">發送成功</div>
                </div>
                <div class="stat-box">
                    <div class="value" id="failed">0</div>
                    <div class="label">發送失敗</div>
                </div>
                <div class="stat-box">
                    <div class="value" id="rate">0%</div>
                    <div class="label">成功率</div>
                </div>
                <div class="stat-box">
                    <div class="value" id="sources">0</div>
                    <div class="label">來源數</div>
                </div>
                <div class="stat-box">
                    <div class="value" id="webhooks">0</div>
                    <div class="label">Webhook</div>
                </div>
            </div>
        </div>
        
        <!-- 端點 -->
        <div class="card">
            <h2>📡 接收端點</h2>
            <p style="font-size: 0.85em; opacity: 0.8;">將此 URL 設定到所有喵雷達的 Webhook 欄位：</p>
            <div class="endpoint-box" id="endpoint">
                載入中...
                <button class="copy-btn" onclick="copyEndpoint()">📋 複製</button>
            </div>
        </div>
        
        <!-- Webhook 管理 -->
        <div class="card">
            <h2>🔗 Webhook 管理</h2>
            <div class="flex-row">
                <input type="text" id="newWebhook" placeholder="貼上 Discord Webhook URL...">
                <button class="btn btn-success" onclick="addWebhook()">➕ 添加</button>
            </div>
            <div id="webhookList"></div>
        </div>
        
        <!-- 來源統計 -->
        <div class="card">
            <h2>🖥️ 來源電腦 (前10)</h2>
            <div id="sourceList" style="margin-top: 8px;"></div>
        </div>
        
        <!-- 歷史 -->
        <div class="card">
            <h2>📜 發送歷史</h2>
            <div id="history"></div>
        </div>
        
        <!-- 測試 -->
        <div class="card">
            <h2>🧪 測試發送</h2>
            <div class="flex-row">
                <input type="text" id="testContent" placeholder="輸入測試訊息...">
                <button class="btn" onclick="sendTest()">📤 發送</button>
            </div>
            <div id="testResult" style="margin-top: 8px; font-size: 0.85em;"></div>
        </div>
    </div>
    
    <script>
        const baseUrl = window.location.origin;
        
        document.getElementById('endpoint').innerHTML = 
            `${baseUrl}/webhook <button class="copy-btn" onclick="copyEndpoint()">📋 複製</button>`;
        
        function copyEndpoint() {
            navigator.clipboard.writeText(baseUrl + '/webhook');
            alert('✅ 已複製到剪貼簿！');
        }
        
        async function loadData() {
            try {
                const res = await fetch('/api/stats');
                const data = await res.json();
                
                document.getElementById('uptime').textContent = data.uptime;
                document.getElementById('received').textContent = data.total_received;
                document.getElementById('sent').textContent = data.total_sent;
                document.getElementById('failed').textContent = data.failed_count;
                document.getElementById('rate').textContent = data.success_rate;
                document.getElementById('sources').textContent = data.source_count;
                document.getElementById('webhooks').textContent = data.webhooks_count;
                
                // Webhook 列表
                document.getElementById('webhookList').innerHTML = data.webhook_details.length 
                    ? data.webhook_details.map((w, i) => `
                        <div class="webhook-item ${w.is_next ? 'next' : ''}">
                            <div>
                                <strong>#${w.index}</strong>
                                ${w.is_next ? '<span class="badge badge-next">下一個</span>' : ''}
                                <div class="webhook-url">${w.url_preview}</div>
                                <div class="webhook-stats">✅ ${w.sent} | ❌ ${w.failed}</div>
                            </div>
                            <button class="btn btn-danger btn-sm" onclick="removeWebhook(${i})">🗑️</button>
                        </div>
                    `).join('')
                    : '<div style="opacity:0.5; font-size:0.85em;">尚未添加 Webhook</div>';
                
                // 來源列表
                document.getElementById('sourceList').innerHTML = data.sources.length
                    ? data.sources.map(s => `<span class="source-item">${s.ip}: ${s.count}次</span>`).join('')
                    : '<span style="opacity:0.5; font-size:0.85em;">尚無來源</span>';
                    
            } catch (e) { console.error(e); }
            
            // 歷史
            try {
                const res = await fetch('/api/history');
                const history = await res.json();
                
                document.getElementById('history').innerHTML = history.length
                    ? history.slice(0, 20).map(h => `
                        <div class="history-item">
                            <span>
                                <span class="time">[${h.time}]</span>
                                ${h.has_image ? '<span class="badge badge-img">📷</span>' : ''}
                                ${h.content}
                            </span>
                            <span class="meta">${h.status} #${h.webhook_index} | ${h.source}</span>
                        </div>
                    `).join('')
                    : '<div style="opacity:0.5; font-size:0.85em;">暫無記錄</div>';
            } catch (e) { console.error(e); }
        }
        
        async function addWebhook() {
            const url = document.getElementById('newWebhook').value.trim();
            if (!url) return alert('請輸入 Webhook URL');
            
            const res = await fetch('/api/webhook', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url})
            });
            const result = await res.json();
            
            if (result.success) {
                document.getElementById('newWebhook').value = '';
                loadData();
            } else {
                alert('❌ ' + result.message);
            }
        }
        
        async function removeWebhook(index) {
            if (!confirm('確定移除此 Webhook？')) return;
            await fetch(`/api/webhook/${index}`, {method: 'DELETE'});
            loadData();
        }
        
        async function sendTest() {
            const content = document.getElementById('testContent').value.trim();
            if (!content) return;
            
            const res = await fetch('/webhook', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({content})
            });
            const result = await res.json();
            
            document.getElementById('testResult').innerHTML = result.success
                ? `<span style="color:#00ff88">✅ 發送成功！→ Webhook #${result.webhook_index}</span>`
                : `<span style="color:#ff4757">❌ ${result.message}</span>`;
            
            document.getElementById('testContent').value = '';
            loadData();
        }
        
        document.getElementById('newWebhook').addEventListener('keypress', e => {
            if (e.key === 'Enter') addWebhook();
        });
        document.getElementById('testContent').addEventListener('keypress', e => {
            if (e.key === 'Enter') sendTest();
        });
        
        loadData();
        setInterval(loadData, 5000);
    </script>
</body>
</html>
'''

# ================================================================================
# API 路由
# ================================================================================

@app.route('/')
@requires_auth
def index():
    """Web 管理介面"""
    return render_template_string(HTML_TEMPLATE)


@app.route('/webhook', methods=['POST'])
def receive_webhook():
    """
    接收 Webhook（不需要密碼）
    
    支援格式：
    1. JSON: {"content": "訊息"}
    2. Form: content + file
    3. 支援 attachments 陣列（飛書轉發格式）
    """
    try:
        source_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ',' in source_ip:
            source_ip = source_ip.split(',')[0].strip()
        
        content = ""
        image_data = None
        
        if request.is_json:
            data = request.get_json()
            content = data.get('content', '')
            
            # 支援 attachments 陣列（圖片路徑）
            attachments = data.get('attachments', [])
            if attachments and len(attachments) > 0:
                first_attachment = attachments[0]
                image_url = first_attachment.get('url', '')
                
                # 如果是本地路徑，嘗試讀取
                if image_url and os.path.exists(image_url):
                    try:
                        with open(image_url, 'rb') as f:
                            image_data = f.read()
                    except:
                        pass
        else:
            content = request.form.get('content', '')
            if 'file' in request.files:
                file = request.files['file']
                if file:
                    image_data = file.read()
        
        if not content and not image_data:
            return jsonify({"success": False, "message": "無內容"}), 400
        
        success, message, webhook_index = relay.relay_message(content, image_data, source_ip)
        
        return jsonify({
            "success": success,
            "message": message,
            "webhook_index": webhook_index
        })
        
    except Exception as e:
        logger.error(f"❌ 處理請求失敗: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/stats')
@requires_auth
def get_stats():
    """獲取統計"""
    return jsonify(relay.get_stats())


@app.route('/api/history')
@requires_auth
def get_history():
    """獲取歷史"""
    return jsonify(list(relay.history))


@app.route('/api/webhook', methods=['POST'])
@requires_auth
def add_webhook():
    """添加 Webhook"""
    data = request.get_json()
    url = data.get('url', '').strip()
    success, message = relay.add_webhook(url)
    return jsonify({"success": success, "message": message})


@app.route('/api/webhook/<int:index>', methods=['DELETE'])
@requires_auth
def remove_webhook(index):
    """移除 Webhook"""
    success = relay.remove_webhook(index)
    return jsonify({"success": success})


@app.route('/health')
def health():
    """健康檢查"""
    return jsonify({"status": "ok", "webhooks": len(relay.webhooks)})


# ================================================================================
# 主程式
# ================================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("  🔄 Webhook 中繼站 v2.0 - 雲端部署版")
    print("=" * 60)
    print(f"  📡 本地訪問: http://localhost:{PORT}")
    print(f"  🔐 密碼保護: {'啟用' if ADMIN_PASSWORD else '停用'}")
    print("=" * 60)
    
    app.run(
        host='0.0.0.0',
        port=PORT,
        debug=False,
        threaded=True
    )