#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
    🔄 Webhook 中繼站 v3.0 - 多 BOSS 分組路由版
================================================================================

核心功能：
    - 支援多個 BOSS 群組，每個群組有獨立的接收端點和分發目標
    - 例如：
        - A BOSS → /webhook/a → 分發到 A 群組的多個 Discord
        - B BOSS → /webhook/b → 分發到 B 群組的多個 Discord
    - 每個群組獨立使用輪詢(Round Robin)分配
    - Web 管理介面可視化管理所有群組
    - 支援圖片轉發

部署平台：
    - Railway (推薦)
    - Render
    - Vercel
    - 任何支援 Python 的雲端平台

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
import re

# ================================================================================
# 環境變數配置
# ================================================================================

# 管理密碼（可選，留空則不需要密碼）
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')

# 預設群組配置 (JSON 格式)
# 例如: {"a": ["https://discord.com/xxx"], "b": ["https://discord.com/yyy"]}
DEFAULT_GROUPS_JSON = os.environ.get('WEBHOOK_GROUPS', '{}')

# 連接埠
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
# BOSS 群組類別
# ================================================================================

class BossGroup:
    """
    單一 BOSS 群組
    
    管理該群組的所有目標 Webhook，使用輪詢分配
    """
    
    def __init__(self, group_id: str, display_name: str = None):
        """
        初始化群組
        
        Args:
            group_id: 群組 ID（用於 URL 路徑，例如 'a', 'b', 'vellum'）
            display_name: 顯示名稱（例如 'A BOSS', 'B BOSS'）
        """
        self.group_id = group_id.lower()
        self.display_name = display_name or f"{group_id.upper()} BOSS"
        self.webhooks = []  # 目標 Discord Webhook 列表
        self.current_index = 0
        self.lock = threading.Lock()
        
        # 統計
        self.stats = {
            "received": 0,
            "sent": 0,
            "failed": 0,
            "webhook_stats": {}  # 每個 webhook 的統計
        }
        
        self.history = deque(maxlen=50)  # 最近 50 條記錄
    
    def add_webhook(self, url: str) -> tuple:
        """添加目標 Webhook"""
        with self.lock:
            if not url or not url.startswith("https://"):
                return False, "無效的 URL（必須以 https:// 開頭）"
            
            if url in self.webhooks:
                return False, "此 Webhook 已存在於此群組"
            
            self.webhooks.append(url)
            self.stats["webhook_stats"][url] = {"sent": 0, "failed": 0}
            logger.info(f"[{self.group_id}] ➕ 添加 Webhook: {url[:50]}...")
            return True, "添加成功"
    
    def remove_webhook(self, index: int) -> bool:
        """移除目標 Webhook"""
        with self.lock:
            if 0 <= index < len(self.webhooks):
                removed = self.webhooks.pop(index)
                if self.current_index >= len(self.webhooks) and len(self.webhooks) > 0:
                    self.current_index = 0
                logger.info(f"[{self.group_id}] ➖ 移除 Webhook: {removed[:50]}...")
                return True
            return False
    
    def get_next_webhook(self) -> tuple:
        """獲取下一個要發送的 Webhook（輪詢）"""
        with self.lock:
            if not self.webhooks:
                return None, -1
            
            webhook = self.webhooks[self.current_index]
            index = self.current_index
            self.current_index = (self.current_index + 1) % len(self.webhooks)
            
            return webhook, index
    
    def relay_message(self, content: str, image_data: bytes = None, source_ip: str = "unknown") -> tuple:
        """
        中繼訊息到下一個 Webhook
        
        Returns:
            tuple: (success, message, webhook_index)
        """
        self.stats["received"] += 1
        
        webhook_url, index = self.get_next_webhook()
        
        if not webhook_url:
            self.stats["failed"] += 1
            return False, f"群組 [{self.group_id}] 無可用的 Webhook", -1
        
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
                self.stats["sent"] += 1
                self.stats["webhook_stats"][webhook_url]["sent"] += 1
                
                self.history.appendleft({
                    "time": timestamp,
                    "content": content[:60] + "..." if len(content) > 60 else content,
                    "webhook_index": index + 1,
                    "source": source_ip[-15:] if len(source_ip) > 15 else source_ip,
                    "has_image": bool(image_data),
                    "status": "✅"
                })
                
                logger.info(f"[{self.group_id}] ✅ 訊息發送到 Webhook #{index + 1}")
                return True, "發送成功", index + 1
            else:
                raise Exception(f"HTTP {response.status_code}")
                
        except Exception as e:
            self.stats["failed"] += 1
            self.stats["webhook_stats"][webhook_url]["failed"] += 1
            
            self.history.appendleft({
                "time": timestamp,
                "content": content[:60] + "..." if len(content) > 60 else content,
                "webhook_index": index + 1,
                "source": source_ip[-15:] if len(source_ip) > 15 else source_ip,
                "has_image": bool(image_data),
                "status": f"❌ {str(e)[:20]}"
            })
            
            logger.error(f"[{self.group_id}] ❌ 發送失敗: {e}")
            return False, str(e), index + 1
    
    def get_stats(self) -> dict:
        """獲取群組統計"""
        return {
            "group_id": self.group_id,
            "display_name": self.display_name,
            "webhooks_count": len(self.webhooks),
            "current_index": self.current_index,
            "received": self.stats["received"],
            "sent": self.stats["sent"],
            "failed": self.stats["failed"],
            "success_rate": f"{(self.stats['sent'] / max(1, self.stats['received']) * 100):.1f}%",
            "webhook_details": [
                {
                    "index": i + 1,
                    "url_preview": f"...{url[-35:]}" if len(url) > 40 else url,
                    "sent": self.stats["webhook_stats"].get(url, {}).get("sent", 0),
                    "failed": self.stats["webhook_stats"].get(url, {}).get("failed", 0),
                    "is_next": i == self.current_index
                }
                for i, url in enumerate(self.webhooks)
            ],
            "history": list(self.history)[:20]
        }


# ================================================================================
# 中繼站管理器
# ================================================================================

class WebhookRelayManager:
    """
    Webhook 中繼站管理器
    
    管理所有 BOSS 群組
    """
    
    def __init__(self):
        """初始化管理器"""
        self.groups = {}  # group_id -> BossGroup
        self.lock = threading.Lock()
        self.start_time = datetime.now()
        
        # 從環境變數載入預設群組
        self._load_from_env()
        
        logger.info("=" * 60)
        logger.info("🔄 Webhook 中繼站 v3.0 (多 BOSS 分組版) 已啟動")
        logger.info(f"📡 已配置 {len(self.groups)} 個 BOSS 群組")
        logger.info(f"🔐 密碼保護: {'啟用' if ADMIN_PASSWORD else '停用'}")
        logger.info("=" * 60)
    
    def _load_from_env(self):
        """從環境變數載入群組配置"""
        try:
            if DEFAULT_GROUPS_JSON and DEFAULT_GROUPS_JSON != '{}':
                groups_config = json.loads(DEFAULT_GROUPS_JSON)
                for group_id, webhooks in groups_config.items():
                    group = self.create_group(group_id)
                    for webhook_url in webhooks:
                        group.add_webhook(webhook_url)
                logger.info(f"✅ 從環境變數載入 {len(self.groups)} 個群組")
        except json.JSONDecodeError as e:
            logger.error(f"❌ 解析 WEBHOOK_GROUPS 失敗: {e}")
    
    def create_group(self, group_id: str, display_name: str = None) -> BossGroup:
        """建立新群組"""
        with self.lock:
            # 清理 group_id（只允許英數字和底線）
            clean_id = re.sub(r'[^a-zA-Z0-9_]', '', group_id.lower())
            if not clean_id:
                clean_id = "default"
            
            if clean_id not in self.groups:
                self.groups[clean_id] = BossGroup(clean_id, display_name)
                logger.info(f"🆕 建立群組: {clean_id}")
            
            return self.groups[clean_id]
    
    def get_group(self, group_id: str) -> BossGroup:
        """獲取群組（若不存在則返回 None）"""
        return self.groups.get(group_id.lower())
    
    def get_or_create_group(self, group_id: str) -> BossGroup:
        """獲取或建立群組"""
        group = self.get_group(group_id)
        if not group:
            group = self.create_group(group_id)
        return group
    
    def delete_group(self, group_id: str) -> bool:
        """刪除群組"""
        with self.lock:
            if group_id.lower() in self.groups:
                del self.groups[group_id.lower()]
                logger.info(f"🗑️ 刪除群組: {group_id}")
                return True
            return False
    
    def rename_group(self, group_id: str, new_display_name: str) -> bool:
        """重命名群組"""
        group = self.get_group(group_id)
        if group:
            group.display_name = new_display_name
            return True
        return False
    
    def get_all_stats(self) -> dict:
        """獲取所有群組統計"""
        uptime = datetime.now() - self.start_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        
        total_received = sum(g.stats["received"] for g in self.groups.values())
        total_sent = sum(g.stats["sent"] for g in self.groups.values())
        total_failed = sum(g.stats["failed"] for g in self.groups.values())
        
        return {
            "uptime": f"{hours}h {minutes}m {seconds}s",
            "total_groups": len(self.groups),
            "total_received": total_received,
            "total_sent": total_sent,
            "total_failed": total_failed,
            "success_rate": f"{(total_sent / max(1, total_received) * 100):.1f}%",
            "groups": [g.get_stats() for g in self.groups.values()]
        }


# 建立全域管理器實例
manager = WebhookRelayManager()

# ================================================================================
# 密碼驗證裝飾器
# ================================================================================

def check_auth(username, password):
    """檢查密碼"""
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
    <title>🔄 Webhook 中繼站 v3.0</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Microsoft JhengHei', 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #0f0f23 0%, #1a1a3e 50%, #0f0f23 100%);
            min-height: 100vh;
            color: #fff;
            padding: 15px;
        }
        .container { max-width: 1000px; margin: 0 auto; }
        h1 {
            text-align: center;
            margin-bottom: 8px;
            font-size: 1.8em;
            background: linear-gradient(90deg, #00d4ff, #00ff88, #ff88ff);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .subtitle {
            text-align: center;
            color: #888;
            margin-bottom: 20px;
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
            grid-template-columns: repeat(auto-fit, minmax(90px, 1fr));
            gap: 10px;
        }
        .stat-box {
            background: rgba(0,212,255,0.08);
            border-radius: 8px;
            padding: 12px;
            text-align: center;
        }
        .stat-box .value {
            font-size: 1.5em;
            font-weight: bold;
            color: #00d4ff;
        }
        .stat-box .label { font-size: 0.7em; opacity: 0.7; margin-top: 3px; }
        
        /* 群組卡片 */
        .group-card {
            background: rgba(255,255,255,0.03);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 10px;
            margin-bottom: 12px;
            overflow: hidden;
        }
        .group-header {
            background: linear-gradient(90deg, rgba(0,212,255,0.15), rgba(0,255,136,0.1));
            padding: 12px 15px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            cursor: pointer;
        }
        .group-header:hover { background: linear-gradient(90deg, rgba(0,212,255,0.25), rgba(0,255,136,0.15)); }
        .group-title {
            font-weight: bold;
            font-size: 1.1em;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .group-title .id { 
            font-family: monospace; 
            background: rgba(0,0,0,0.3);
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.85em;
        }
        .group-stats-mini {
            display: flex;
            gap: 15px;
            font-size: 0.85em;
            opacity: 0.8;
        }
        .group-body {
            padding: 15px;
            display: none;
        }
        .group-body.open { display: block; }
        
        .endpoint-box {
            background: rgba(0,255,136,0.1);
            border: 1px solid rgba(0,255,136,0.3);
            border-radius: 6px;
            padding: 10px;
            font-family: monospace;
            font-size: 0.85em;
            margin: 10px 0;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
        }
        
        .webhook-item {
            background: rgba(255,255,255,0.03);
            border-radius: 6px;
            padding: 8px 10px;
            margin-bottom: 6px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.85em;
        }
        .webhook-item.next { 
            border-left: 3px solid #00ff88;
            background: rgba(0,255,136,0.08);
        }
        .webhook-url { font-family: monospace; opacity: 0.7; word-break: break-all; }
        .webhook-stats { font-size: 0.75em; opacity: 0.5; }
        
        .btn {
            background: linear-gradient(135deg, #00d4ff, #0088ff);
            border: none;
            color: #fff;
            padding: 7px 12px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 0.8em;
            transition: all 0.2s;
        }
        .btn:hover { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(0,212,255,0.3); }
        .btn-danger { background: linear-gradient(135deg, #ff4757, #ff2f2f); }
        .btn-success { background: linear-gradient(135deg, #00ff88, #00cc66); }
        .btn-purple { background: linear-gradient(135deg, #a855f7, #7c3aed); }
        .btn-sm { padding: 4px 8px; font-size: 0.75em; }
        
        input[type="text"] {
            padding: 8px 10px;
            border: 1px solid rgba(255,255,255,0.15);
            border-radius: 5px;
            background: rgba(255,255,255,0.05);
            color: #fff;
            font-size: 0.85em;
        }
        input[type="text"]::placeholder { color: rgba(255,255,255,0.4); }
        input[type="text"]:focus { outline: none; border-color: #00d4ff; }
        
        .flex-row { display: flex; gap: 8px; margin-bottom: 8px; }
        .flex-row input { flex: 1; }
        
        .history-item {
            background: rgba(255,255,255,0.02);
            border-radius: 4px;
            padding: 6px 8px;
            margin-bottom: 4px;
            font-size: 0.75em;
            display: flex;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 5px;
        }
        .history-item .time { color: #00d4ff; font-family: monospace; }
        
        .badge {
            display: inline-block;
            padding: 2px 6px;
            border-radius: 6px;
            font-size: 0.65em;
            font-weight: bold;
        }
        .badge-next { background: #00ff88; color: #000; }
        .badge-img { background: #ff88ff; color: #000; }
        
        .copy-btn {
            background: transparent;
            border: 1px solid rgba(255,255,255,0.3);
            color: #fff;
            padding: 3px 8px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.75em;
        }
        .copy-btn:hover { background: rgba(255,255,255,0.1); }
        
        .section-title {
            font-size: 0.9em;
            color: #00d4ff;
            margin: 12px 0 8px 0;
            padding-bottom: 5px;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }
        
        .no-data { opacity: 0.4; font-size: 0.8em; padding: 10px; text-align: center; }
        
        @media (max-width: 600px) {
            .stats-grid { grid-template-columns: repeat(3, 1fr); }
            .group-header { flex-direction: column; align-items: flex-start; gap: 8px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🔄 Webhook 中繼站 v3.0</h1>
        <p class="subtitle">多 BOSS 分組路由系統 | 運行: <span id="uptime">-</span></p>
        
        <!-- 總覽統計 -->
        <div class="card">
            <h2>📊 總覽統計</h2>
            <div class="stats-grid">
                <div class="stat-box">
                    <div class="value" id="totalGroups">0</div>
                    <div class="label">BOSS 群組</div>
                </div>
                <div class="stat-box">
                    <div class="value" id="totalReceived">0</div>
                    <div class="label">接收總數</div>
                </div>
                <div class="stat-box">
                    <div class="value" id="totalSent">0</div>
                    <div class="label">發送成功</div>
                </div>
                <div class="stat-box">
                    <div class="value" id="totalFailed">0</div>
                    <div class="label">發送失敗</div>
                </div>
                <div class="stat-box">
                    <div class="value" id="successRate">0%</div>
                    <div class="label">成功率</div>
                </div>
            </div>
        </div>
        
        <!-- 建立新群組 -->
        <div class="card">
            <h2>➕ 建立新 BOSS 群組</h2>
            <div class="flex-row">
                <input type="text" id="newGroupId" placeholder="群組 ID (英文/數字，如: a, b, vellum)" style="width: 150px;">
                <input type="text" id="newGroupName" placeholder="顯示名稱 (如: A BOSS, 暴君)">
                <button class="btn btn-success" onclick="createGroup()">🆕 建立</button>
            </div>
            <p style="font-size: 0.75em; opacity: 0.6; margin-top: 5px;">
                建立後，喵雷達發送至 <code>/webhook/{群組ID}</code> 的通知將分發到該群組的 Discord
            </p>
        </div>
        
        <!-- 群組列表 -->
        <div class="card">
            <h2>🎯 BOSS 群組管理</h2>
            <div id="groupList"></div>
        </div>
        
        <!-- 使用說明 -->
        <div class="card">
            <h2>📖 使用說明</h2>
            <div style="font-size: 0.85em; line-height: 1.6;">
                <p><strong>1. 建立群組</strong> - 為每種 BOSS 建立獨立群組（如: a, b, vellum）</p>
                <p><strong>2. 設定目標</strong> - 在群組中添加多個 Discord Webhook（會輪流發送）</p>
                <p><strong>3. 喵雷達設定</strong> - 將對應的端點 URL 填入喵雷達的 Webhook 欄位</p>
                <p style="margin-top: 10px; padding: 8px; background: rgba(255,255,255,0.05); border-radius: 5px;">
                    💡 <strong>範例：</strong><br>
                    A BOSS 喵雷達 → <code>{baseUrl}/webhook/a</code><br>
                    B BOSS 喵雷達 → <code>{baseUrl}/webhook/b</code>
                </p>
            </div>
        </div>
    </div>
    
    <script>
        const baseUrl = window.location.origin;
        let openGroups = new Set();
        
        async function loadData() {
            try {
                const res = await fetch('/api/stats');
                const data = await res.json();
                
                document.getElementById('uptime').textContent = data.uptime;
                document.getElementById('totalGroups').textContent = data.total_groups;
                document.getElementById('totalReceived').textContent = data.total_received;
                document.getElementById('totalSent').textContent = data.total_sent;
                document.getElementById('totalFailed').textContent = data.total_failed;
                document.getElementById('successRate').textContent = data.success_rate;
                
                renderGroups(data.groups);
            } catch (e) { console.error(e); }
        }
        
        function renderGroups(groups) {
            const container = document.getElementById('groupList');
            
            if (!groups || groups.length === 0) {
                container.innerHTML = '<div class="no-data">尚未建立任何群組，請在上方建立</div>';
                return;
            }
            
            container.innerHTML = groups.map(g => `
                <div class="group-card">
                    <div class="group-header" onclick="toggleGroup('${g.group_id}')">
                        <div class="group-title">
                            <span>${g.display_name}</span>
                            <span class="id">${g.group_id}</span>
                        </div>
                        <div class="group-stats-mini">
                            <span>📥 ${g.received}</span>
                            <span>✅ ${g.sent}</span>
                            <span>🔗 ${g.webhooks_count}</span>
                        </div>
                    </div>
                    <div class="group-body ${openGroups.has(g.group_id) ? 'open' : ''}" id="group-${g.group_id}">
                        <!-- 端點 -->
                        <div class="section-title">📡 接收端點</div>
                        <div class="endpoint-box">
                            <span>${baseUrl}/webhook/${g.group_id}</span>
                            <button class="copy-btn" onclick="copyText('${baseUrl}/webhook/${g.group_id}')">📋 複製</button>
                        </div>
                        
                        <!-- 添加 Webhook -->
                        <div class="section-title">🔗 目標 Discord Webhook</div>
                        <div class="flex-row">
                            <input type="text" id="webhook-input-${g.group_id}" placeholder="貼上 Discord Webhook URL...">
                            <button class="btn btn-success btn-sm" onclick="addWebhook('${g.group_id}')">➕</button>
                        </div>
                        
                        <!-- Webhook 列表 -->
                        ${g.webhook_details.length ? g.webhook_details.map((w, i) => `
                            <div class="webhook-item ${w.is_next ? 'next' : ''}">
                                <div>
                                    <strong>#${w.index}</strong>
                                    ${w.is_next ? '<span class="badge badge-next">下一個</span>' : ''}
                                    <div class="webhook-url">${w.url_preview}</div>
                                    <div class="webhook-stats">✅ ${w.sent} | ❌ ${w.failed}</div>
                                </div>
                                <button class="btn btn-danger btn-sm" onclick="removeWebhook('${g.group_id}', ${i})">🗑️</button>
                            </div>
                        `).join('') : '<div class="no-data">尚未添加目標 Webhook</div>'}
                        
                        <!-- 歷史 -->
                        <div class="section-title">📜 最近發送</div>
                        ${g.history && g.history.length ? g.history.slice(0, 10).map(h => `
                            <div class="history-item">
                                <span>
                                    <span class="time">${h.time}</span>
                                    ${h.has_image ? '<span class="badge badge-img">📷</span>' : ''}
                                    ${h.content}
                                </span>
                                <span>${h.status} #${h.webhook_index}</span>
                            </div>
                        `).join('') : '<div class="no-data">暫無記錄</div>'}
                        
                        <!-- 操作 -->
                        <div style="margin-top: 15px; display: flex; gap: 8px; justify-content: flex-end;">
                            <button class="btn btn-purple btn-sm" onclick="testGroup('${g.group_id}')">🧪 測試</button>
                            <button class="btn btn-danger btn-sm" onclick="deleteGroup('${g.group_id}')">🗑️ 刪除群組</button>
                        </div>
                    </div>
                </div>
            `).join('');
        }
        
        function toggleGroup(groupId) {
            if (openGroups.has(groupId)) {
                openGroups.delete(groupId);
            } else {
                openGroups.add(groupId);
            }
            const el = document.getElementById(`group-${groupId}`);
            if (el) el.classList.toggle('open');
        }
        
        function copyText(text) {
            navigator.clipboard.writeText(text);
            alert('✅ 已複製到剪貼簿！');
        }
        
        async function createGroup() {
            const groupId = document.getElementById('newGroupId').value.trim();
            const displayName = document.getElementById('newGroupName').value.trim();
            
            if (!groupId) return alert('請輸入群組 ID');
            
            const res = await fetch('/api/group', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ group_id: groupId, display_name: displayName || null })
            });
            const result = await res.json();
            
            if (result.success) {
                document.getElementById('newGroupId').value = '';
                document.getElementById('newGroupName').value = '';
                openGroups.add(groupId.toLowerCase());
                loadData();
            } else {
                alert('❌ ' + result.message);
            }
        }
        
        async function deleteGroup(groupId) {
            if (!confirm(`確定刪除群組 [${groupId}]？\\n此操作無法復原！`)) return;
            
            await fetch(`/api/group/${groupId}`, { method: 'DELETE' });
            openGroups.delete(groupId);
            loadData();
        }
        
        async function addWebhook(groupId) {
            const input = document.getElementById(`webhook-input-${groupId}`);
            const url = input.value.trim();
            if (!url) return;
            
            const res = await fetch(`/api/group/${groupId}/webhook`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ url })
            });
            const result = await res.json();
            
            if (result.success) {
                input.value = '';
                loadData();
            } else {
                alert('❌ ' + result.message);
            }
        }
        
        async function removeWebhook(groupId, index) {
            if (!confirm('確定移除此 Webhook？')) return;
            await fetch(`/api/group/${groupId}/webhook/${index}`, { method: 'DELETE' });
            loadData();
        }
        
        async function testGroup(groupId) {
            const content = prompt('輸入測試訊息:', `[測試] ${groupId.toUpperCase()} BOSS 通知測試`);
            if (!content) return;
            
            const res = await fetch(`/webhook/${groupId}`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ content })
            });
            const result = await res.json();
            
            alert(result.success 
                ? `✅ 發送成功！→ Webhook #${result.webhook_index}`
                : `❌ ${result.message}`);
            loadData();
        }
        
        // 綁定 Enter 鍵
        document.getElementById('newGroupId').addEventListener('keypress', e => {
            if (e.key === 'Enter') createGroup();
        });
        document.getElementById('newGroupName').addEventListener('keypress', e => {
            if (e.key === 'Enter') createGroup();
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


@app.route('/webhook/<group_id>', methods=['POST'])
def receive_webhook(group_id):
    """
    接收指定群組的 Webhook（不需要密碼）
    
    URL: /webhook/{group_id}
    例如: /webhook/a, /webhook/b, /webhook/vellum
    """
    try:
        source_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ',' in source_ip:
            source_ip = source_ip.split(',')[0].strip()
        
        # 獲取或建立群組
        group = manager.get_or_create_group(group_id)
        
        content = ""
        image_data = None
        
        if request.is_json:
            data = request.get_json()
            content = data.get('content', '')
            
            # 支援 attachments 陣列
            attachments = data.get('attachments', [])
            if attachments and len(attachments) > 0:
                first_attachment = attachments[0]
                image_url = first_attachment.get('url', '')
                
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
        
        success, message, webhook_index = group.relay_message(content, image_data, source_ip)
        
        return jsonify({
            "success": success,
            "message": message,
            "group_id": group_id,
            "webhook_index": webhook_index
        })
        
    except Exception as e:
        logger.error(f"❌ [{group_id}] 處理請求失敗: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


# 向下相容：舊版端點（無群組指定時使用 default 群組）
@app.route('/webhook', methods=['POST'])
def receive_webhook_default():
    """向下相容的預設端點"""
    return receive_webhook('default')


@app.route('/api/stats')
@requires_auth
def get_stats():
    """獲取所有統計"""
    return jsonify(manager.get_all_stats())


@app.route('/api/group', methods=['POST'])
@requires_auth
def create_group():
    """建立新群組"""
    data = request.get_json()
    group_id = data.get('group_id', '').strip()
    display_name = data.get('display_name')
    
    if not group_id:
        return jsonify({"success": False, "message": "請提供群組 ID"})
    
    # 檢查是否已存在
    if manager.get_group(group_id):
        return jsonify({"success": False, "message": "此群組 ID 已存在"})
    
    manager.create_group(group_id, display_name)
    return jsonify({"success": True, "message": "建立成功"})


@app.route('/api/group/<group_id>', methods=['DELETE'])
@requires_auth
def delete_group(group_id):
    """刪除群組"""
    success = manager.delete_group(group_id)
    return jsonify({"success": success})


@app.route('/api/group/<group_id>/webhook', methods=['POST'])
@requires_auth
def add_webhook_to_group(group_id):
    """添加 Webhook 到群組"""
    group = manager.get_group(group_id)
    if not group:
        return jsonify({"success": False, "message": "群組不存在"})
    
    data = request.get_json()
    url = data.get('url', '').strip()
    success, message = group.add_webhook(url)
    return jsonify({"success": success, "message": message})


@app.route('/api/group/<group_id>/webhook/<int:index>', methods=['DELETE'])
@requires_auth
def remove_webhook_from_group(group_id, index):
    """從群組移除 Webhook"""
    group = manager.get_group(group_id)
    if not group:
        return jsonify({"success": False, "message": "群組不存在"})
    
    success = group.remove_webhook(index)
    return jsonify({"success": success})


@app.route('/health')
def health():
    """健康檢查"""
    return jsonify({
        "status": "ok",
        "groups": len(manager.groups),
        "version": "3.0"
    })


# ================================================================================
# 主程式
# ================================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("  🔄 Webhook 中繼站 v3.0 - 多 BOSS 分組路由版")
    print("=" * 60)
    print(f"  📡 本地訪問: http://localhost:{PORT}")
    print(f"  🔐 密碼保護: {'啟用' if ADMIN_PASSWORD else '停用'}")
    print("=" * 60)
    print()
    print("  使用方式:")
    print("    1. 建立群組 (如: a, b, vellum)")
    print("    2. 在群組中添加多個 Discord Webhook")
    print("    3. 喵雷達 Webhook 設定為 /webhook/{群組ID}")
    print()
    print("  範例:")
    print(f"    A BOSS → http://localhost:{PORT}/webhook/a")
    print(f"    B BOSS → http://localhost:{PORT}/webhook/b")
    print("=" * 60)
    
    app.run(
        host='0.0.0.0',
        port=PORT,
        debug=False,
        threaded=True
    )
