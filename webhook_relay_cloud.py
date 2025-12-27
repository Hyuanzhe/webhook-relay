#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
    🔄 Webhook 中繼站 v4.3 - 支援 Web 介面更新飛書憑證
================================================================================

核心功能：
    - 🆕 Web 介面動態更新飛書憑證（無需重啟）
    - 🆕 Webhook 時段控制（每個 Webhook 可設定自己的通知時間範圍）
    - 🆕 固定 Webhook（無論模式都會發送）
    - JSON 文件持久化存儲（自動保存/載入配置）
    - 支援硬編碼預設 Webhook（重啟自動恢復）
    - 兩種發送模式：同步模式 / 輪詢模式
    - Webhook 啟用/禁用開關（無需刪除）
    - 自定義 Webhook 名稱
    - 支援 Discord、飛書、企業微信

配置優先級：
    1. JSON 文件中的配置（如果存在）
    2. 硬編碼的 PRESET_WEBHOOKS 配置
    3. 環境變數 WEBHOOK_GROUPS

作者: @yyv3vnn
版本: 4.3
更新: 2025-12-28
================================================================================
"""

import json
import os
import threading
import time
import requests
import hashlib
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string, Response
from functools import wraps
from collections import deque
import logging
import re
import atexit

# ================================================================================
# 環境變數配置
# ================================================================================

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')
DEFAULT_GROUPS_JSON = os.environ.get('WEBHOOK_GROUPS', '{}')
PORT = int(os.environ.get('PORT', 5000))

# 飛書應用憑證（預設值，可透過 Web 介面更新）
FEISHU_APP_ID = os.environ.get('FEISHU_APP_ID', 'cli_a9dae0436f38dbcd')
FEISHU_APP_SECRET = os.environ.get('FEISHU_APP_SECRET', 'Brdq4CElOawyTEXZqUUhIv4xrfGoq7Eq')

# 配置文件路徑
CONFIG_FILE = os.environ.get('CONFIG_FILE', 'webhook_config.json')

# 時區設定（預設台灣 UTC+8）
TIMEZONE_OFFSET = int(os.environ.get('TIMEZONE_OFFSET', 8))  # 小時

# ================================================================================
# 時區輔助函數
# ================================================================================

def get_local_time() -> datetime:
    """獲取本地時間（根據 TIMEZONE_OFFSET 設定）"""
    from datetime import timezone, timedelta
    utc_now = datetime.now(timezone.utc)
    local_tz = timezone(timedelta(hours=TIMEZONE_OFFSET))
    return utc_now.astimezone(local_tz)

def get_local_time_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """獲取格式化的本地時間字串"""
    return get_local_time().strftime(fmt)

# ================================================================================
# 🔧 硬編碼預設配置（重啟自動恢復）
# ================================================================================

PRESET_WEBHOOKS = {
    # ============ 群組 A: 喵z ============
    "a": {
        "display_name": "喵z",
        "send_mode": "sync",
        "webhooks": [
            {
                "name": "喵喵1車",
                "url": "https://discordapp.com/api/webhooks/1441419865331335241/TIYTWKN7iE_Hs137IuD1o0ZrallCJG0XNxcu_tvZx4uSz0UaP37yvA9z8oqNoZGJ7r7S",
                "type": "discord",
                "enabled": True,
                "is_fixed": False
            },
            {
                "name": "喵z飛書通知",
                "url": "https://open.feishu.cn/open-apis/bot/v2/hook/9a199629-4368-4093-8dcf-bed6f2bae085",
                "type": "feishu",
                "enabled": True,
                "is_fixed": False
            },
        ]
    },
    
    # ============ 群組 B: 蘑菇 ============
    "b": {
        "display_name": "蘑菇",
        "send_mode": "sync",
        "webhooks": [
            {
                "name": "蘑菇1車",
                "url": "https://discordapp.com/api/webhooks/1443905667353022605/qoJ8CfGwH6PoSQ8p_jQZAEd9Fxfawwm6zYK55eOCXHNjxvOON90SEZkwWbepwxlLq5Pf",
                "type": "discord",
                "enabled": True,
                "is_fixed": False
            },
            {
                "name": "蘑菇飛書通知",
                "url": "https://open.feishu.cn/open-apis/bot/v2/hook/97a7254b-563f-4115-a0e6-9ebdd174bb7d",
                "type": "feishu",
                "enabled": True,
                "is_fixed": False
            },
        ]
    },
    
    # ============ 群組 C: 仙人 ============
    "c": {
        "display_name": "仙人娃娃",
        "send_mode": "sync",
        "webhooks": [
            {
                "name": "仙人娃娃1車",
                "url": "https://discordapp.com/api/webhooks/1444220275171397653/gGNvk6eeqWKh1HvkqdZFWP2Nc8bnPYV-u9LjWIZrPMmUjojBM8gB7drVwJK12iqgIm8-",
                "type": "discord",
                "enabled": True,
                "is_fixed": False
            },
            {
                "name": "仙人飛書通知",
                "url": "https://open.feishu.cn/open-apis/bot/v2/hook/8a52a977-a826-48c9-804e-a69baa75cada",
                "type": "feishu",
                "enabled": True,
                "is_fixed": False
            },
        ]
    },
    
    # ============ 群組 D: 黑輪 ============
    "d": {
        "display_name": "黑輪",
        "send_mode": "sync",
        "webhooks": [
            {
                "name": "黑輪1車",
                "url": "https://discordapp.com/api/webhooks/1448220103861735575/H9um9fDJBB5MvYkCcMe5HnT8zCknP8EhS13FNmNKrNJsk53EdOItJp5qz66qarp4Ipdf",
                "type": "discord",
                "enabled": True,
                "is_fixed": False
            },
            {
                "name": "黑輪飛書通知",
                "url": "https://open.feishu.cn/open-apis/bot/v2/hook/71381da3-e69a-486b-8c94-d2ebafae8e15",
                "type": "feishu",
                "enabled": True,
                "is_fixed": False
            },
        ]
    },
    
    # ============ 群組 XB: 小巴 ============
    "xb": {
        "display_name": "小巴",
        "send_mode": "sync",
        "webhooks": [
            {
                "name": "小巴",
                "url": "https://discordapp.com/api/webhooks/1444649970564071454/sFbE4LZCDz7MVQgjnJo0ggTSLUW_d7eZQvokpQzyceKAVSELXSzx7LO8Wy-sK5YaPmD-",
                "type": "discord",
                "enabled": True,
                "is_fixed": False
            },
            {
                "name": "小巴飛書通知",
                "url": "https://open.feishu.cn/open-apis/bot/v2/hook/7b80a188-da17-4817-b533-c123a970a51a",
                "type": "feishu",
                "enabled": True,
                "is_fixed": False
            },
            {
                "name": "小巴二車飛書通知",
                "url": "https://open.feishu.cn/open-apis/bot/v2/hook/a5ff3842-fbeb-4508-87cf-8e8e62824044",
                "type": "feishu",
                "enabled": True,
                "is_fixed": False
            },
            {
                "name": "小巴企業微信通知",
                "url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=c1fd1bc4-33b5-4e0c-b4b0-e6b814101048",
                "type": "wecom",
                "enabled": True,
                "is_fixed": False
            },
        ]
    },
    
    # ============ 群組 ss: 書生 ============
    "ss": {
        "display_name": "書生",
        "send_mode": "sync",
        "webhooks": [
            {
                "name": "書生",
                "url": "https://discordapp.com/api/webhooks/1451812376440606762/UJOjrJgGMsi1T45WqoeX3nI5HbzDdV74Dbzbw2-MBWuJhpktDc77y3q_NzNlDnGgnp6B",
                "type": "discord",
                "enabled": True,
                "is_fixed": False
            },
            {
                "name": "書生飛書通知",
                "url": "https://open.feishu.cn/open-apis/bot/v2/hook/a5ff3842-fbeb-4508-87cf-8e8e62824044",
                "type": "feishu",
                "enabled": True,
                "is_fixed": False
            },
        ]
    },
}

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
# 飛書圖片上傳器
# ================================================================================

class FeishuImageUploader:
    """飛書圖片上傳器 - 增強日誌版"""
    
    def __init__(self):
        self.upload_cache = {}
        self.token_cache = {'token': None, 'expire_time': 0}
        self.app_id = None
        self.app_secret = None
    
    def set_credentials(self, app_id: str, app_secret: str):
        """設定飛書憑證"""
        self.app_id = app_id
        self.app_secret = app_secret
    
    def get_tenant_access_token(self) -> str:
        """獲取 tenant_access_token（帶緩存）"""
        try:
            # 如果憑證未設定，使用全域變數
            app_id = self.app_id or FEISHU_APP_ID
            app_secret = self.app_secret or FEISHU_APP_SECRET
            
            if not app_id or not app_secret:
                logger.warning("⚠️ 飛書憑證未設定")
                return None
            
            current_time = time.time()
            if self.token_cache['token'] and current_time < self.token_cache['expire_time'] - 60:
                logger.info("🔄 使用緩存的 access_token")
                return self.token_cache['token']
            
            logger.info("🔑 開始獲取新的 access_token...")
            url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
            payload = {"app_id": app_id, "app_secret": app_secret}
            
            response = requests.post(url, json=payload, timeout=10)
            logger.info(f"📥 Token 響應狀態碼: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"📦 Token API 返回: code={result.get('code')}")
                
                if result.get('code') == 0:
                    token = result.get('tenant_access_token')
                    expire = result.get('expire', 7200)
                    self.token_cache['token'] = token
                    self.token_cache['expire_time'] = current_time + expire
                    logger.info("✅ 獲取飛書 access_token 成功")
                    return token
                else:
                    logger.error(f"❌ 飛書 API 錯誤: code={result.get('code')}, msg={result.get('msg')}")
            else:
                logger.error(f"❌ HTTP 請求失敗: {response.status_code}")
            
            return None
        except Exception as e:
            logger.error(f"❌ 獲取 access_token 異常: {e}", exc_info=True)
            return None
    
    def upload_image(self, image_data: bytes) -> str:
        """上傳圖片到飛書"""
        try:
            if not image_data:
                logger.warning("⚠️ 圖片數據為空，跳過上傳")
                return None
                
            logger.info(f"📷 開始上傳圖片，大小: {len(image_data)} bytes")
            
            img_hash = hashlib.md5(image_data).hexdigest()
            if img_hash in self.upload_cache:
                logger.info(f"♻️ 使用緩存的圖片 key")
                return self.upload_cache[img_hash]
            
            token = self.get_tenant_access_token()
            if not token:
                logger.error("❌ 無法獲取 access_token，圖片上傳失敗")
                return None
            
            url = "https://open.feishu.cn/open-apis/im/v1/images"
            headers = {"Authorization": f"Bearer {token}"}
            files = {'image': ('screenshot.png', image_data, 'image/png')}
            data = {'image_type': 'message'}
            
            logger.info(f"📡 開始上傳圖片到飛書...")
            response = requests.post(url, headers=headers, files=files, data=data, timeout=30)
            logger.info(f"📥 上傳響應狀態碼: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"📦 上傳 API 返回: code={result.get('code')}")
                
                if result.get('code') == 0:
                    image_key = result.get('data', {}).get('image_key')
                    if image_key:
                        self.upload_cache[img_hash] = image_key
                        logger.info(f"✅ 圖片上傳成功! image_key: {image_key}")
                        return image_key
                    else:
                        logger.error("❌ 響應中沒有 image_key")
                else:
                    logger.error(f"❌ 飛書圖片上傳 API 錯誤: code={result.get('code')}, msg={result.get('msg')}")
            else:
                logger.error(f"❌ 圖片上傳 HTTP 失敗: {response.status_code}, {response.text[:200]}")
            
            return None
        except Exception as e:
            logger.error(f"❌ 上傳圖片異常: {e}", exc_info=True)
            return None


feishu_uploader = FeishuImageUploader()

# ================================================================================
# Webhook 項目類別 - ⭐ 支援 Webhook 層級時段控制
# ================================================================================

class WebhookItem:
    """單個 Webhook 項目 - 支援時段控制"""
    
    def __init__(self, url: str, name: str = None, webhook_type: str = 'discord', 
                 enabled: bool = True, is_fixed: bool = False, webhook_id: str = None,
                 schedule_enabled: bool = False, schedule_start: str = "00:00", 
                 schedule_end: str = "23:59"):
        self.id = webhook_id or hashlib.md5(f"{url}{time.time()}".encode()).hexdigest()[:8]
        self.url = url
        self.name = name or self._generate_default_name(webhook_type)
        self.webhook_type = webhook_type
        self.enabled = enabled
        self.is_fixed = is_fixed
        self.stats = {"sent": 0, "failed": 0}
        self.created_at = get_local_time_str()
        
        # Webhook 時段控制
        self.schedule_enabled = schedule_enabled
        self.schedule_start = schedule_start
        self.schedule_end = schedule_end
    
    def _generate_default_name(self, webhook_type: str) -> str:
        timestamp = get_local_time_str("%H%M%S")
        type_map = {'discord': 'Discord', 'feishu': '飛書', 'wecom': '企業微信'}
        return f"{type_map.get(webhook_type, 'Webhook')}-{timestamp}"
    
    def is_in_schedule(self) -> bool:
        """檢查當前時間是否在允許的時段內"""
        if not self.schedule_enabled:
            return True
        
        current_time = get_local_time().strftime("%H:%M")
        
        # 處理跨日情況（例如 22:00 - 02:00）
        if self.schedule_start <= self.schedule_end:
            return self.schedule_start <= current_time <= self.schedule_end
        else:
            return current_time >= self.schedule_start or current_time <= self.schedule_end
    
    def to_dict(self) -> dict:
        """轉換為字典（用於顯示）"""
        return {
            "id": self.id,
            "name": self.name,
            "url_preview": f"...{self.url[-30:]}" if len(self.url) > 35 else self.url,
            "full_url": self.url,
            "webhook_type": self.webhook_type,
            "enabled": self.enabled,
            "is_fixed": self.is_fixed,
            "schedule_enabled": self.schedule_enabled,
            "schedule_start": self.schedule_start,
            "schedule_end": self.schedule_end,
            "is_in_schedule": self.is_in_schedule(),
            "sent": self.stats["sent"],
            "failed": self.stats["failed"],
            "created_at": self.created_at
        }
    
    def to_save_dict(self) -> dict:
        """轉換為字典（用於保存）"""
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "type": self.webhook_type,
            "enabled": self.enabled,
            "is_fixed": self.is_fixed,
            "schedule_enabled": self.schedule_enabled,
            "schedule_start": self.schedule_start,
            "schedule_end": self.schedule_end,
            "stats": self.stats,
            "created_at": self.created_at
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'WebhookItem':
        """從字典創建"""
        item = cls(
            url=data.get('url', ''),
            name=data.get('name'),
            webhook_type=data.get('type', 'discord'),
            enabled=data.get('enabled', True),
            is_fixed=data.get('is_fixed', False),
            webhook_id=data.get('id'),
            schedule_enabled=data.get('schedule_enabled', False),
            schedule_start=data.get('schedule_start', "00:00"),
            schedule_end=data.get('schedule_end', "23:59")
        )
        item.stats = data.get('stats', {"sent": 0, "failed": 0})
        item.created_at = data.get('created_at', item.created_at)
        return item


# ================================================================================
# 消息發送器
# ================================================================================

class MessageSender:
    """消息發送器"""
    
    @staticmethod
    def send_to_discord(webhook_url: str, content: str, image_data: bytes = None) -> bool:
        try:
            if image_data:
                files = {'file': ('screenshot.png', image_data, 'image/png')}
                data = {'content': content}
                response = requests.post(webhook_url, data=data, files=files, timeout=30)
            else:
                payload = {"content": content}
                response = requests.post(webhook_url, json=payload, timeout=15)
            return response.status_code in [200, 204]
        except Exception as e:
            logger.error(f"❌ Discord 發送失敗: {e}")
            return False
    
    @staticmethod
    def send_to_feishu(webhook_url: str, content: str, image_key: str = None) -> bool:
        try:
            content_blocks = []
            if content:
                for line in content.split('\n'):
                    if line.strip():
                        content_blocks.append([{"tag": "text", "text": line + "\n"}])
            if image_key:
                content_blocks.append([{"tag": "img", "image_key": image_key, "width": 800, "height": 600}])
            content_blocks.append([{"tag": "text", "text": f"\n⏰ {get_local_time_str()}"}])
            
            payload = {
                "msg_type": "post",
                "content": {"post": {"zh_cn": {"title": "🎯 BOSS 通知", "content": content_blocks}}}
            }
            response = requests.post(webhook_url, json=payload, headers={'Content-Type': 'application/json'}, timeout=10)
            if response.status_code == 200:
                result = response.json()
                return result.get('code') == 0 or result.get('StatusCode') == 0
            return False
        except Exception as e:
            logger.error(f"❌ 飛書發送失敗: {e}")
            return False
    
    @staticmethod
    def send_to_wecom(webhook_url: str, content: str, image_data: bytes = None) -> bool:
        """發送到企業微信群機器人（支援圖片 Base64）"""
        try:
            import base64
            
            # 先發送文字訊息
            text_payload = {
                "msgtype": "markdown",
                "markdown": {
                    "content": f"## 🎯 BOSS 通知\n\n{content}\n\n> ⏰ {get_local_time_str()}"
                }
            }
            
            response = requests.post(webhook_url, json=text_payload, timeout=10)
            result = response.json()
            
            if result.get('errcode') != 0:
                logger.error(f"❌ 企業微信文字發送失敗: {result}")
                return False
            
            # 如果有圖片，發送圖片
            if image_data:
                try:
                    img_base64 = base64.b64encode(image_data).decode()
                    img_md5 = hashlib.md5(image_data).hexdigest()
                    
                    image_payload = {
                        "msgtype": "image",
                        "image": {
                            "base64": img_base64,
                            "md5": img_md5
                        }
                    }
                    
                    img_response = requests.post(webhook_url, json=image_payload, timeout=30)
                    img_result = img_response.json()
                    
                    if img_result.get('errcode') == 0:
                        logger.info("✅ 企業微信圖片發送成功")
                    else:
                        logger.warning(f"⚠️ 企業微信圖片發送失敗: {img_result.get('errmsg')}")
                
                except Exception as img_e:
                    logger.warning(f"⚠️ 企業微信圖片發送異常: {img_e}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ 企業微信發送失敗: {e}")
            return False


# ================================================================================
# BOSS 群組類別
# ================================================================================

class BossGroup:
    """BOSS 群組 - 支援兩種發送模式 + 固定 Webhook"""
    
    MODE_SYNC = 'sync'
    MODE_ROUND_ROBIN = 'round_robin'
    
    def __init__(self, group_id: str, display_name: str = None):
        self.group_id = group_id.lower()
        self.display_name = display_name or f"{group_id.upper()} BOSS"
        self.webhooks: list[WebhookItem] = []
        self.send_mode = self.MODE_SYNC
        self.current_index = 0
        self.lock = threading.Lock()
        self.stats = {"received": 0, "total_sent": 0, "total_failed": 0}
        self.history = deque(maxlen=50)
        
        # 保存回調（由管理器設置）
        self._save_callback = None
    
    def set_save_callback(self, callback):
        """設置保存回調函數"""
        self._save_callback = callback
    
    def _trigger_save(self):
        """觸發保存"""
        if self._save_callback:
            self._save_callback()
    
    def set_send_mode(self, mode: str) -> tuple:
        with self.lock:
            if mode not in [self.MODE_SYNC, self.MODE_ROUND_ROBIN]:
                return False, f"無效的模式"
            self.send_mode = mode
            self._trigger_save()
            return True, f"已切換為{'同步模式' if mode == self.MODE_SYNC else '輪詢模式'}"
    
    def add_webhook(self, url: str, name: str = None, webhook_type: str = 'discord', is_fixed: bool = False) -> tuple:
        with self.lock:
            if not url or not url.startswith("https://"):
                return False, "無效的 URL（必須以 https:// 開頭）"
            for wh in self.webhooks:
                if wh.url == url:
                    return False, "此 Webhook URL 已存在"
            if webhook_type not in ['discord', 'feishu', 'wecom']:
                return False, "類型必須是 'discord'、'feishu' 或 'wecom'"
            
            webhook = WebhookItem(url, name, webhook_type, enabled=True, is_fixed=is_fixed)
            self.webhooks.append(webhook)
            fixed_text = " (固定)" if is_fixed else ""
            logger.info(f"[{self.group_id}] ➕ 添加 {webhook_type} Webhook: {webhook.name}{fixed_text}")
            self._trigger_save()
            return True, f"添加成功: {webhook.name}{fixed_text}"
    
    def remove_webhook(self, webhook_id: str) -> bool:
        with self.lock:
            for i, wh in enumerate(self.webhooks):
                if wh.id == webhook_id:
                    removed = self.webhooks.pop(i)
                    if self.current_index >= len(self.webhooks) and len(self.webhooks) > 0:
                        self.current_index = 0
                    logger.info(f"[{self.group_id}] ➖ 移除 Webhook: {removed.name}")
                    self._trigger_save()
                    return True
            return False
    
    def toggle_webhook(self, webhook_id: str, enabled: bool) -> tuple:
        with self.lock:
            for wh in self.webhooks:
                if wh.id == webhook_id:
                    wh.enabled = enabled
                    self._trigger_save()
                    return True, f"{wh.name} 已{'啟用' if enabled else '禁用'}"
            return False, "找不到此 Webhook"
    
    def toggle_webhook_fixed(self, webhook_id: str, is_fixed: bool) -> tuple:
        """切換 Webhook 的固定狀態"""
        with self.lock:
            for wh in self.webhooks:
                if wh.id == webhook_id:
                    wh.is_fixed = is_fixed
                    self._trigger_save()
                    return True, f"{wh.name} {'已設為' if is_fixed else '已取消'}固定發送"
            return False, "找不到此 Webhook"
    
    def update_webhook(self, webhook_id: str, name: str = None) -> tuple:
        with self.lock:
            for wh in self.webhooks:
                if wh.id == webhook_id:
                    if name:
                        wh.name = name
                        self._trigger_save()
                        return True, f"已重命名為: {name}"
            return False, "找不到此 Webhook"
    
    def get_enabled_webhooks(self, exclude_fixed: bool = False) -> list:
        """獲取啟用的 Webhook（可選擇排除固定的）"""
        webhooks = [wh for wh in self.webhooks if wh.enabled]
        if exclude_fixed:
            webhooks = [wh for wh in webhooks if not wh.is_fixed]
        return webhooks
    
    def get_fixed_webhooks(self) -> list:
        """獲取固定的 Webhook"""
        return [wh for wh in self.webhooks if wh.is_fixed and wh.enabled]
    
    def get_next_webhook_round_robin(self) -> WebhookItem:
        enabled = self.get_enabled_webhooks(exclude_fixed=True)
        if not enabled:
            return None
        self.current_index = self.current_index % len(enabled)
        webhook = enabled[self.current_index]
        self.current_index = (self.current_index + 1) % len(enabled)
        return webhook
    
    def relay_message(self, content: str, image_data: bytes = None, source_ip: str = "unknown") -> tuple:
        self.stats["received"] += 1
        timestamp = get_local_time_str()
        results = []
        
        feishu_image_key = None
        if image_data:
            feishu_image_key = feishu_uploader.upload_image(image_data)
        
        with self.lock:
            # 1. 先發送固定的 Webhook（檢查時段）
            fixed_webhooks = self.get_fixed_webhooks()
            for wh in fixed_webhooks:
                if wh.is_in_schedule():
                    success = self._send_to_webhook(wh, content, image_data, feishu_image_key)
                    results.append({"name": wh.name, "type": wh.webhook_type, "success": success, "is_fixed": True, "skipped": False})
                else:
                    logger.info(f"[{self.group_id}] ⏰ {wh.name} 不在通知時段內，已跳過")
                    results.append({"name": wh.name, "type": wh.webhook_type, "success": False, "is_fixed": True, "skipped": True})
            
            # 2. 根據模式發送非固定的 Webhook
            if self.send_mode == self.MODE_SYNC:
                enabled_webhooks = self.get_enabled_webhooks(exclude_fixed=True)
                if not enabled_webhooks and not fixed_webhooks:
                    self.history.appendleft({"time": timestamp, "content": content[:50], "status": "⚠️ 無啟用的 Webhook", "source": source_ip[-15:], "has_image": bool(image_data), "mode": "同步"})
                    return False, "無啟用的 Webhook", []
                for wh in enabled_webhooks:
                    if wh.is_in_schedule():
                        success = self._send_to_webhook(wh, content, image_data, feishu_image_key)
                        results.append({"name": wh.name, "type": wh.webhook_type, "success": success, "is_fixed": False, "skipped": False})
                    else:
                        logger.info(f"[{self.group_id}] ⏰ {wh.name} 不在通知時段內，已跳過")
                        results.append({"name": wh.name, "type": wh.webhook_type, "success": False, "is_fixed": False, "skipped": True})
            else:
                webhook = self.get_next_webhook_round_robin()
                if not webhook and not fixed_webhooks:
                    self.history.appendleft({"time": timestamp, "content": content[:50], "status": "⚠️ 無啟用的 Webhook", "source": source_ip[-15:], "has_image": bool(image_data), "mode": "輪詢"})
                    return False, "無啟用的 Webhook", []
                if webhook:
                    if webhook.is_in_schedule():
                        success = self._send_to_webhook(webhook, content, image_data, feishu_image_key)
                        results.append({"name": webhook.name, "type": webhook.webhook_type, "success": success, "is_fixed": False, "skipped": False})
                    else:
                        logger.info(f"[{self.group_id}] ⏰ {webhook.name} 不在通知時段內，已跳過")
                        results.append({"name": webhook.name, "type": webhook.webhook_type, "success": False, "is_fixed": False, "skipped": True})
        
        success_count = sum(1 for r in results if r["success"])
        fail_count = sum(1 for r in results if not r["success"] and not r.get("skipped", False))
        skipped_count = sum(1 for r in results if r.get("skipped", False))
        self.stats["total_sent"] += success_count
        self.stats["total_failed"] += fail_count
        
        status_parts = []
        for r in results:
            if r.get("skipped"):
                emoji = '⏰'
            else:
                emoji = '✅' if r['success'] else '❌'
            type_emoji = {'discord': '🔵', 'feishu': '📱', 'wecom': '💬'}.get(r['type'], '🔗')
            fixed_mark = '📌' if r.get('is_fixed') else ''
            status_parts.append(f"{emoji}{type_emoji}{fixed_mark}{r['name'][:8]}")
        
        mode_name = "同步" if self.send_mode == self.MODE_SYNC else "輪詢"
        
        message_parts = [f"成功: {success_count}"]
        if fail_count > 0:
            message_parts.append(f"失敗: {fail_count}")
        if skipped_count > 0:
            message_parts.append(f"時段外: {skipped_count}")
        
        self.history.appendleft({
            "time": timestamp, 
            "content": content[:50] + "..." if len(content) > 50 else content, 
            "status": " | ".join(status_parts), 
            "source": source_ip[-15:], 
            "has_image": bool(image_data), 
            "mode": mode_name
        })
        
        return success_count > 0, f"[{mode_name}] {', '.join(message_parts)}", results
    
    def _send_to_webhook(self, webhook: WebhookItem, content: str, image_data: bytes, feishu_image_key: str) -> bool:
        try:
            if webhook.webhook_type == 'discord':
                success = MessageSender.send_to_discord(webhook.url, content, image_data)
            elif webhook.webhook_type == 'feishu':
                success = MessageSender.send_to_feishu(webhook.url, content, feishu_image_key)
            elif webhook.webhook_type == 'wecom':
                success = MessageSender.send_to_wecom(webhook.url, content, image_data)
            else:
                success = False
            
            if success:
                webhook.stats["sent"] += 1
                logger.info(f"[{self.group_id}] ✅ → {webhook.name}")
            else:
                webhook.stats["failed"] += 1
                logger.error(f"[{self.group_id}] ❌ → {webhook.name}")
            return success
        except Exception as e:
            webhook.stats["failed"] += 1
            logger.error(f"[{self.group_id}] ❌ → {webhook.name}: {e}")
            return False
    
    def get_stats(self) -> dict:
        fixed_count = len(self.get_fixed_webhooks())
        return {
            "group_id": self.group_id,
            "display_name": self.display_name,
            "send_mode": self.send_mode,
            "send_mode_name": "同步模式" if self.send_mode == self.MODE_SYNC else "輪詢模式",
            "webhooks_total": len(self.webhooks),
            "webhooks_enabled": len(self.get_enabled_webhooks()),
            "webhooks_fixed": fixed_count,
            "current_index": self.current_index,
            "received": self.stats["received"],
            "total_sent": self.stats["total_sent"],
            "total_failed": self.stats["total_failed"],
            "success_rate": f"{(self.stats['total_sent'] / max(1, self.stats['received']) * 100):.1f}%",
            "webhooks": [wh.to_dict() for wh in self.webhooks],
            "history": list(self.history)[:20]
        }
    
    def to_save_dict(self) -> dict:
        """轉換為保存格式"""
        return {
            "display_name": self.display_name,
            "send_mode": self.send_mode,
            "current_index": self.current_index,
            "webhooks": [wh.to_save_dict() for wh in self.webhooks]
        }
    
    @classmethod
    def from_dict(cls, group_id: str, data: dict) -> 'BossGroup':
        """從字典創建群組"""
        group = cls(group_id, data.get('display_name'))
        group.send_mode = data.get('send_mode', cls.MODE_SYNC)
        group.current_index = data.get('current_index', 0)
        
        for wh_data in data.get('webhooks', []):
            webhook = WebhookItem.from_dict(wh_data)
            group.webhooks.append(webhook)
        
        return group


# ================================================================================
# 中繼站管理器（帶持久化 + 飛書憑證管理）
# ================================================================================

class WebhookRelayManager:
    """Webhook 中繼站管理器 - 支援持久化存儲 + 飛書憑證管理"""
    
    def __init__(self):
        self.groups = {}
        self.lock = threading.Lock()
        self.start_time = get_local_time()
        self._save_lock = threading.Lock()
        self._save_timer = None
        
        # 飛書憑證（可動態更新）
        self.feishu_app_id = FEISHU_APP_ID
        self.feishu_app_secret = FEISHU_APP_SECRET
        
        # 載入配置
        self._load_config()
        
        # 註冊退出時保存
        atexit.register(self._save_config_sync)
        
        logger.info("=" * 60)
        logger.info("🔄 Webhook 中繼站 v4.3 (飛書憑證管理版)")
        logger.info(f"📡 已配置 {len(self.groups)} 個 BOSS 群組")
        logger.info(f"💾 配置文件: {CONFIG_FILE}")
        logger.info(f"🕐 時區: UTC{'+' if TIMEZONE_OFFSET >= 0 else ''}{TIMEZONE_OFFSET}")
        logger.info(f"🔐 密碼保護: {'啟用' if ADMIN_PASSWORD else '停用'}")
        logger.info(f"📱 飛書 APP ID: {self.feishu_app_id[:10]}..." if self.feishu_app_id else "📱 飛書憑證: 未設定")
        logger.info("=" * 60)
    
    def _load_config(self):
        """載入配置（優先順序：JSON > 硬編碼 > 環境變數）"""
        loaded = False
        
        # 1. 嘗試從 JSON 文件載入
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                
                # 載入飛書憑證
				if 'feishu_credentials' in config:
					self.feishu_app_id = config['feishu_credentials'].get('app_id', FEISHU_APP_ID)
					self.feishu_app_secret = config['feishu_credentials'].get('app_secret', FEISHU_APP_SECRET)
					
					# 更新全域變數
					global FEISHU_APP_ID, FEISHU_APP_SECRET
					FEISHU_APP_ID = self.feishu_app_id
					FEISHU_APP_SECRET = self.feishu_app_secret
					
					# 更新上傳器的憑證  ← 添加這行
					feishu_uploader.set_credentials(self.feishu_app_id, self.feishu_app_secret)
					
					logger.info(f"✅ 從 JSON 載入飛書憑證: {self.feishu_app_id[:10]}...")
                
                for group_id, group_data in config.get('groups', {}).items():
                    group = BossGroup.from_dict(group_id, group_data)
                    group.set_save_callback(self._schedule_save)
                    self.groups[group_id] = group
                
                logger.info(f"✅ 從 JSON 文件載入 {len(self.groups)} 個群組")
                loaded = True
            except Exception as e:
                logger.error(f"❌ 載入 JSON 配置失敗: {e}")
        
        # 2. 如果 JSON 載入失敗，使用硬編碼配置
        if not loaded:
            logger.info("📦 使用硬編碼預設配置...")
            for group_id, preset in PRESET_WEBHOOKS.items():
                group = BossGroup(group_id, preset.get('display_name'))
                group.send_mode = preset.get('send_mode', BossGroup.MODE_SYNC)
                group.set_save_callback(self._schedule_save)
                
                for wh_preset in preset.get('webhooks', []):
                    if wh_preset.get('url'):
                        webhook = WebhookItem(
                            url=wh_preset['url'],
                            name=wh_preset.get('name'),
                            webhook_type=wh_preset.get('type', 'discord'),
                            enabled=wh_preset.get('enabled', True),
                            is_fixed=wh_preset.get('is_fixed', False)
                        )
                        group.webhooks.append(webhook)
                
                self.groups[group_id] = group
                wh_count = len(group.webhooks)
                if wh_count > 0:
                    logger.info(f"   ✅ {group_id} → {preset.get('display_name')} ({wh_count} webhooks)")
                else:
                    logger.info(f"   ✅ {group_id} → {preset.get('display_name')}")
            
            # 首次保存
            self._save_config_sync()
        
        # 3. 從環境變數補充（可選）
        self._load_from_env()
    
    def _load_from_env(self):
        """從環境變數載入補充配置"""
        try:
            if DEFAULT_GROUPS_JSON and DEFAULT_GROUPS_JSON != '{}':
                groups_config = json.loads(DEFAULT_GROUPS_JSON)
                for group_id, webhooks in groups_config.items():
                    group = self.get_or_create_group(group_id)
                    for webhook_url in webhooks:
                        exists = any(wh.url == webhook_url for wh in group.webhooks)
                        if not exists:
                            group.add_webhook(webhook_url)
        except json.JSONDecodeError as e:
            logger.error(f"❌ 解析 WEBHOOK_GROUPS 失敗: {e}")
    
    def _schedule_save(self):
        """排程保存（防抖動，延遲2秒）"""
        with self._save_lock:
            if self._save_timer:
                self._save_timer.cancel()
            self._save_timer = threading.Timer(2.0, self._save_config_sync)
            self._save_timer.start()
    
    def _save_config_sync(self):
        """同步保存配置到 JSON 文件"""
        try:
            config = {
                "version": "4.3",
                "updated_at": get_local_time_str(),
                "feishu_credentials": {
                    "app_id": self.feishu_app_id,
                    "app_secret": self.feishu_app_secret
                },
                "groups": {}
            }
            
            with self.lock:
                for group_id, group in self.groups.items():
                    config["groups"][group_id] = group.to_save_dict()
            
            # 先寫入臨時文件，再重命名（原子操作）
            temp_file = CONFIG_FILE + ".tmp"
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            
            os.replace(temp_file, CONFIG_FILE)
            logger.info(f"💾 配置已保存到 {CONFIG_FILE}")
            
        except Exception as e:
            logger.error(f"❌ 保存配置失敗: {e}")
    
    def update_feishu_credentials(self, app_id: str, app_secret: str) -> tuple:
		"""更新飛書應用憑證"""
		try:
			if not app_id or not app_secret:
				return False, "APP ID 和 APP Secret 不能為空"
			
			with self.lock:
				self.feishu_app_id = app_id.strip()
				self.feishu_app_secret = app_secret.strip()
			
			# 更新全域變數
			global FEISHU_APP_ID, FEISHU_APP_SECRET
			FEISHU_APP_ID = self.feishu_app_id
			FEISHU_APP_SECRET = self.feishu_app_secret
			
			# 更新上傳器的憑證  ← 添加這行
			feishu_uploader.set_credentials(self.feishu_app_id, self.feishu_app_secret)
			
			# 清空上傳器的 token 緩存，強制重新獲取
			feishu_uploader.token_cache = {'token': None, 'expire_time': 0}
			
			self._schedule_save()
			logger.info(f"✅ 飛書憑證已更新: {app_id[:10]}...")
			
			return True, "飛書憑證已更新並保存"
        except Exception as e:
            logger.error(f"❌ 更新飛書憑證失敗: {e}")
            return False, f"更新失敗: {str(e)}"
    
    def get_feishu_credentials(self) -> dict:
        """獲取飛書憑證（隱藏部分內容）"""
        return {
            "app_id": self.feishu_app_id,
            "app_id_masked": f"{self.feishu_app_id[:10]}..." if self.feishu_app_id and len(self.feishu_app_id) > 10 else self.feishu_app_id,
            "app_secret": self.feishu_app_secret,
            "app_secret_masked": f"{self.feishu_app_secret[:8]}..." if self.feishu_app_secret and len(self.feishu_app_secret) > 8 else "***",
            "is_configured": bool(self.feishu_app_id and self.feishu_app_secret)
        }
    
    def create_group(self, group_id: str, display_name: str = None) -> BossGroup:
        with self.lock:
            clean_id = re.sub(r'[^a-zA-Z0-9_]', '', group_id.lower())
            if not clean_id:
                clean_id = "default"
            
            if clean_id not in self.groups:
                group = BossGroup(clean_id, display_name)
                group.set_save_callback(self._schedule_save)
                self.groups[clean_id] = group
                logger.info(f"🆕 建立群組: {clean_id}")
                self._schedule_save()
            
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
                self._schedule_save()
                return True
            return False
    
    def get_all_stats(self) -> dict:
        uptime = get_local_time() - self.start_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        
        total_received = sum(g.stats["received"] for g in self.groups.values())
        total_sent = sum(g.stats["total_sent"] for g in self.groups.values())
        total_failed = sum(g.stats["total_failed"] for g in self.groups.values())
        
        return {
            "uptime": f"{hours}h {minutes}m {seconds}s",
            "total_groups": len(self.groups),
            "total_received": total_received,
            "total_sent": total_sent,
            "total_failed": total_failed,
            "success_rate": f"{(total_sent / max(1, total_received) * 100):.1f}%",
            "config_file": CONFIG_FILE,
            "timezone": f"UTC{'+' if TIMEZONE_OFFSET >= 0 else ''}{TIMEZONE_OFFSET}",
            "current_time": get_local_time_str(),
            "groups": [g.get_stats() for g in self.groups.values()]
        }
    
    def force_save(self):
        """強制立即保存"""
        self._save_config_sync()


# 建立全域管理器
manager = WebhookRelayManager()

# ================================================================================
# 密碼驗證
# ================================================================================

def check_auth(username, password):
    return password == ADMIN_PASSWORD

def authenticate():
    return Response('需要密碼才能訪問管理介面\n', 401,
                   {'WWW-Authenticate': 'Basic realm="Webhook Relay Admin"'})

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
# API 路由
# ================================================================================

@app.route('/')
@requires_auth
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/webhook/<group_id>', methods=['POST'])
def receive_webhook(group_id):
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
            attachments = data.get('attachments', [])
            if attachments:
                image_url = attachments[0].get('url', '')
                if image_url:
                    if os.path.exists(image_url):
                        with open(image_url, 'rb') as f:
                            image_data = f.read()
                    elif image_url.startswith(('http://', 'https://')):
                        try:
                            resp = requests.get(image_url, timeout=30)
                            if resp.status_code == 200:
                                image_data = resp.content
                        except:
                            pass
        else:
            content = request.form.get('content', '')
            if 'file' in request.files:
                image_data = request.files['file'].read()
        
        if not content and not image_data:
            return jsonify({"success": False, "message": "無內容"}), 400
        
        logger.info(f"[{group_id}] 📥 {content[:50]}...")
        success, message, details = group.relay_message(content, image_data, source_ip)
        
        return jsonify({
            "success": success, 
            "message": message, 
            "group_id": group_id, 
            "mode": group.send_mode, 
            "details": details
        })
    except Exception as e:
        logger.error(f"❌ [{group_id}] {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/webhook', methods=['POST'])
def receive_webhook_default():
    return receive_webhook('default')


@app.route('/api/stats')
@requires_auth
def get_stats():
    return jsonify(manager.get_all_stats())


@app.route('/api/feishu/credentials', methods=['GET'])
@requires_auth
def get_feishu_credentials():
    """獲取飛書憑證（脫敏）"""
    return jsonify(manager.get_feishu_credentials())


@app.route('/api/feishu/credentials', methods=['POST'])
@requires_auth
def update_feishu_credentials():
    """更新飛書憑證"""
    data = request.get_json()
    app_id = data.get('app_id', '').strip()
    app_secret = data.get('app_secret', '').strip()
    
    success, message = manager.update_feishu_credentials(app_id, app_secret)
    return jsonify({"success": success, "message": message})


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
    return jsonify({"success": manager.delete_group(group_id)})


@app.route('/api/group/<group_id>/mode', methods=['POST'])
@requires_auth
def set_group_mode(group_id):
    group = manager.get_group(group_id)
    if not group:
        return jsonify({"success": False, "message": "群組不存在"})
    data = request.get_json()
    success, message = group.set_send_mode(data.get('mode', ''))
    return jsonify({"success": success, "message": message})


@app.route('/api/group/<group_id>/webhook', methods=['POST'])
@requires_auth
def add_webhook_to_group(group_id):
    group = manager.get_group(group_id)
    if not group:
        return jsonify({"success": False, "message": "群組不存在"})
    data = request.get_json()
    success, message = group.add_webhook(
        data.get('url', '').strip(), 
        data.get('name'), 
        data.get('webhook_type', 'discord'),
        data.get('is_fixed', False)
    )
    return jsonify({"success": success, "message": message})


@app.route('/api/group/<group_id>/webhook/<webhook_id>', methods=['DELETE'])
@requires_auth
def remove_webhook_from_group(group_id, webhook_id):
    group = manager.get_group(group_id)
    if not group:
        return jsonify({"success": False, "message": "群組不存在"})
    return jsonify({"success": group.remove_webhook(webhook_id)})


@app.route('/api/group/<group_id>/webhook/<webhook_id>', methods=['PATCH'])
@requires_auth
def update_webhook(group_id, webhook_id):
    group = manager.get_group(group_id)
    if not group:
        return jsonify({"success": False, "message": "群組不存在"})
    data = request.get_json()
    success, message = group.update_webhook(webhook_id, data.get('name'))
    return jsonify({"success": success, "message": message})


@app.route('/api/group/<group_id>/webhook/<webhook_id>/toggle', methods=['POST'])
@requires_auth
def toggle_webhook(group_id, webhook_id):
    group = manager.get_group(group_id)
    if not group:
        return jsonify({"success": False, "message": "群組不存在"})
    data = request.get_json()
    success, message = group.toggle_webhook(webhook_id, data.get('enabled', True))
    return jsonify({"success": success, "message": message})


@app.route('/api/group/<group_id>/webhook/<webhook_id>/fixed', methods=['POST'])
@requires_auth
def toggle_webhook_fixed(group_id, webhook_id):
    group = manager.get_group(group_id)
    if not group:
        return jsonify({"success": False, "message": "群組不存在"})
    data = request.get_json()
    success, message = group.toggle_webhook_fixed(webhook_id, data.get('is_fixed', False))
    return jsonify({"success": success, "message": message})


@app.route('/api/group/<group_id>/webhook/<webhook_id>/schedule', methods=['POST'])
@requires_auth
def set_webhook_schedule(group_id, webhook_id):
    """⭐ 設定單個 Webhook 的時段控制"""
    group = manager.get_group(group_id)
    if not group:
        return jsonify({"success": False, "message": "群組不存在"})
    
    webhook = next((wh for wh in group.webhooks if wh.id == webhook_id), None)
    if not webhook:
        return jsonify({"success": False, "message": "找不到此 Webhook"})
    
    data = request.get_json()
    webhook.schedule_enabled = data.get('enabled', False)
    if data.get('start_time'):
        webhook.schedule_start = data.get('start_time')
    if data.get('end_time'):
        webhook.schedule_end = data.get('end_time')
    
    manager.force_save()
    
    status = f"{webhook.name} 時段控制已{'啟用' if webhook.schedule_enabled else '停用'}"
    if webhook.schedule_enabled and data.get('start_time') and data.get('end_time'):
        status += f" ({webhook.schedule_start} - {webhook.schedule_end})"
    
    return jsonify({"success": True, "message": status})


@app.route('/api/group/<group_id>/webhook/<webhook_id>/test', methods=['POST'])
@requires_auth
def test_single_webhook(group_id, webhook_id):
    group = manager.get_group(group_id)
    if not group:
        return jsonify({"success": False, "message": "群組不存在"})
    
    webhook = next((wh for wh in group.webhooks if wh.id == webhook_id), None)
    if not webhook:
        return jsonify({"success": False, "message": "找不到此 Webhook"})
    
    data = request.get_json()
    content = data.get('content', f'[測試] {webhook.name}')
    
    if webhook.webhook_type == 'discord':
        success = MessageSender.send_to_discord(webhook.url, content)
    elif webhook.webhook_type == 'feishu':
        success = MessageSender.send_to_feishu(webhook.url, content)
    elif webhook.webhook_type == 'wecom':
        success = MessageSender.send_to_wecom(webhook.url, content)
    else:
        success = False
    
    if success:
        webhook.stats["sent"] += 1
    else:
        webhook.stats["failed"] += 1
    
    return jsonify({"success": success, "message": "發送成功" if success else "發送失敗"})


@app.route('/api/save', methods=['POST'])
@requires_auth
def force_save():
    manager.force_save()
    return jsonify({"success": True, "message": "已保存"})


@app.route('/health')
def health():
    return jsonify({
        "status": "ok", 
        "version": "4.3", 
        "groups": len(manager.groups), 
        "config_file": CONFIG_FILE
    })

# ================================================================================
# HTML 模板
# ================================================================================

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🔄 Webhook 中繼站 v4.3</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Microsoft JhengHei', 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #0f0f23 0%, #1a1a3e 50%, #0f0f23 100%);
            min-height: 100vh;
            color: #fff;
            padding: 15px;
        }
        .container { max-width: 1100px; margin: 0 auto; }
        
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
        .config-info {
            text-align: center;
            font-size: 0.75em;
            color: #666;
            margin-bottom: 15px;
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
            font-size: 1.5em;
            font-weight: bold;
            color: #00d4ff;
        }
        .stat-box .label { font-size: 0.7em; opacity: 0.7; margin-top: 3px; }
        
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
            flex-wrap: wrap;
            gap: 10px;
        }
        .group-header:hover { background: linear-gradient(90deg, rgba(0,212,255,0.25), rgba(0,255,136,0.15)); }
        .group-title {
            font-weight: bold;
            font-size: 1.1em;
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
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
            flex-wrap: wrap;
        }
        .group-body {
            padding: 15px;
            display: none;
        }
        .group-body.open { display: block; }
        
        .mode-selector {
            display: flex;
            gap: 10px;
            margin: 10px 0;
            flex-wrap: wrap;
        }
        .mode-btn {
            padding: 8px 16px;
            border-radius: 20px;
            border: 2px solid rgba(255,255,255,0.2);
            background: transparent;
            color: #fff;
            cursor: pointer;
            font-size: 0.85em;
            transition: all 0.2s;
        }
        .mode-btn:hover { border-color: #00d4ff; }
        .mode-btn.active {
            background: linear-gradient(135deg, #00d4ff, #0088ff);
            border-color: #00d4ff;
        }
        .mode-btn.active-rr {
            background: linear-gradient(135deg, #ff88ff, #aa55ff);
            border-color: #ff88ff;
        }
        
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
            border-radius: 8px;
            padding: 12px;
            margin-bottom: 8px;
            border: 1px solid rgba(255,255,255,0.08);
            transition: all 0.2s;
        }
        .webhook-item.disabled {
            opacity: 0.5;
            background: rgba(100,100,100,0.1);
        }
        .webhook-item.next {
            border-left: 3px solid #00ff88;
            background: rgba(0,255,136,0.08);
        }
        .webhook-item.fixed {
            border-left: 3px solid #ff88ff;
            background: rgba(255,136,255,0.08);
        }
        .webhook-item.schedule-inactive {
            border-left: 3px solid #fbbf24;
            background: rgba(251,191,36,0.08);
        }
        .webhook-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
            flex-wrap: wrap;
            gap: 8px;
        }
        .webhook-name {
            font-weight: bold;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .webhook-url { font-family: monospace; font-size: 0.75em; opacity: 0.5; word-break: break-all; margin-top: 4px; }
        .webhook-stats { font-size: 0.75em; opacity: 0.6; margin-top: 4px; }
        .webhook-controls { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
        
        .webhook-schedule-box {
            background: rgba(251,191,36,0.05);
            border: 1px solid rgba(251,191,36,0.2);
            border-radius: 6px;
            padding: 8px;
            margin-top: 8px;
            font-size: 0.8em;
        }
        .webhook-schedule-box.active {
            background: rgba(0,255,136,0.05);
            border-color: rgba(0,255,136,0.2);
        }
        
        .toggle-switch { position: relative; width: 44px; height: 24px; }
        .toggle-switch input { opacity: 0; width: 0; height: 0; }
        .toggle-slider {
            position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0;
            background-color: #555; transition: 0.3s; border-radius: 24px;
        }
        .toggle-slider:before {
            position: absolute; content: ""; height: 18px; width: 18px; left: 3px; bottom: 3px;
            background-color: white; transition: 0.3s; border-radius: 50%;
        }
        .toggle-switch input:checked + .toggle-slider { background: linear-gradient(135deg, #00ff88, #00cc66); }
        .toggle-switch input:checked + .toggle-slider:before { transform: translateX(20px); }
        
        .btn {
            background: linear-gradient(135deg, #00d4ff, #0088ff);
            border: none; color: #fff; padding: 7px 12px; border-radius: 5px;
            cursor: pointer; font-size: 0.8em; transition: all 0.2s;
        }
        .btn:hover { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(0,212,255,0.3); }
        .btn-danger { background: linear-gradient(135deg, #ff4757, #ff2f2f); }
        .btn-success { background: linear-gradient(135deg, #00ff88, #00cc66); }
        .btn-purple { background: linear-gradient(135deg, #a855f7, #7c3aed); }
        .btn-pink { background: linear-gradient(135deg, #ff88ff, #ff44ff); }
        .btn-yellow { background: linear-gradient(135deg, #fbbf24, #f59e0b); }
        .btn-sm { padding: 4px 8px; font-size: 0.75em; }
        
        input[type="text"], input[type="password"], input[type="time"], select {
            padding: 8px 10px;
            border: 1px solid rgba(255,255,255,0.15);
            border-radius: 5px;
            background: rgba(255,255,255,0.05);
            color: #fff;
            font-size: 0.85em;
        }
        input[type="text"]::placeholder, input[type="password"]::placeholder { color: rgba(255,255,255,0.4); }
        input[type="text"]:focus, input[type="password"]:focus, input[type="time"]:focus, select:focus { outline: none; border-color: #00d4ff; }
        select { cursor: pointer; }
        select option { background: #1a1a3e; color: #fff; }
        
        .flex-row { display: flex; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; align-items: center; }
        .flex-row input { flex: 1; min-width: 150px; }
        
        .add-webhook-form {
            background: rgba(0,0,0,0.2);
            border-radius: 8px;
            padding: 12px;
            margin: 10px 0;
        }
        .add-webhook-form .title { font-size: 0.9em; color: #00d4ff; margin-bottom: 10px; }
        
        .history-item {
            background: rgba(255,255,255,0.02);
            border-radius: 4px;
            padding: 8px 10px;
            margin-bottom: 4px;
            font-size: 0.75em;
        }
        .history-item .time { color: #00d4ff; font-family: monospace; }
        .history-item .mode-tag { background: rgba(255,255,255,0.1); padding: 2px 6px; border-radius: 4px; font-size: 0.85em; }
        
        .badge { display: inline-block; padding: 2px 6px; border-radius: 6px; font-size: 0.65em; font-weight: bold; }
        .badge-discord { background: #5865F2; color: #fff; }
        .badge-feishu { background: #3b82f6; color: #fff; }
        .badge-wecom { background: #07C160; color: #fff; }
        .badge-next { background: #00ff88; color: #000; }
        .badge-fixed { background: #ff88ff; color: #000; }
        .badge-img { background: #ff88ff; color: #000; }
        .badge-sync { background: #00d4ff; color: #000; }
        .badge-rr { background: #ff88ff; color: #000; }
        .badge-saved { background: #00ff88; color: #000; }
        .badge-schedule { background: #fbbf24; color: #000; }
        
        .copy-btn {
            background: transparent; border: 1px solid rgba(255,255,255,0.3); color: #fff;
            padding: 3px 8px; border-radius: 4px; cursor: pointer; font-size: 0.75em;
        }
        .copy-btn:hover { background: rgba(255,255,255,0.1); }
        
        .section-title {
            font-size: 0.9em; color: #00d4ff; margin: 15px 0 10px 0;
            padding-bottom: 5px; border-bottom: 1px solid rgba(255,255,255,0.1);
        }
        
        .no-data { opacity: 0.4; font-size: 0.8em; padding: 15px; text-align: center; background: rgba(0,0,0,0.1); border-radius: 6px; }
        
        .mode-info {
            background: rgba(0,212,255,0.1); border: 1px solid rgba(0,212,255,0.3);
            border-radius: 6px; padding: 10px; font-size: 0.8em; margin: 10px 0;
        }
        .mode-info.round_robin { background: rgba(255,136,255,0.1); border-color: rgba(255,136,255,0.3); }
        
        .save-indicator {
            position: fixed; bottom: 20px; right: 20px;
            background: rgba(0,255,136,0.9); color: #000; padding: 10px 20px;
            border-radius: 8px; font-weight: bold; display: none; z-index: 1000;
        }
        
        .feishu-status-ok { color: #00ff88; }
        .feishu-status-error { color: #ff4757; }
        
        @media (max-width: 600px) {
            .stats-grid { grid-template-columns: repeat(2, 1fr); }
            .group-header { flex-direction: column; align-items: flex-start; }
            .webhook-header { flex-direction: column; align-items: flex-start; }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🔄 Webhook 中繼站 v4.3</h1>
        <p class="subtitle">飛書憑證管理版 + Webhook 時段控制 + 固定 Webhook | 運行: <span id="uptime">-</span></p>
        <p class="config-info">💾 配置: <span id="configFile">-</span> | 🕐 時區: <span id="timezone">-</span> | 當前: <span id="currentTime">-</span></p>
        
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
        
        <div class="card">
            <h2>📱 飛書應用憑證設定</h2>
            <div style="font-size: 0.85em; margin-bottom: 10px; opacity: 0.8;">
                <p>用於上傳圖片到飛書。修改後會自動保存到配置文件，無需重啟。</p>
                <p>狀態: <span id="feishuStatus">載入中...</span></p>
            </div>
            <div class="flex-row">
                <input type="text" id="feishuAppId" placeholder="APP ID" style="flex: 1; min-width: 200px;">
                <input type="password" id="feishuAppSecret" placeholder="APP Secret" style="flex: 1; min-width: 200px;">
                <button class="btn btn-success" onclick="updateFeishuCredentials()">💾 保存</button>
                <button class="btn btn-purple btn-sm" onclick="toggleFeishuSecretVisibility()">👁️</button>
            </div>
        </div>
        
        <div class="card">
            <h2>➕ 建立新 BOSS 群組</h2>
            <div class="flex-row">
                <input type="text" id="newGroupId" placeholder="群組 ID (英文/數字)" style="max-width: 150px;">
                <input type="text" id="newGroupName" placeholder="顯示名稱">
                <button class="btn btn-success" onclick="createGroup()">🆕 建立</button>
            </div>
        </div>
        
        <div class="card">
            <h2>🎯 BOSS 群組管理</h2>
            <div id="groupList"></div>
        </div>
        
        <div class="card">
            <h2>📖 使用說明</h2>
            <div style="font-size: 0.85em; line-height: 1.8;">
                <p><strong>🆕 v4.3 新功能：</strong></p>
                <ul style="margin-left: 20px; margin-bottom: 10px;">
                    <li>📱 <strong>Web 介面管理飛書憑證</strong>：無需重啟，即時生效</li>
                    <li>⏰ <strong>Webhook 時段控制</strong>：每個 Webhook 可設定自己的通知時間範圍</li>
                    <li>📌 <strong>固定 Webhook</strong>：無論同步/輪詢模式都會發送</li>
                </ul>
                <p><strong>💾 持久化存儲：</strong></p>
                <ul style="margin-left: 20px; margin-bottom: 10px;">
                    <li>所有配置自動保存到 JSON 文件</li>
                    <li>重啟後自動恢復所有 Webhook 設定和飛書憑證</li>
                </ul>
                <p><strong>📡 發送模式：</strong></p>
                <ul style="margin-left: 20px; margin-bottom: 10px;">
                    <li><span class="badge badge-sync">同步模式</span> 同時發送到所有啟用的 Webhook</li>
                    <li><span class="badge badge-rr">輪詢模式</span> 輪流發送到下一個啟用的 Webhook</li>
                    <li><span class="badge badge-fixed">固定發送</span> 無論何種模式都會發送</li>
                    <li><span class="badge badge-schedule">⏰</span> 不在時段內的 Webhook 會自動跳過</li>
                </ul>
            </div>
        </div>
    </div>
    
    <div class="save-indicator" id="saveIndicator">💾 已自動保存</div>
    
    <script>
        const baseUrl = window.location.origin;
        let openGroups = new Set();
        let openScheduleBoxes = new Set();
        let inputStates = {};
        let isUserInteracting = false;
        let lastInteractionTime = 0;
        let lastFullData = null;
        
        document.addEventListener('DOMContentLoaded', function() {
            document.body.addEventListener('mousedown', function() {
                isUserInteracting = true;
                lastInteractionTime = Date.now();
            });
            
            document.body.addEventListener('keydown', function() {
                isUserInteracting = true;
                lastInteractionTime = Date.now();
            });
            
            document.body.addEventListener('focus', function(e) {
                if (e.target.matches('input, select, textarea')) {
                    isUserInteracting = true;
                    lastInteractionTime = Date.now();
                }
            }, true);
            
            setInterval(() => {
                if (Date.now() - lastInteractionTime > 5000) {
                    isUserInteracting = false;
                }
            }, 500);
            
            loadFeishuCredentials();
        });
        
        function showSaveIndicator() {
            const el = document.getElementById('saveIndicator');
            el.style.display = 'block';
            setTimeout(() => { el.style.display = 'none'; }, 2000);
        }
        
        function saveInputStates() {
            inputStates = {};
            
            const newGroupId = document.getElementById('newGroupId');
            const newGroupName = document.getElementById('newGroupName');
            if (newGroupId) inputStates.newGroupId = newGroupId.value;
            if (newGroupName) inputStates.newGroupName = newGroupName.value;
            
            document.querySelectorAll('[id^="webhook-name-"]').forEach(input => {
                inputStates[input.id] = input.value;
            });
            document.querySelectorAll('[id^="webhook-url-"]').forEach(input => {
                inputStates[input.id] = input.value;
            });
            document.querySelectorAll('[id^="webhook-type-"]').forEach(select => {
                inputStates[select.id] = select.value;
            });
            document.querySelectorAll('[id^="webhook-fixed-"]').forEach(checkbox => {
                inputStates[checkbox.id] = checkbox.checked;
            });
            
            document.querySelectorAll('[id^="schedule-start-"]').forEach(input => {
                inputStates[input.id] = input.value;
            });
            document.querySelectorAll('[id^="schedule-end-"]').forEach(input => {
                inputStates[input.id] = input.value;
            });
            document.querySelectorAll('[id^="schedule-enabled-"]').forEach(checkbox => {
                inputStates[checkbox.id] = checkbox.checked;
            });
        }
        
        function restoreInputStates() {
            for (const [id, value] of Object.entries(inputStates)) {
                const element = document.getElementById(id);
                if (element) {
                    if (element.type === 'checkbox') {
                        element.checked = value;
                    } else {
                        element.value = value;
                    }
                }
            }
        }
        
        function saveScheduleBoxStates() {
            openScheduleBoxes.clear();
            document.querySelectorAll('[id^="schedule-box-"]').forEach(box => {
                if (box.style.display !== 'none') {
                    const webhookId = box.id.replace('schedule-box-', '');
                    openScheduleBoxes.add(webhookId);
                }
            });
        }
        
        function restoreScheduleBoxStates() {
            openScheduleBoxes.forEach(webhookId => {
                const box = document.getElementById(`schedule-box-${webhookId}`);
                if (box) {
                    box.style.display = 'block';
                }
            });
        }
        
        function updateStatsOnly(data) {
            document.getElementById('uptime').textContent = data.uptime;
            document.getElementById('totalGroups').textContent = data.total_groups;
            document.getElementById('totalReceived').textContent = data.total_received;
            document.getElementById('totalSent').textContent = data.total_sent;
            document.getElementById('totalFailed').textContent = data.total_failed;
            document.getElementById('successRate').textContent = data.success_rate;
            document.getElementById('configFile').textContent = data.config_file || '-';
            document.getElementById('timezone').textContent = data.timezone || '-';
            document.getElementById('currentTime').textContent = data.current_time || '-';
        }
        
        async function loadData(forceFullRender = false) {
            try {
                const res = await fetch('/api/stats');
                const data = await res.json();
                
                lastFullData = data;
                
                if (isUserInteracting && !forceFullRender) {
                    console.log('用戶正在操作，只更新統計數據...');
                    updateStatsOnly(data);
                    return;
                }
                
                saveInputStates();
                saveScheduleBoxStates();
                
                updateStatsOnly(data);
                renderGroups(data.groups);
                
                restoreInputStates();
                restoreScheduleBoxStates();
            } catch (e) { 
                console.error(e); 
            }
        }
        
        async function loadFeishuCredentials() {
            try {
                const res = await fetch('/api/feishu/credentials');
                const data = await res.json();
                
                document.getElementById('feishuAppId').value = data.app_id || '';
                document.getElementById('feishuAppSecret').value = data.app_secret || '';
                
                const statusEl = document.getElementById('feishuStatus');
                if (data.is_configured) {
                    statusEl.innerHTML = `<span class="feishu-status-ok">✅ 已配置 (${data.app_id_masked})</span>`;
                } else {
                    statusEl.innerHTML = `<span class="feishu-status-error">❌ 未配置</span>`;
                }
            } catch (e) {
                console.error('載入飛書憑證失敗:', e);
            }
        }
        
        async function updateFeishuCredentials() {
            const appId = document.getElementById('feishuAppId').value.trim();
            const appSecret = document.getElementById('feishuAppSecret').value.trim();
            
            if (!appId || !appSecret) {
                return alert('請填寫完整的 APP ID 和 APP Secret');
            }
            
            const res = await fetch('/api/feishu/credentials', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ app_id: appId, app_secret: appSecret })
            });
            const result = await res.json();
            
            if (result.success) {
                showSaveIndicator();
                await loadFeishuCredentials();
                alert('✅ ' + result.message);
            } else {
                alert('❌ ' + result.message);
            }
        }
        
        function toggleFeishuSecretVisibility() {
            const input = document.getElementById('feishuAppSecret');
            input.type = input.type === 'password' ? 'text' : 'password';
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
                            <span class="badge ${g.send_mode === 'sync' ? 'badge-sync' : 'badge-rr'}">${g.send_mode_name}</span>
                            ${g.webhooks_fixed > 0 ? '<span class="badge badge-fixed">📌' + g.webhooks_fixed + '</span>' : ''}
                        </div>
                        <div class="group-stats-mini">
                            <span>📥${g.received}</span>
                            <span>✅${g.total_sent}</span>
                            <span>❌${g.total_failed}</span>
                            <span>🔗${g.webhooks_enabled}/${g.webhooks_total}</span>
                        </div>
                    </div>
                    <div class="group-body ${openGroups.has(g.group_id) ? 'open' : ''}" id="group-${g.group_id}">
                        <div class="section-title">📡 接收端點</div>
                        <div class="endpoint-box">
                            <span>${baseUrl}/webhook/${g.group_id}</span>
                            <button class="copy-btn" onclick="copyText('${baseUrl}/webhook/${g.group_id}')">📋 複製</button>
                        </div>
                        
                        <div class="section-title">⚙️ 發送模式</div>
                        <div class="mode-selector">
                            <button class="mode-btn ${g.send_mode === 'sync' ? 'active' : ''}" onclick="setMode('${g.group_id}', 'sync')">🔄 同步模式</button>
                            <button class="mode-btn ${g.send_mode === 'round_robin' ? 'active-rr' : ''}" onclick="setMode('${g.group_id}', 'round_robin')">🎯 輪詢模式</button>
                        </div>
                        <div class="mode-info ${g.send_mode}">
                            ${g.send_mode === 'sync' ? '💡 同步模式：每次通知會同時發送到所有<strong>啟用且在時段內</strong>的 Webhook（固定的也會發送）' : '💡 輪詢模式：每次通知會輪流發送到下一個<strong>啟用且在時段內</strong>的 Webhook（固定的也會發送）'}
                        </div>
                        
                        <div class="section-title">🔗 Webhook 列表 (${g.webhooks_enabled}/${g.webhooks_total} 啟用, ${g.webhooks_fixed} 固定)</div>
                        <div class="add-webhook-form">
                            <div class="title">➕ 添加新 Webhook</div>
                            <div class="flex-row">
                                <input type="text" id="webhook-name-${g.group_id}" placeholder="名稱 (可選)" style="max-width: 120px;">
                                <select id="webhook-type-${g.group_id}" style="max-width: 100px;">
                                    <option value="discord">Discord</option>
                                    <option value="feishu">飛書</option>
                                    <option value="wecom">企業微信</option>
                                </select>
                                <input type="text" id="webhook-url-${g.group_id}" placeholder="Webhook URL">
                                <label style="display: flex; align-items: center; gap: 4px; font-size: 0.85em;">
                                    <input type="checkbox" id="webhook-fixed-${g.group_id}">
                                    <span>📌 固定</span>
                                </label>
                                <button class="btn btn-success btn-sm" onclick="addWebhook('${g.group_id}')">➕</button>
                            </div>
                        </div>
                        
                        ${g.webhooks && g.webhooks.length ? g.webhooks.map((w, i) => {
                            const isNext = g.send_mode === 'round_robin' && w.enabled && !w.is_fixed && isNextWebhook(g, w.id);
                            const scheduleClass = w.schedule_enabled && !w.is_in_schedule ? 'schedule-inactive' : '';
                            return `
                            <div class="webhook-item ${!w.enabled ? 'disabled' : ''} ${isNext ? 'next' : ''} ${w.is_fixed ? 'fixed' : ''} ${scheduleClass}">
                                <div class="webhook-header">
                                    <div class="webhook-name">
                                        <span class="badge ${w.webhook_type === 'discord' ? 'badge-discord' : w.webhook_type === 'feishu' ? 'badge-feishu' : 'badge-wecom'}">
                                            ${w.webhook_type === 'discord' ? '🔵 Discord' : w.webhook_type === 'feishu' ? '📱 飛書' : '💬 企業微信'}
                                        </span>
                                        <span>${w.name}</span>
                                        ${w.is_fixed ? '<span class="badge badge-fixed">📌 固定</span>' : ''}
                                        ${isNext ? '<span class="badge badge-next">下一個</span>' : ''}
                                        ${w.schedule_enabled ? '<span class="badge badge-schedule">⏰ ' + w.schedule_start + '-' + w.schedule_end + '</span>' : ''}
                                    </div>
                                    <div class="webhook-controls">
                                        <label class="toggle-switch">
                                            <input type="checkbox" ${w.enabled ? 'checked' : ''} onchange="toggleWebhook('${g.group_id}', '${w.id}', this.checked)">
                                            <span class="toggle-slider"></span>
                                        </label>
                                        <button class="btn ${w.is_fixed ? 'btn-pink' : 'btn-purple'} btn-sm" 
                                                onclick="toggleFixed('${g.group_id}', '${w.id}', ${!w.is_fixed})" 
                                                title="${w.is_fixed ? '取消固定' : '設為固定'}">
                                            ${w.is_fixed ? '📌' : '📍'}
                                        </button>
                                        <button class="btn btn-yellow btn-sm" 
                                                onclick="toggleScheduleUI('${g.group_id}', '${w.id}')" 
                                                title="設定時段">
                                            ⏰
                                        </button>
                                        <button class="btn btn-purple btn-sm" onclick="renameWebhook('${g.group_id}', '${w.id}', '${w.name}')">✏️</button>
                                        <button class="btn btn-sm" onclick="testWebhook('${g.group_id}', '${w.id}')">🧪</button>
                                        <button class="btn btn-danger btn-sm" onclick="removeWebhook('${g.group_id}', '${w.id}')">🗑️</button>
                                    </div>
                                </div>
                                <div class="webhook-url">${w.url_preview}</div>
                                <div class="webhook-stats">✅ ${w.sent} | ❌ ${w.failed} | 📅 ${w.created_at}</div>
                                
                                <div class="webhook-schedule-box ${w.schedule_enabled ? 'active' : ''}" id="schedule-box-${w.id}" style="display: none;">
                                    <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px; flex-wrap: wrap;">
                                        <label class="toggle-switch">
                                            <input type="checkbox" id="schedule-enabled-${w.id}" ${w.schedule_enabled ? 'checked' : ''}>
                                            <span class="toggle-slider"></span>
                                        </label>
                                        <span style="font-size: 0.9em;">啟用時段控制</span>
                                    </div>
                                    <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
                                        <span style="font-size: 0.85em; opacity: 0.8;">從</span>
                                        <input type="time" id="schedule-start-${w.id}" value="${w.schedule_start}" style="max-width: 100px; padding: 4px;">
                                        <span style="font-size: 0.85em; opacity: 0.8;">到</span>
                                        <input type="time" id="schedule-end-${w.id}" value="${w.schedule_end}" style="max-width: 100px; padding: 4px;">
                                        <button class="btn btn-success btn-sm" onclick="saveWebhookSchedule('${g.group_id}', '${w.id}')">💾 保存</button>
                                    </div>
                                    ${w.schedule_enabled ? `
                                        <div style="margin-top: 6px; font-size: 0.85em; opacity: 0.8;">
                                            ${w.is_in_schedule ? '✅ 目前在通知時段內' : '⏰ 目前不在通知時段內'}
                                        </div>
                                    ` : ''}
                                </div>
                            </div>
                        `}).join('') : '<div class="no-data">尚未添加任何 Webhook</div>'}
                        
                        <div class="section-title">📜 最近發送記錄</div>
                        ${g.history && g.history.length ? g.history.slice(0, 8).map(h => `
                            <div class="history-item">
                                <div style="display: flex; justify-content: space-between; flex-wrap: wrap; gap: 5px;">
                                    <span><span class="time">${h.time}</span> <span class="mode-tag">${h.mode}</span> ${h.has_image ? '<span class="badge badge-img">📷</span>' : ''}</span>
                                    <span>${h.status}</span>
                                </div>
                                <div style="opacity: 0.6; margin-top: 4px;">${h.content}</div>
                            </div>
                        `).join('') : '<div class="no-data">暫無記錄</div>'}
                        
                        <div style="margin-top: 15px; display: flex; gap: 8px; justify-content: flex-end; flex-wrap: wrap;">
                            <button class="btn btn-purple btn-sm" onclick="testGroup('${g.group_id}')">🧪 測試群組</button>
                            <button class="btn btn-danger btn-sm" onclick="deleteGroup('${g.group_id}')">🗑️ 刪除群組</button>
                        </div>
                    </div>
                </div>
            `).join('');
        }
        
        function isNextWebhook(group, webhookId) {
            const enabled = group.webhooks.filter(w => w.enabled && !w.is_fixed);
            if (enabled.length === 0) return false;
            const idx = group.current_index % enabled.length;
            return enabled[idx] && enabled[idx].id === webhookId;
        }
        
        function toggleGroup(groupId) {
            if (openGroups.has(groupId)) openGroups.delete(groupId);
            else openGroups.add(groupId);
            document.getElementById(`group-${groupId}`)?.classList.toggle('open');
        }
        
        function toggleScheduleUI(groupId, webhookId) {
            const box = document.getElementById(`schedule-box-${webhookId}`);
            if (box.style.display === 'none') {
                box.style.display = 'block';
                openScheduleBoxes.add(webhookId);
            } else {
                box.style.display = 'none';
                openScheduleBoxes.delete(webhookId);
            }
        }
        
        async function saveWebhookSchedule(groupId, webhookId) {
            const enabled = document.getElementById(`schedule-enabled-${webhookId}`).checked;
            const startTime = document.getElementById(`schedule-start-${webhookId}`).value;
            const endTime = document.getElementById(`schedule-end-${webhookId}`).value;
            
            if (enabled && (!startTime || !endTime)) {
                return alert('請選擇開始和結束時間');
            }
            
            const res = await fetch(`/api/group/${groupId}/webhook/${webhookId}/schedule`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ 
                    enabled,
                    start_time: startTime,
                    end_time: endTime
                })
            });
            const result = await res.json();
            
            if (result.success) {
                showSaveIndicator();
                await loadData(true);
                alert('✅ ' + result.message);
            } else {
                alert('❌ ' + result.message);
            }
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
                showSaveIndicator();
                await loadData(true);
            } else alert('❌ ' + result.message);
        }
        
        async function deleteGroup(groupId) {
            if (!confirm(`確定刪除群組 [${groupId}]？`)) return;
            await fetch(`/api/group/${groupId}`, { method: 'DELETE' });
            openGroups.delete(groupId);
            showSaveIndicator();
            await loadData(true);
        }
        
        async function setMode(groupId, mode) {
            const res = await fetch(`/api/group/${groupId}/mode`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ mode })
            });
            const result = await res.json();
            if (result.success) { 
                showSaveIndicator(); 
                await loadData(true); 
            } else alert('❌ ' + result.message);
        }
        
        async function addWebhook(groupId) {
            const nameInput = document.getElementById(`webhook-name-${groupId}`);
            const typeSelect = document.getElementById(`webhook-type-${groupId}`);
            const urlInput = document.getElementById(`webhook-url-${groupId}`);
            const fixedCheckbox = document.getElementById(`webhook-fixed-${groupId}`);
            
            const name = nameInput.value.trim();
            const type = typeSelect.value;
            const url = urlInput.value.trim();
            const isFixed = fixedCheckbox.checked;
            
            if (!url) return alert('請輸入 Webhook URL');
            
            const res = await fetch(`/api/group/${groupId}/webhook`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ url, name: name || null, webhook_type: type, is_fixed: isFixed })
            });
            const result = await res.json();
            
            if (result.success) {
                nameInput.value = '';
                urlInput.value = '';
                typeSelect.value = 'discord';
                fixedCheckbox.checked = false;
                showSaveIndicator();
                await loadData(true);
            } else {
                alert('❌ ' + result.message);
            }
        }
        
        async function removeWebhook(groupId, webhookId) {
            if (!confirm('確定移除？')) return;
            await fetch(`/api/group/${groupId}/webhook/${webhookId}`, { method: 'DELETE' });
            openScheduleBoxes.delete(webhookId);
            showSaveIndicator();
            await loadData(true);
        }
        
        async function toggleWebhook(groupId, webhookId, enabled) {
            await fetch(`/api/group/${groupId}/webhook/${webhookId}/toggle`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ enabled })
            });
            showSaveIndicator();
            await loadData(true);
        }
        
        async function toggleFixed(groupId, webhookId, isFixed) {
            await fetch(`/api/group/${groupId}/webhook/${webhookId}/fixed`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ is_fixed: isFixed })
            });
            showSaveIndicator();
            await loadData(true);
        }
        
        async function renameWebhook(groupId, webhookId, currentName) {
            const newName = prompt('請輸入新名稱:', currentName);
            if (!newName || newName === currentName) return;
            await fetch(`/api/group/${groupId}/webhook/${webhookId}`, {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ name: newName })
            });
            showSaveIndicator();
            await loadData(true);
        }
        
        async function testWebhook(groupId, webhookId) {
            const res = await fetch(`/api/group/${groupId}/webhook/${webhookId}/test`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ content: `[測試] ${new Date().toLocaleTimeString()}` })
            });
            const result = await res.json();
            alert(result.success ? '✅ 測試成功！' : `❌ ${result.message}`);
            await loadData(true);
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
            await loadData(true);
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
# 主程式
# ================================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("  🔄 Webhook 中繼站 v4.3")
    print("  飛書憑證管理版")
    print("=" * 60)
    print(f"  📡 本地訪問: http://localhost:{PORT}")
    print(f"  💾 配置文件: {CONFIG_FILE}")
    print(f"  🕐 時區: UTC{'+' if TIMEZONE_OFFSET >= 0 else ''}{TIMEZONE_OFFSET}")
    print(f"  🔐 密碼保護: {'啟用' if ADMIN_PASSWORD else '停用'}")
    print("=" * 60)
    print()
    print("  🆕 v4.3 新功能:")
    print("    - 📱 Web 介面管理飛書憑證：無需重啟，即時生效")
    print("    - ⏰ Webhook 時段控制：每個 Webhook 可設定自己的通知時段")
    print("    - 📌 固定 Webhook：無論同步/輪詢都會發送")
    print()
    print("  📝 使用說明:")
    print("    - 編輯 PRESET_WEBHOOKS 設定預設配置")
    print("    - 所有變更會自動保存到 JSON 文件")
    print("    - 重啟後自動恢復所有設定")
    print("    - 在 Web 介面更新飛書憑證，無需修改環境變數")
    print("=" * 60)
    
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
