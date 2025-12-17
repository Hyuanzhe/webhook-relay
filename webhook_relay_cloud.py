#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
    🔄 Webhook 中繼站 v3.1 - 整合飛書圖片轉發版
================================================================================

核心功能：
    - 支援多個 BOSS 群組，每個群組有獨立的接收端點和分發目標
    - 🆕 支援同時轉發到 Discord 和飛書
    - 🆕 自動上傳圖片到飛書並顯示
    - 🆕 飛書使用富文本消息展示圖片
    - 每個群組獨立使用輪詢(Round Robin)分配
    - Web 管理介面可視化管理所有群組

部署平台：
    - Railway (推薦)
    - Render
    - 任何支援 Python 的雲端平台

作者: @yyv3vnn
更新: 整合飛書圖片轉發功能
================================================================================
"""

import json
import os
import threading
import time
import requests
import base64
import hashlib
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
DEFAULT_GROUPS_JSON = os.environ.get('WEBHOOK_GROUPS', '{}')

# 連接埠
PORT = int(os.environ.get('PORT', 5000))

# 🆕 飛書應用憑證（固定配置，與轉發程序共用）
FEISHU_APP_ID = os.environ.get('FEISHU_APP_ID', 'cli_a98f2ae2ea3b900e')
FEISHU_APP_SECRET = os.environ.get('FEISHU_APP_SECRET', 'Ez8BLvrXG3kvWg6avZqD3gduuc5Pg0uf')

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
# 🆕 飛書圖片上傳器
# ================================================================================

class FeishuImageUploader:
    """
    飛書圖片上傳器
    
    功能：
    - 獲取 tenant_access_token
    - 上傳圖片獲取 image_key
    - 緩存已上傳的圖片
    """
    
    def __init__(self):
        self.upload_cache = {}  # hash -> image_key
        self.token_cache = {
            'token': None,
            'expire_time': 0
        }
    
    def get_tenant_access_token(self) -> str:
        """獲取 tenant_access_token（帶緩存）"""
        try:
            # 檢查緩存
            current_time = time.time()
            if (self.token_cache['token'] and 
                current_time < self.token_cache['expire_time'] - 60):
                return self.token_cache['token']
            
            url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
            
            payload = {
                "app_id": FEISHU_APP_ID,
                "app_secret": FEISHU_APP_SECRET
            }
            
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('code') == 0:
                    token = result.get('tenant_access_token')
                    expire = result.get('expire', 7200)
                    
                    # 緩存 token
                    self.token_cache['token'] = token
                    self.token_cache['expire_time'] = current_time + expire
                    
                    logger.info("✅ 獲取飛書 access_token 成功")
                    return token
                else:
                    logger.error(f"❌ 獲取 token 失敗: {result}")
                    return None
            else:
                logger.error(f"❌ API 請求失敗: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"❌ 獲取 access_token 異常: {e}")
            return None
    
    def upload_image(self, image_data: bytes) -> str:
        """
        上傳圖片到飛書
        
        Args:
            image_data: 圖片二進制數據
            
        Returns:
            image_key 或 None
        """
        try:
            # 檢查緩存
            img_hash = hashlib.md5(image_data).hexdigest()
            if img_hash in self.upload_cache:
                logger.info("📦 使用緩存的圖片 key")
                return self.upload_cache[img_hash]
            
            # 獲取 token
            token = self.get_tenant_access_token()
            if not token:
                logger.error("❌ 無法獲取 access_token")
                return None
            
            # 上傳圖片
            url = "https://open.feishu.cn/open-apis/im/v1/images"
            
            headers = {
                "Authorization": f"Bearer {token}"
            }
            
            files = {
                'image': ('boss_screenshot.png', image_data, 'image/png')
            }
            
            data = {
                'image_type': 'message'
            }
            
            response = requests.post(
                url,
                headers=headers,
                files=files,
                data=data,
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('code') == 0:
                    image_key = result.get('data', {}).get('image_key')
                    if image_key:
                        # 緩存
                        self.upload_cache[img_hash] = image_key
                        logger.info(f"✅ 圖片上傳成功: {image_key[:20]}...")
                        return image_key
                else:
                    logger.error(f"❌ 上傳圖片失敗: {result}")
                    return None
            else:
                logger.error(f"❌ 上傳請求失敗: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"❌ 上傳圖片異常: {e}")
            return None


# 全局飛書上傳器
feishu_uploader = FeishuImageUploader()

# ================================================================================
# 🆕 飛書消息發送器
# ================================================================================

class FeishuSender:
    """飛書消息發送器"""
    
    @staticmethod
    def send_message_with_image(webhook_url: str, content: str, 
                                 image_key: str = None) -> bool:
        """
        發送帶圖片的富文本消息到飛書
        
        Args:
            webhook_url: 飛書 Webhook URL
            content: 文本內容
            image_key: 飛書圖片 key（可選）
            
        Returns:
            是否成功
        """
        try:
            if not webhook_url:
                logger.warning("⚠️ 未配置飛書 Webhook URL")
                return False
            
            # 構建富文本內容
            content_blocks = []
            
            # 添加文本
            if content:
                lines = content.split('\n')
                for line in lines:
                    if line.strip():
                        content_blocks.append([
                            {
                                "tag": "text",
                                "text": line + "\n"
                            }
                        ])
            
            # 添加圖片
            if image_key:
                content_blocks.append([
                    {
                        "tag": "img",
                        "image_key": image_key,
                        "width": 800,
                        "height": 600
                    }
                ])
            
            # 添加時間戳
            content_blocks.append([
                {
                    "tag": "text",
                    "text": f"\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                }
            ])
            
            # 構建消息
            payload = {
                "msg_type": "post",
                "content": {
                    "post": {
                        "zh_cn": {
                            "title": "🎯 BOSS 通知",
                            "content": content_blocks
                        }
                    }
                }
            }
            
            # 發送
            response = requests.post(
                webhook_url,
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('code') == 0 or result.get('StatusCode') == 0:
                    logger.info("✅ 飛書消息發送成功")
                    return True
                else:
                    logger.error(f"❌ 飛書返回錯誤: {result}")
                    return False
            else:
                logger.error(f"❌ 飛書 API 錯誤: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ 發送飛書消息失敗: {e}")
            return False
    
    @staticmethod
    def send_with_retry(webhook_url: str, content: str, 
                        image_key: str = None, retries: int = 3) -> bool:
        """帶重試的發送"""
        for attempt in range(1, retries + 1):
            if FeishuSender.send_message_with_image(webhook_url, content, image_key):
                return True
            
            if attempt < retries:
                logger.warning(f"⏳ 飛書發送失敗，2秒後重試 ({attempt}/{retries})...")
                time.sleep(2)
        
        return False


# ================================================================================
# BOSS 群組類別
# ================================================================================

class BossGroup:
    """
    單一 BOSS 群組
    
    管理該群組的所有目標 Webhook，使用輪詢分配
    🆕 支援同時轉發到飛書
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
        
        # 🆕 飛書 Webhook URL
        self.feishu_webhook_url = ""
        self.feishu_enabled = False
        
        # 統計
        self.stats = {
            "received": 0,
            "sent": 0,
            "failed": 0,
            "feishu_sent": 0,
            "feishu_failed": 0,
            "webhook_stats": {}
        }
        
        self.history = deque(maxlen=50)
    
    def set_feishu_webhook(self, url: str) -> tuple:
        """設置飛書 Webhook URL"""
        with self.lock:
            if url and url.startswith("https://"):
                self.feishu_webhook_url = url
                self.feishu_enabled = True
                logger.info(f"[{self.group_id}] ✅ 飛書 Webhook 已設置")
                return True, "設置成功"
            elif not url:
                self.feishu_webhook_url = ""
                self.feishu_enabled = False
                logger.info(f"[{self.group_id}] ⚠️ 飛書 Webhook 已清除")
                return True, "已清除"
            else:
                return False, "無效的 URL（必須以 https:// 開頭）"
    
    def add_webhook(self, url: str) -> tuple:
        """添加目標 Discord Webhook"""
        with self.lock:
            if not url or not url.startswith("https://"):
                return False, "無效的 URL（必須以 https:// 開頭）"
            
            if url in self.webhooks:
                return False, "此 Webhook 已存在於此群組"
            
            self.webhooks.append(url)
            self.stats["webhook_stats"][url] = {"sent": 0, "failed": 0}
            logger.info(f"[{self.group_id}] ➕ 添加 Discord Webhook: {url[:50]}...")
            return True, "添加成功"
    
    def remove_webhook(self, index: int) -> bool:
        """移除目標 Discord Webhook"""
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
    
    def relay_message(self, content: str, image_data: bytes = None, 
                      source_ip: str = "unknown") -> tuple:
        """
        中繼訊息到 Discord 和飛書
        
        Returns:
            tuple: (success, message, webhook_index)
        """
        self.stats["received"] += 1
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        discord_success = False
        feishu_success = False
        webhook_index = -1
        
        # ========== 1. 發送到 Discord ==========
        webhook_url, index = self.get_next_webhook()
        webhook_index = index + 1 if index >= 0 else -1
        
        if webhook_url:
            try:
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
                    discord_success = True
                    logger.info(f"[{self.group_id}] ✅ Discord 發送成功 → Webhook #{webhook_index}")
                else:
                    raise Exception(f"HTTP {response.status_code}")
                    
            except Exception as e:
                self.stats["failed"] += 1
                self.stats["webhook_stats"][webhook_url]["failed"] += 1
                logger.error(f"[{self.group_id}] ❌ Discord 發送失敗: {e}")
        else:
            logger.warning(f"[{self.group_id}] ⚠️ 無可用的 Discord Webhook")
        
        # ========== 2. 🆕 發送到飛書 ==========
        if self.feishu_enabled and self.feishu_webhook_url:
            try:
                logger.info(f"[{self.group_id}] 📤 開始轉發到飛書...")
                
                image_key = None
                if image_data:
                    logger.info(f"[{self.group_id}] 📷 上傳圖片到飛書...")
                    image_key = feishu_uploader.upload_image(image_data)
                    if image_key:
                        logger.info(f"[{self.group_id}] ✅ 圖片上傳成功")
                    else:
                        logger.warning(f"[{self.group_id}] ⚠️ 圖片上傳失敗，將只發送文字")
                
                # 發送消息
                if FeishuSender.send_with_retry(
                    self.feishu_webhook_url, 
                    content, 
                    image_key
                ):
                    self.stats["feishu_sent"] += 1
                    feishu_success = True
                    logger.info(f"[{self.group_id}] ✅ 飛書發送成功")
                else:
                    self.stats["feishu_failed"] += 1
                    logger.error(f"[{self.group_id}] ❌ 飛書發送失敗")
                    
            except Exception as e:
                self.stats["feishu_failed"] += 1
                logger.error(f"[{self.group_id}] ❌ 飛書轉發異常: {e}")
        
        # ========== 3. 記錄歷史 ==========
        status_parts = []
        if discord_success:
            status_parts.append(f"✅D#{webhook_index}")
        elif webhook_url:
            status_parts.append("❌D")
        
        if self.feishu_enabled:
            if feishu_success:
                status_parts.append("✅飛書")
            else:
                status_parts.append("❌飛書")
        
        self.history.appendleft({
            "time": timestamp,
            "content": content[:60] + "..." if len(content) > 60 else content,
            "webhook_index": webhook_index,
            "source": source_ip[-15:] if len(source_ip) > 15 else source_ip,
            "has_image": bool(image_data),
            "status": " | ".join(status_parts) if status_parts else "⚠️無目標"
        })
        
        # 返回結果
        overall_success = discord_success or feishu_success
        message = f"Discord: {'✅' if discord_success else '❌'}"
        if self.feishu_enabled:
            message += f" | 飛書: {'✅' if feishu_success else '❌'}"
        
        return overall_success, message, webhook_index
    
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
            "feishu_enabled": self.feishu_enabled,
            "feishu_webhook_url": self.feishu_webhook_url[:50] + "..." if self.feishu_webhook_url and len(self.feishu_webhook_url) > 50 else self.feishu_webhook_url,
            "feishu_sent": self.stats["feishu_sent"],
            "feishu_failed": self.stats["feishu_failed"],
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
# 🆕 預設群組配置（部署後自動建立）
# ================================================================================

DEFAULT_BOSS_GROUPS = {
    "a": "喵z",
    "b": "蘑菇",
    "c": "仙人",
    "d": "黑輪",
    "xb": "小巴"
}

# ================================================================================
# 中繼站管理器
# ================================================================================

class WebhookRelayManager:
    """Webhook 中繼站管理器"""
    
    def __init__(self):
        self.groups = {}
        self.lock = threading.Lock()
        self.start_time = datetime.now()
        
        # 🆕 先建立預設群組
        self._create_default_groups()
        
        self._load_from_env()
        
        logger.info("=" * 60)
        logger.info("🔄 Webhook 中繼站 v3.1 (整合飛書版) 已啟動")
        logger.info(f"📡 已配置 {len(self.groups)} 個 BOSS 群組")
        logger.info(f"🔐 密碼保護: {'啟用' if ADMIN_PASSWORD else '停用'}")
        logger.info(f"📱 飛書 App ID: {FEISHU_APP_ID[:10]}...")
        logger.info("=" * 60)
    
    def _create_default_groups(self):
        """🆕 建立預設的 BOSS 群組"""
        logger.info("🔧 建立預設 BOSS 群組...")
        for group_id, display_name in DEFAULT_BOSS_GROUPS.items():
            self.create_group(group_id, display_name)
            logger.info(f"   ✅ {group_id} → {display_name}")
        logger.info(f"✅ 已建立 {len(DEFAULT_BOSS_GROUPS)} 個預設群組")
    
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
            clean_id = re.sub(r'[^a-zA-Z0-9_]', '', group_id.lower())
            if not clean_id:
                clean_id = "default"
            
            if clean_id not in self.groups:
                self.groups[clean_id] = BossGroup(clean_id, display_name)
                logger.info(f"🆕 建立群組: {clean_id}")
            
            return self.groups[clean_id]
    
    def get_group(self, group_id: str) -> BossGroup:
        return self.groups.get(group_id.lower())
    
    def get_or_create_group(self, group_id: str) -> BossGroup:
        group = self.get_group(group_id)
        if not group:
            group = self.create_group(group_id)
        return group
    
    def delete_group(self, group_id: str) -> bool:
        with self.lock:
            if group_id.lower() in self.groups:
                del self.groups[group_id.lower()]
                logger.info(f"🗑️ 刪除群組: {group_id}")
                return True
            return False
    
    def get_all_stats(self) -> dict:
        uptime = datetime.now() - self.start_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        
        total_received = sum(g.stats["received"] for g in self.groups.values())
        total_sent = sum(g.stats["sent"] for g in self.groups.values())
        total_failed = sum(g.stats["failed"] for g in self.groups.values())
        total_feishu_sent = sum(g.stats["feishu_sent"] for g in self.groups.values())
        total_feishu_failed = sum(g.stats["feishu_failed"] for g in self.groups.values())
        
        return {
            "uptime": f"{hours}h {minutes}m {seconds}s",
            "total_groups": len(self.groups),
            "total_received": total_received,
            "total_sent": total_sent,
            "total_failed": total_failed,
            "total_feishu_sent": total_feishu_sent,
            "total_feishu_failed": total_feishu_failed,
            "success_rate": f"{(total_sent / max(1, total_received) * 100):.1f}%",
            "groups": [g.get_stats() for g in self.groups.values()]
        }


# 建立全域管理器實例
manager = WebhookRelayManager()

# ================================================================================
# 密碼驗證裝飾器
# ================================================================================

def check_auth(username, password):
    return password == ADMIN_PASSWORD

def authenticate():
    return Response(
        '需要密碼才能訪問管理介面\n',
        401,
        {'WWW-Authenticate': 'Basic realm="Webhook Relay Admin"'}
    )

def requires_auth(f):
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
# Web 介面模板（整合飛書設置）
# ================================================================================

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🔄 Webhook 中繼站 v3.1 (整合飛書版)</title>
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
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(80px, 1fr));
            gap: 10px;
        }
        .stat-box {
            background: rgba(0,212,255,0.08);
            border-radius: 8px;
            padding: 12px;
            text-align: center;
        }
        .stat-box .value {
            font-size: 1.4em;
            font-weight: bold;
            color: #00d4ff;
        }
        .stat-box .label { font-size: 0.65em; opacity: 0.7; margin-top: 3px; }
        .stat-box.feishu .value { color: #00ff88; }
        
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
            gap: 12px;
            font-size: 0.8em;
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
        
        .feishu-box {
            background: rgba(0,136,255,0.1);
            border: 1px solid rgba(0,136,255,0.3);
            border-radius: 6px;
            padding: 12px;
            margin: 10px 0;
        }
        .feishu-box .title {
            color: #00d4ff;
            font-weight: bold;
            margin-bottom: 8px;
            font-size: 0.9em;
        }
        .feishu-status {
            font-size: 0.8em;
            padding: 3px 8px;
            border-radius: 4px;
            display: inline-block;
        }
        .feishu-status.enabled { background: rgba(0,255,136,0.2); color: #00ff88; }
        .feishu-status.disabled { background: rgba(255,100,100,0.2); color: #ff8888; }
        
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
        .btn-feishu { background: linear-gradient(135deg, #3b82f6, #1d4ed8); }
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
        .badge-feishu { background: #3b82f6; color: #fff; }
        
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
        <h1>🔄 Webhook 中繼站 v3.1</h1>
        <p class="subtitle">整合飛書圖片轉發 | 運行: <span id="uptime">-</span></p>
        
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
                    <div class="label">Discord✅</div>
                </div>
                <div class="stat-box">
                    <div class="value" id="totalFailed">0</div>
                    <div class="label">Discord❌</div>
                </div>
                <div class="stat-box feishu">
                    <div class="value" id="totalFeishuSent">0</div>
                    <div class="label">飛書✅</div>
                </div>
                <div class="stat-box feishu">
                    <div class="value" id="totalFeishuFailed">0</div>
                    <div class="label">飛書❌</div>
                </div>
            </div>
        </div>
        
        <div class="card">
            <h2>➕ 建立新 BOSS 群組</h2>
            <div class="flex-row">
                <input type="text" id="newGroupId" placeholder="群組 ID (英文/數字)" style="width: 150px;">
                <input type="text" id="newGroupName" placeholder="顯示名稱">
                <button class="btn btn-success" onclick="createGroup()">🆕 建立</button>
            </div>
        </div>
        
        <div class="card">
            <h2>🎯 BOSS 群組管理</h2>
            <div id="groupList"></div>
        </div>
        
        <div class="card">
            <h2>📖 預設端點</h2>
            <div style="font-size: 0.85em; line-height: 1.8;">
                <p style="padding: 10px; background: rgba(0,255,136,0.1); border-radius: 5px; font-family: monospace;">
                    🎯 <strong>/webhook/a</strong> → 喵z<br>
                    🎯 <strong>/webhook/b</strong> → 蘑菇<br>
                    🎯 <strong>/webhook/c</strong> → 仙人<br>
                    🎯 <strong>/webhook/d</strong> → 黑輪<br>
                    🎯 <strong>/webhook/xb</strong> → 小巴
                </p>
                <p style="margin-top: 10px;">
                    📱 <strong>飛書功能：</strong>在群組中配置飛書 Webhook 後，圖片會自動上傳並顯示
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
                document.getElementById('totalFeishuSent').textContent = data.total_feishu_sent || 0;
                document.getElementById('totalFeishuFailed').textContent = data.total_feishu_failed || 0;
                
                renderGroups(data.groups);
            } catch (e) { console.error(e); }
        }
        
        function renderGroups(groups) {
            const container = document.getElementById('groupList');
            
            if (!groups || groups.length === 0) {
                container.innerHTML = '<div class="no-data">尚未建立任何群組</div>';
                return;
            }
            
            container.innerHTML = groups.map(g => `
                <div class="group-card">
                    <div class="group-header" onclick="toggleGroup('${g.group_id}')">
                        <div class="group-title">
                            <span>${g.display_name}</span>
                            <span class="id">${g.group_id}</span>
                            ${g.feishu_enabled ? '<span class="badge badge-feishu">飛書</span>' : ''}
                        </div>
                        <div class="group-stats-mini">
                            <span>📥${g.received}</span>
                            <span>D✅${g.sent}</span>
                            <span>飛✅${g.feishu_sent || 0}</span>
                        </div>
                    </div>
                    <div class="group-body ${openGroups.has(g.group_id) ? 'open' : ''}" id="group-${g.group_id}">
                        <div class="section-title">📡 接收端點</div>
                        <div class="endpoint-box">
                            <span>${baseUrl}/webhook/${g.group_id}</span>
                            <button class="copy-btn" onclick="copyText('${baseUrl}/webhook/${g.group_id}')">📋</button>
                        </div>
                        
                        <div class="section-title">📱 飛書 Webhook</div>
                        <div class="feishu-box">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                                <span class="title">飛書轉發</span>
                                <span class="feishu-status ${g.feishu_enabled ? 'enabled' : 'disabled'}">
                                    ${g.feishu_enabled ? '✅啟用' : '❌未啟用'}
                                </span>
                            </div>
                            <div class="flex-row">
                                <input type="text" id="feishu-input-${g.group_id}" placeholder="飛書 Webhook URL" value="${g.feishu_webhook_url || ''}">
                                <button class="btn btn-feishu btn-sm" onclick="setFeishuWebhook('${g.group_id}')">💾</button>
                                <button class="btn btn-purple btn-sm" onclick="testFeishu('${g.group_id}')">🧪</button>
                            </div>
                        </div>
                        
                        <div class="section-title">🔗 Discord Webhook</div>
                        <div class="flex-row">
                            <input type="text" id="webhook-input-${g.group_id}" placeholder="Discord Webhook URL">
                            <button class="btn btn-success btn-sm" onclick="addWebhook('${g.group_id}')">➕</button>
                        </div>
                        
                        ${g.webhook_details.length ? g.webhook_details.map((w, i) => `
                            <div class="webhook-item ${w.is_next ? 'next' : ''}">
                                <div>
                                    <strong>#${w.index}</strong>
                                    ${w.is_next ? '<span class="badge badge-next">下一個</span>' : ''}
                                    <div class="webhook-url">${w.url_preview}</div>
                                    <div class="webhook-stats">✅${w.sent} ❌${w.failed}</div>
                                </div>
                                <button class="btn btn-danger btn-sm" onclick="removeWebhook('${g.group_id}', ${i})">🗑️</button>
                            </div>
                        `).join('') : '<div class="no-data">尚未添加 Discord Webhook</div>'}
                        
                        <div class="section-title">📜 最近發送</div>
                        ${g.history && g.history.length ? g.history.slice(0, 8).map(h => `
                            <div class="history-item">
                                <span>
                                    <span class="time">${h.time}</span>
                                    ${h.has_image ? '<span class="badge badge-img">📷</span>' : ''}
                                    ${h.content}
                                </span>
                                <span>${h.status}</span>
                            </div>
                        `).join('') : '<div class="no-data">暫無記錄</div>'}
                        
                        <div style="margin-top: 15px; display: flex; gap: 8px; justify-content: flex-end;">
                            <button class="btn btn-purple btn-sm" onclick="testGroup('${g.group_id}')">🧪 測試</button>
                            <button class="btn btn-danger btn-sm" onclick="deleteGroup('${g.group_id}')">🗑️ 刪除</button>
                        </div>
                    </div>
                </div>
            `).join('');
        }
        
        function toggleGroup(groupId) {
            if (openGroups.has(groupId)) openGroups.delete(groupId);
            else openGroups.add(groupId);
            const el = document.getElementById(`group-${groupId}`);
            if (el) el.classList.toggle('open');
        }
        
        function copyText(text) {
            navigator.clipboard.writeText(text);
            alert('✅ 已複製！');
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
            } else alert('❌ ' + result.message);
        }
        
        async function deleteGroup(groupId) {
            if (!confirm(`確定刪除群組 [${groupId}]？`)) return;
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
            
            if (result.success) { input.value = ''; loadData(); }
            else alert('❌ ' + result.message);
        }
        
        async function removeWebhook(groupId, index) {
            if (!confirm('確定移除？')) return;
            await fetch(`/api/group/${groupId}/webhook/${index}`, { method: 'DELETE' });
            loadData();
        }
        
        async function setFeishuWebhook(groupId) {
            const input = document.getElementById(`feishu-input-${groupId}`);
            const url = input.value.trim();
            
            const res = await fetch(`/api/group/${groupId}/feishu`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ url })
            });
            const result = await res.json();
            
            if (result.success) { alert('✅ 飛書 Webhook 已保存！'); loadData(); }
            else alert('❌ ' + result.message);
        }
        
        async function testFeishu(groupId) {
            const res = await fetch(`/api/group/${groupId}/feishu/test`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ content: `[測試] ${groupId.toUpperCase()} BOSS 飛書通知` })
            });
            const result = await res.json();
            alert(result.success ? '✅ 飛書測試成功！' : `❌ ${result.message}`);
            loadData();
        }
        
        async function testGroup(groupId) {
            const content = prompt('測試訊息:', `[測試] ${groupId.toUpperCase()} BOSS 通知`);
            if (!content) return;
            
            const res = await fetch(`/webhook/${groupId}`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ content })
            });
            const result = await res.json();
            alert(result.success ? `✅ ${result.message}` : `❌ ${result.message}`);
            loadData();
        }
        
        document.getElementById('newGroupId').addEventListener('keypress', e => { if (e.key === 'Enter') createGroup(); });
        document.getElementById('newGroupName').addEventListener('keypress', e => { if (e.key === 'Enter') createGroup(); });
        
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
    """接收指定群組的 Webhook"""
    try:
        source_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ',' in source_ip:
            source_ip = source_ip.split(',')[0].strip()
        
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
                
                if image_url:
                    if os.path.exists(image_url):
                        try:
                            with open(image_url, 'rb') as f:
                                image_data = f.read()
                            logger.info(f"[{group_id}] 📷 讀取本地圖片: {image_url}")
                        except Exception as e:
                            logger.error(f"[{group_id}] ❌ 讀取本地圖片失敗: {e}")
                    elif image_url.startswith(('http://', 'https://')):
                        try:
                            resp = requests.get(image_url, timeout=30)
                            if resp.status_code == 200:
                                image_data = resp.content
                                logger.info(f"[{group_id}] 📷 下載遠程圖片成功")
                        except Exception as e:
                            logger.error(f"[{group_id}] ❌ 下載遠程圖片失敗: {e}")
        else:
            content = request.form.get('content', '')
            if 'file' in request.files:
                file = request.files['file']
                if file:
                    image_data = file.read()
        
        if not content and not image_data:
            return jsonify({"success": False, "message": "無內容"}), 400
        
        logger.info(f"[{group_id}] 📥 收到消息: {content[:50]}..." if len(content) > 50 else f"[{group_id}] 📥 收到消息: {content}")
        if image_data:
            logger.info(f"[{group_id}] 📷 包含圖片: {len(image_data) / 1024:.2f} KB")
        
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


@app.route('/webhook', methods=['POST'])
def receive_webhook_default():
    return receive_webhook('default')


@app.route('/api/stats')
@requires_auth
def get_stats():
    return jsonify(manager.get_all_stats())


@app.route('/api/group', methods=['POST'])
@requires_auth
def create_group():
    data = request.get_json()
    group_id = data.get('group_id', '').strip()
    display_name = data.get('display_name')
    
    if not group_id:
        return jsonify({"success": False, "message": "請提供群組 ID"})
    
    if manager.get_group(group_id):
        return jsonify({"success": False, "message": "此群組 ID 已存在"})
    
    manager.create_group(group_id, display_name)
    return jsonify({"success": True, "message": "建立成功"})


@app.route('/api/group/<group_id>', methods=['DELETE'])
@requires_auth
def delete_group(group_id):
    success = manager.delete_group(group_id)
    return jsonify({"success": success})


@app.route('/api/group/<group_id>/webhook', methods=['POST'])
@requires_auth
def add_webhook_to_group(group_id):
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
    group = manager.get_group(group_id)
    if not group:
        return jsonify({"success": False, "message": "群組不存在"})
    
    success = group.remove_webhook(index)
    return jsonify({"success": success})


@app.route('/api/group/<group_id>/feishu', methods=['POST'])
@requires_auth
def set_feishu_webhook(group_id):
    group = manager.get_group(group_id)
    if not group:
        return jsonify({"success": False, "message": "群組不存在"})
    
    data = request.get_json()
    url = data.get('url', '').strip()
    success, message = group.set_feishu_webhook(url)
    return jsonify({"success": success, "message": message})


@app.route('/api/group/<group_id>/feishu/test', methods=['POST'])
@requires_auth
def test_feishu_webhook(group_id):
    group = manager.get_group(group_id)
    if not group:
        return jsonify({"success": False, "message": "群組不存在"})
    
    if not group.feishu_enabled or not group.feishu_webhook_url:
        return jsonify({"success": False, "message": "未配置飛書 Webhook"})
    
    data = request.get_json()
    content = data.get('content', f'[測試] {group_id.upper()} BOSS 通知')
    
    success = FeishuSender.send_with_retry(group.feishu_webhook_url, content)
    
    return jsonify({
        "success": success,
        "message": "發送成功" if success else "發送失敗"
    })


@app.route('/health')
def health():
    return jsonify({
        "status": "ok",
        "groups": len(manager.groups),
        "version": "3.1",
        "features": ["discord", "feishu", "image_upload"]
    })


# ================================================================================
# 主程式
# ================================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("  🔄 Webhook 中繼站 v3.1 - 整合飛書圖片轉發版")
    print("=" * 60)
    print(f"  📡 本地訪問: http://localhost:{PORT}")
    print(f"  🔐 密碼保護: {'啟用' if ADMIN_PASSWORD else '停用'}")
    print(f"  📱 飛書 App ID: {FEISHU_APP_ID[:10]}...")
    print("=" * 60)
    print()
    print("  🎯 預設 BOSS 群組:")
    for gid, name in DEFAULT_BOSS_GROUPS.items():
        print(f"    /webhook/{gid} → {name}")
    print()
    print("  🆕 新功能:")
    print("    - 支援同時轉發到 Discord 和飛書")
    print("    - 自動上傳圖片到飛書並顯示")
    print("    - 每個群組可獨立配置飛書 Webhook")
    print("=" * 60)
    
    app.run(
        host='0.0.0.0',
        port=PORT,
        debug=False,
        threaded=True
    )
