#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
    Webhook 中繼站 v4.5 - 日期時段排程版
================================================================================

核心功能：
    - Web 介面動態更新飛書憑證（無需重啟）
    - 多筆日期時段排程（每個 Webhook 可設定多組「指定日期 + 時段」）
    - 固定 Webhook（無論模式都會發送，仍受排程限制）
    - JSON 文件持久化存儲（自動保存/載入配置）
    - 支援硬編碼預設 Webhook（重啟自動恢復）
    - 兩種發送模式：同步模式 / 輪詢模式
    - Webhook 啟用/禁用開關（無需刪除）
    - 自定義 Webhook 名稱
    - 支援 Discord、飛書、企業微信
    - 純文字 BOSS 偵測訊息過濾

配置優先級：
    1. JSON 文件中的配置（如果存在）
    2. 硬編碼的 PRESET_WEBHOOKS 配置
    3. 環境變數 WEBHOOK_GROUPS

v4.5 更新：
    - 排程系統從「每日固定時段」升級為「多筆日期+時段」排程
    - 例如：A webhook 在 2/23 12:00-22:00 和 2/24 00:00-12:00 開啟
    - 過期排程自動標灰，可一鍵清除
    - 向後相容 v4.4 的 schedule_enabled 格式

v4.4 修正：
    - 修復輪詢模式下 Webhook 不在時段內時通知被吃掉的問題
    - 輪詢模式會自動跳過不在時段內的 Webhook，嘗試下一個
    - UI 美化：移除多餘 emoji、統一配色、更乾淨的介面

作者: @yyv3vnn
版本: 4.5
================================================================================
"""

import json
import os
import threading
import time
import requests
import hashlib
import base64
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
TIMEZONE_OFFSET = int(os.environ.get('TIMEZONE_OFFSET', 8))

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
# 硬編碼預設配置（重啟自動恢復）
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
            {
                "name": "喵z固定記數",
                "url": "https://discordapp.com/api/webhooks/1453064998355210291/xnPVQqIIrwFh3ZUXgC9w7ZTCxlMZ9sFx9w42PWFCE0w0Ku2fh2TjloWUgirsef0GaodU",
                "type": "discord",
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
            {
                "name": "蘑菇固定記數",
                "url": "https://discordapp.com/api/webhooks/1453064913114370048/wTtrO6rTs22Hlpt8avLmFMUu1oNtwYuZsAMT--uyHaS9RlIMn6vmvuPjLPhtK3Hdz3Of",
                "type": "discord",
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
                "name": "小巴固定記數",
                "url": "https://discordapp.com/api/webhooks/1453064747128979656/-VJbdKPoeKhdCLc_hYlHf2HT6ysApOGDn9joiyYwCOzwTxwXkr0DzaDWpUTCZLFhMPKB",
                "type": "discord",
                "enabled": True,
                "is_fixed": False
            },
        ]
    },
    
    # ============ 群組 SS: 書生 ============
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
            {
                "name": "皮蛋老闆書生",
                "url": "https://open.feishu.cn/open-apis/bot/v2/hook/6754e60c-be48-4454-bea8-4d27831218d4",
                "type": "feishu",
                "enabled": True,
                "is_fixed": False
            },
            {
                "name": "牛牛老板書生(24h)",
                "url": "https://open.feishu.cn/open-apis/bot/v2/hook/9082d4d4-e2ff-49bf-9b5b-4f9ae85ba42e",
                "type": "feishu",
                "enabled": True,
                "is_fixed": False
            },
            {
                "name": "路況書生",
                "url": "https://open.feishu.cn/open-apis/bot/v2/hook/75dda4ad-f3fa-4aa8-b69f-a9576171e682",
                "type": "feishu",
                "enabled": True,
                "is_fixed": False
            },
            {
                "name": "rock書生",
                "url": "https://open.feishu.cn/open-apis/bot/v2/hook/cccf7729-bc94-4486-897a-5f35f4320d0b",
                "type": "feishu",
                "enabled": True,
                "is_fixed": False
            },
        ]
    },
}

# ================================================================================
# Flask 應用程式
# ================================================================================

app = Flask(__name__)


# ================================================================================
# 飛書圖片上傳器
# ================================================================================

class FeishuImageUploader:
    """飛書圖片上傳器 - 支援 token 快取與圖片快取"""
    
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
            app_id = self.app_id or FEISHU_APP_ID
            app_secret = self.app_secret or FEISHU_APP_SECRET
            
            if not app_id or not app_secret:
                logger.warning("飛書憑證未設定")
                return None
            
            current_time = time.time()
            if self.token_cache['token'] and current_time < self.token_cache['expire_time'] - 60:
                return self.token_cache['token']
            
            logger.info("開始獲取新的飛書 access_token...")
            url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
            payload = {"app_id": app_id, "app_secret": app_secret}
            
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('code') == 0:
                    token = result.get('tenant_access_token')
                    expire = result.get('expire', 7200)
                    self.token_cache = {
                        'token': token,
                        'expire_time': current_time + expire
                    }
                    logger.info("獲取飛書 access_token 成功")
                    return token
                else:
                    logger.error(f"飛書 API 錯誤: code={result.get('code')}, msg={result.get('msg')}")
            else:
                logger.error(f"飛書 token HTTP 請求失敗: {response.status_code}")
            
            return None
        except Exception as e:
            logger.error(f"獲取 access_token 異常: {e}")
            return None
    
    def upload_image(self, image_data: bytes) -> str:
        """上傳圖片到飛書，回傳 image_key"""
        try:
            if not image_data:
                return None
            
            # 使用 MD5 快取避免重複上傳
            img_hash = hashlib.md5(image_data).hexdigest()
            if img_hash in self.upload_cache:
                logger.info("使用緩存的飛書圖片 key")
                return self.upload_cache[img_hash]
            
            token = self.get_tenant_access_token()
            if not token:
                logger.error("無法獲取 access_token，圖片上傳失敗")
                return None
            
            url = "https://open.feishu.cn/open-apis/im/v1/images"
            headers = {"Authorization": f"Bearer {token}"}
            files = {'image': ('screenshot.png', image_data, 'image/png')}
            data = {'image_type': 'message'}
            
            response = requests.post(url, headers=headers, files=files, data=data, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('code') == 0:
                    image_key = result.get('data', {}).get('image_key')
                    if image_key:
                        self.upload_cache[img_hash] = image_key
                        logger.info(f"飛書圖片上傳成功: {image_key}")
                        return image_key
                else:
                    logger.error(f"飛書圖片上傳 API 錯誤: {result.get('msg')}")
            else:
                logger.error(f"飛書圖片上傳 HTTP 失敗: {response.status_code}")
            
            return None
        except Exception as e:
            logger.error(f"上傳圖片異常: {e}")
            return None


# 全域飛書上傳器
feishu_uploader = FeishuImageUploader()


# ================================================================================
# WebhookItem - v4.5 多筆日期時段排程
# ================================================================================

class WebhookItem:
    """
    單個 Webhook 項目 - 支援多筆日期時段排程
    
    v4.5 排程系統：
        schedule_mode: "off" (不限制) | "date_range" (啟用日期排程)
        schedules: [
            {"date": "2025-02-23", "start_time": "12:00", "end_time": "22:00"},
            {"date": "2025-02-24", "start_time": "00:00", "end_time": "12:00"},
            ...
        ]
    
    向後相容 v4.4：
        舊版的 schedule_enabled / schedule_start / schedule_end
        會自動轉換為一筆以今天日期為基礎的排程
    """
    
    def __init__(self, url: str, name: str = None, webhook_type: str = 'discord',
                 enabled: bool = True, is_fixed: bool = False, webhook_id: str = None,
                 schedule_mode: str = "off", schedules: list = None):
        self.id = webhook_id or hashlib.md5(f"{url}{time.time()}".encode()).hexdigest()[:8]
        self.url = url
        self.name = name or self._generate_default_name(webhook_type)
        self.webhook_type = webhook_type
        self.enabled = enabled
        self.is_fixed = is_fixed
        self.stats = {"sent": 0, "failed": 0}
        self.created_at = get_local_time_str()
        
        # v4.5 多筆日期排程
        self.schedule_mode = schedule_mode  # "off" | "date_range"
        self.schedules = schedules or []    # [{date, start_time, end_time}, ...]
    
    def _generate_default_name(self, webhook_type: str) -> str:
        """產生預設名稱"""
        timestamp = get_local_time_str("%H%M%S")
        type_map = {'discord': 'Discord', 'feishu': '飛書', 'wecom': '企業微信'}
        return f"{type_map.get(webhook_type, 'Webhook')}-{timestamp}"
    
    def is_in_schedule(self) -> bool:
        """
        檢查當前時間是否在排程內
        
        - schedule_mode == "off": 永遠回傳 True（不限制）
        - schedule_mode == "date_range": 檢查今天是否有匹配的排程項，且當前時間在該時段內
        """
        if self.schedule_mode == "off":
            return True
        
        if not self.schedules:
            return False
        
        now = get_local_time()
        today_str = now.strftime("%Y-%m-%d")
        current_time = now.strftime("%H:%M")
        
        for schedule in self.schedules:
            # 只檢查今天的排程
            if schedule.get("date") != today_str:
                continue
            
            start_time = schedule.get("start_time", "00:00")
            end_time = schedule.get("end_time", "23:59")
            
            # 處理跨日情況（例如 22:00 - 02:00）
            if start_time <= end_time:
                if start_time <= current_time <= end_time:
                    return True
            else:
                if current_time >= start_time or current_time <= end_time:
                    return True
        
        return False
    
    def get_schedule_info(self) -> str:
        """
        取得排程摘要資訊（用於 UI 顯示）
        
        回傳格式：
            排程關閉: "" (空字串)
            無排程項: "排程: 無排程項"
            全部過期: "排程: 已全部過期"
            正常: "2/23 12:00-22:00 | 2/24 00:00-12:00" (最多顯示 3 筆)
        """
        if self.schedule_mode == "off":
            return ""
        
        if not self.schedules:
            return "排程: 無排程項"
        
        today_str = get_local_time().strftime("%Y-%m-%d")
        
        # 篩選未過期的排程（今天及以後），按日期+時間排序
        upcoming = sorted(
            [s for s in self.schedules if s.get("date", "") >= today_str],
            key=lambda x: x.get("date", "") + x.get("start_time", "")
        )
        
        if not upcoming:
            return "排程: 已全部過期"
        
        # 最多顯示 3 筆
        parts = []
        for schedule in upcoming[:3]:
            try:
                dt = datetime.strptime(schedule["date"], "%Y-%m-%d")
                parts.append(f"{dt.month}/{dt.day} {schedule['start_time']}-{schedule['end_time']}")
            except (KeyError, ValueError):
                parts.append(f"{schedule.get('date', '?')} {schedule.get('start_time', '')}-{schedule.get('end_time', '')}")
        
        result = " | ".join(parts)
        if len(upcoming) > 3:
            result += f" (+{len(upcoming) - 3})"
        
        return result
    
    def to_dict(self) -> dict:
        """轉換為字典（用於 API 回應 / UI 顯示）"""
        return {
            "id": self.id,
            "name": self.name,
            "url_preview": f"...{self.url[-30:]}" if len(self.url) > 35 else self.url,
            "webhook_type": self.webhook_type,
            "enabled": self.enabled,
            "is_fixed": self.is_fixed,
            "schedule_mode": self.schedule_mode,
            "schedules": self.schedules,
            "schedule_info": self.get_schedule_info(),
            "is_in_schedule": self.is_in_schedule(),
            "sent": self.stats["sent"],
            "failed": self.stats["failed"],
            "created_at": self.created_at
        }
    
    def to_save_dict(self) -> dict:
        """轉換為字典（用於 JSON 持久化保存）"""
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "type": self.webhook_type,
            "enabled": self.enabled,
            "is_fixed": self.is_fixed,
            "schedule_mode": self.schedule_mode,
            "schedules": self.schedules,
            "stats": self.stats,
            "created_at": self.created_at
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'WebhookItem':
        """
        從字典創建 WebhookItem
        
        向後相容 v4.4：
            如果偵測到舊版 schedule_enabled 欄位，
            自動轉換為以今天日期為基礎的 date_range 排程
        """
        schedule_mode = data.get('schedule_mode', 'off')
        schedules = data.get('schedules', [])
        
        # v4.4 向後相容：自動轉換舊格式
        if data.get('schedule_enabled') and not schedules:
            schedule_mode = "date_range"
            schedules = [{
                "date": get_local_time().strftime("%Y-%m-%d"),
                "start_time": data.get('schedule_start', '00:00'),
                "end_time": data.get('schedule_end', '23:59')
            }]
            logger.info(f"v4.4 相容：自動轉換 {data.get('name', '?')} 的排程格式")
        
        item = cls(
            url=data.get('url', ''),
            name=data.get('name'),
            webhook_type=data.get('type', 'discord'),
            enabled=data.get('enabled', True),
            is_fixed=data.get('is_fixed', False),
            webhook_id=data.get('id'),
            schedule_mode=schedule_mode,
            schedules=schedules
        )
        item.stats = data.get('stats', {"sent": 0, "failed": 0})
        item.created_at = data.get('created_at', item.created_at)
        return item


# ================================================================================
# 消息發送器
# ================================================================================

class MessageSender:
    """消息發送器 - 支援 Discord、飛書、企業微信"""
    
    @staticmethod
    def send_to_discord(webhook_url: str, content: str, image_data: bytes = None) -> bool:
        """發送到 Discord Webhook"""
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
            logger.error(f"Discord 發送失敗: {e}")
            return False
    
    @staticmethod
    def send_to_feishu(webhook_url: str, content: str, image_key: str = None) -> bool:
        """發送到飛書 Webhook（富文本格式）"""
        try:
            content_blocks = []
            
            # 文字內容
            if content:
                for line in content.split('\n'):
                    if line.strip():
                        content_blocks.append([{"tag": "text", "text": line + "\n"}])
            
            # 圖片
            if image_key:
                content_blocks.append([{
                    "tag": "img",
                    "image_key": image_key,
                    "width": 800,
                    "height": 600
                }])
            
            # 時間戳
            content_blocks.append([{"tag": "text", "text": f"\n{get_local_time_str()}"}])
            
            payload = {
                "msg_type": "post",
                "content": {
                    "post": {
                        "zh_cn": {
                            "title": "BOSS 通知",
                            "content": content_blocks
                        }
                    }
                }
            }
            
            response = requests.post(
                webhook_url, json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                return result.get('code') == 0 or result.get('StatusCode') == 0
            return False
        except Exception as e:
            logger.error(f"飛書發送失敗: {e}")
            return False
    
    @staticmethod
    def send_to_wecom(webhook_url: str, content: str, image_data: bytes = None) -> bool:
        """發送到企業微信群機器人（支援圖片 Base64）"""
        try:
            # 發送文字（Markdown 格式）
            text_payload = {
                "msgtype": "markdown",
                "markdown": {
                    "content": f"## BOSS 通知\n\n{content}\n\n> {get_local_time_str()}"
                }
            }
            
            response = requests.post(webhook_url, json=text_payload, timeout=10)
            result = response.json()
            
            if result.get('errcode') != 0:
                logger.error(f"企業微信文字發送失敗: {result}")
                return False
            
            # 發送圖片（如果有）
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
                    
                    if img_result.get('errcode') != 0:
                        logger.warning(f"企業微信圖片發送失敗: {img_result.get('errmsg')}")
                except Exception as img_e:
                    logger.warning(f"企業微信圖片發送異常: {img_e}")
            
            return True
        except Exception as e:
            logger.error(f"企業微信發送失敗: {e}")
            return False


# ================================================================================
# BOSS 群組類別
# ================================================================================

class BossGroup:
    """BOSS 群組 - 支援兩種發送模式 + 固定 Webhook + 日期時段排程"""
    
    MODE_SYNC = 'sync'
    MODE_ROUND_ROBIN = 'round_robin'
    
    def __init__(self, group_id: str, display_name: str = None):
        self.group_id = group_id.lower()
        self.display_name = display_name or f"{group_id.upper()} BOSS"
        self.webhooks: list = []
        self.send_mode = self.MODE_SYNC
        self.current_index = 0
        self.lock = threading.Lock()
        self.stats = {"received": 0, "total_sent": 0, "total_failed": 0}
        self.history = deque(maxlen=50)
        self._save_callback = None
    
    def set_save_callback(self, callback):
        """設置保存回調函數"""
        self._save_callback = callback
    
    def _trigger_save(self):
        """觸發保存"""
        if self._save_callback:
            self._save_callback()
    
    # ---- 模式管理 ----
    
    def set_send_mode(self, mode: str) -> tuple:
        """切換發送模式"""
        with self.lock:
            if mode not in [self.MODE_SYNC, self.MODE_ROUND_ROBIN]:
                return False, "無效的模式"
            self.send_mode = mode
            self._trigger_save()
            mode_name = '同步模式' if mode == self.MODE_SYNC else '輪詢模式'
            return True, f"已切換為{mode_name}"
    
    # ---- Webhook CRUD ----
    
    def add_webhook(self, url: str, name: str = None, webhook_type: str = 'discord',
                    is_fixed: bool = False) -> tuple:
        """添加新的 Webhook"""
        with self.lock:
            if not url or not url.startswith("https://"):
                return False, "無效的 URL（必須以 https:// 開頭）"
            
            if any(wh.url == url for wh in self.webhooks):
                return False, "此 Webhook URL 已存在"
            
            if webhook_type not in ['discord', 'feishu', 'wecom']:
                return False, "類型必須是 'discord'、'feishu' 或 'wecom'"
            
            webhook = WebhookItem(url, name, webhook_type, enabled=True, is_fixed=is_fixed)
            self.webhooks.append(webhook)
            
            fixed_text = " (固定)" if is_fixed else ""
            logger.info(f"[{self.group_id}] 添加 {webhook_type} Webhook: {webhook.name}{fixed_text}")
            self._trigger_save()
            return True, f"添加成功: {webhook.name}{fixed_text}"
    
    def remove_webhook(self, webhook_id: str) -> bool:
        """移除 Webhook"""
        with self.lock:
            for i, wh in enumerate(self.webhooks):
                if wh.id == webhook_id:
                    removed = self.webhooks.pop(i)
                    if self.current_index >= len(self.webhooks) and len(self.webhooks) > 0:
                        self.current_index = 0
                    logger.info(f"[{self.group_id}] 移除 Webhook: {removed.name}")
                    self._trigger_save()
                    return True
            return False
    
    def toggle_webhook(self, webhook_id: str, enabled: bool) -> tuple:
        """啟用/禁用 Webhook"""
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
        """更新 Webhook 名稱"""
        with self.lock:
            for wh in self.webhooks:
                if wh.id == webhook_id and name:
                    wh.name = name
                    self._trigger_save()
                    return True, f"已重命名為: {name}"
            return False, "找不到此 Webhook"
    
    # ---- 查詢方法 ----
    
    def get_enabled_webhooks(self, exclude_fixed: bool = False) -> list:
        """獲取啟用的 Webhook（可選擇排除固定的）"""
        webhooks = [wh for wh in self.webhooks if wh.enabled]
        if exclude_fixed:
            webhooks = [wh for wh in webhooks if not wh.is_fixed]
        return webhooks
    
    def get_fixed_webhooks(self) -> list:
        """獲取固定的 Webhook"""
        return [wh for wh in self.webhooks if wh.is_fixed and wh.enabled]
    
    def get_next_webhook_round_robin(self) -> tuple:
        """
        [v4.4 修正] 輪詢模式取下一個 Webhook
        
        修正邏輯：
        - 遍歷所有啟用的非固定 Webhook，跳過不在排程內的
        - 只有成功找到在排程內的 Webhook 才消耗 index
        - 如果全部都不在排程內，返回 (None, skipped_list)
        
        Returns:
            tuple: (選中的 WebhookItem 或 None, 被跳過的 Webhook 列表)
        """
        enabled = self.get_enabled_webhooks(exclude_fixed=True)
        if not enabled:
            return None, []
        
        skipped = []
        total = len(enabled)
        
        # 最多嘗試所有啟用的 webhook
        for _ in range(total):
            self.current_index = self.current_index % total
            candidate = enabled[self.current_index]
            self.current_index = (self.current_index + 1) % total
            
            if candidate.is_in_schedule():
                return candidate, skipped
            else:
                skipped.append(candidate)
                logger.info(f"[{self.group_id}] 輪詢跳過 {candidate.name}（不在排程內）")
        
        # 全部都不在排程內
        return None, skipped
    
    # ---- 消息中繼 ----
    
    def relay_message(self, content: str, image_data: bytes = None, 
                      source_ip: str = "unknown") -> tuple:
        """
        中繼訊息到 Webhook
        
        過濾規則：如果沒有圖片且包含 BOSS 檢測關鍵字，則不發送
        
        Returns:
            tuple: (成功與否, 訊息, 詳細結果列表)
        """
        # 過濾純文字 BOSS 檢測訊息
        if not image_data and content:
            filter_keywords = ["偵測到HP血條", "BOSS存在", "⏰ 時間:", "🩸"]
            
            if any(keyword in content for keyword in filter_keywords):
                logger.info(f"[{self.group_id}] 過濾純文字 BOSS 檢測訊息")
                self.history.appendleft({
                    "time": get_local_time_str(),
                    "content": content[:50],
                    "status": "已過濾（純文字）",
                    "source": source_ip[-15:],
                    "has_image": False,
                    "mode": "過濾"
                })
                return True, "已過濾", []
        
        # 正常發送流程
        self.stats["received"] += 1
        timestamp = get_local_time_str()
        results = []
        
        # 飛書圖片預上傳（如果有啟用的飛書 Webhook 且在排程內）
        feishu_image_key = None
        if image_data:
            has_active_feishu = any(
                wh.enabled and wh.webhook_type == 'feishu' and wh.is_in_schedule()
                for wh in self.webhooks
            )
            if has_active_feishu:
                feishu_image_key = feishu_uploader.upload_image(image_data)
        
        with self.lock:
            # 1. 先發送固定的 Webhook（仍受排程限制）
            fixed_webhooks = self.get_fixed_webhooks()
            for wh in fixed_webhooks:
                if wh.is_in_schedule():
                    success = self._send_to_webhook(wh, content, image_data, feishu_image_key)
                    results.append({
                        "name": wh.name, "type": wh.webhook_type,
                        "success": success, "is_fixed": True, "skipped": False
                    })
                else:
                    logger.info(f"[{self.group_id}] 固定 {wh.name} 不在排程內，已跳過")
                    results.append({
                        "name": wh.name, "type": wh.webhook_type,
                        "success": False, "is_fixed": True, "skipped": True
                    })
            
            # 2. 根據模式發送非固定的 Webhook
            if self.send_mode == self.MODE_SYNC:
                # 同步模式：發送到所有啟用且在排程內的
                enabled_webhooks = self.get_enabled_webhooks(exclude_fixed=True)
                
                if not enabled_webhooks and not fixed_webhooks:
                    self.history.appendleft({
                        "time": timestamp, "content": content[:50],
                        "status": "無啟用的 Webhook", "source": source_ip[-15:],
                        "has_image": bool(image_data), "mode": "同步"
                    })
                    return False, "無啟用的 Webhook", []
                
                for wh in enabled_webhooks:
                    if wh.is_in_schedule():
                        success = self._send_to_webhook(wh, content, image_data, feishu_image_key)
                        results.append({
                            "name": wh.name, "type": wh.webhook_type,
                            "success": success, "is_fixed": False, "skipped": False
                        })
                    else:
                        logger.info(f"[{self.group_id}] {wh.name} 不在排程內，已跳過")
                        results.append({
                            "name": wh.name, "type": wh.webhook_type,
                            "success": False, "is_fixed": False, "skipped": True
                        })
            else:
                # 輪詢模式：自動跳過不在排程內的，嘗試下一個
                webhook, skipped_webhooks = self.get_next_webhook_round_robin()
                
                for skipped_wh in skipped_webhooks:
                    results.append({
                        "name": skipped_wh.name, "type": skipped_wh.webhook_type,
                        "success": False, "is_fixed": False, "skipped": True
                    })
                
                if not webhook and not fixed_webhooks:
                    skip_msg = "所有 Webhook 都不在排程內" if skipped_webhooks else "無啟用的 Webhook"
                    self.history.appendleft({
                        "time": timestamp, "content": content[:50],
                        "status": skip_msg, "source": source_ip[-15:],
                        "has_image": bool(image_data), "mode": "輪詢"
                    })
                    return False, skip_msg, results
                
                if webhook:
                    success = self._send_to_webhook(webhook, content, image_data, feishu_image_key)
                    results.append({
                        "name": webhook.name, "type": webhook.webhook_type,
                        "success": success, "is_fixed": False, "skipped": False
                    })
        
        # 統計結果
        success_count = sum(1 for r in results if r["success"])
        fail_count = sum(1 for r in results if not r["success"] and not r.get("skipped"))
        skipped_count = sum(1 for r in results if r.get("skipped"))
        self.stats["total_sent"] += success_count
        self.stats["total_failed"] += fail_count
        
        # 組裝狀態字串
        status_parts = []
        for r in results:
            mark = '[跳過]' if r.get("skipped") else ('[OK]' if r['success'] else '[失敗]')
            type_label = {'discord': 'DC', 'feishu': '飛書', 'wecom': '微信'}.get(r['type'], '?')
            status_parts.append(f"{mark}{type_label}{r['name'][:8]}")
        
        mode_name = "同步" if self.send_mode == self.MODE_SYNC else "輪詢"
        
        message_parts = [f"成功: {success_count}"]
        if fail_count > 0:
            message_parts.append(f"失敗: {fail_count}")
        if skipped_count > 0:
            message_parts.append(f"排程外: {skipped_count}")
        
        self.history.appendleft({
            "time": timestamp,
            "content": (content[:50] + "...") if len(content) > 50 else content,
            "status": " | ".join(status_parts),
            "source": source_ip[-15:],
            "has_image": bool(image_data),
            "mode": mode_name
        })
        
        return success_count > 0, f"[{mode_name}] {', '.join(message_parts)}", results
    
    def _send_to_webhook(self, webhook: WebhookItem, content: str,
                         image_data: bytes, feishu_image_key: str) -> bool:
        """發送訊息到指定 Webhook"""
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
                logger.info(f"[{self.group_id}] OK -> {webhook.name}")
            else:
                webhook.stats["failed"] += 1
                logger.error(f"[{self.group_id}] FAIL -> {webhook.name}")
            
            return success
        except Exception as e:
            webhook.stats["failed"] += 1
            logger.error(f"[{self.group_id}] ERROR -> {webhook.name}: {e}")
            return False
    
    # ---- 序列化 ----
    
    def get_stats(self) -> dict:
        """獲取群組統計資訊"""
        return {
            "group_id": self.group_id,
            "display_name": self.display_name,
            "send_mode": self.send_mode,
            "send_mode_name": "同步模式" if self.send_mode == self.MODE_SYNC else "輪詢模式",
            "webhooks_total": len(self.webhooks),
            "webhooks_enabled": len(self.get_enabled_webhooks()),
            "webhooks_fixed": len(self.get_fixed_webhooks()),
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
        
        self.feishu_app_id = FEISHU_APP_ID
        self.feishu_app_secret = FEISHU_APP_SECRET
        
        self._load_config()
        atexit.register(self._save_config_sync)
        
        logger.info("=" * 60)
        logger.info("Webhook 中繼站 v4.5 啟動")
        logger.info(f"已配置 {len(self.groups)} 個 BOSS 群組")
        logger.info(f"配置文件: {CONFIG_FILE}")
        logger.info(f"時區: UTC{'+' if TIMEZONE_OFFSET >= 0 else ''}{TIMEZONE_OFFSET}")
        logger.info(f"密碼保護: {'啟用' if ADMIN_PASSWORD else '停用'}")
        if self.feishu_app_id:
            logger.info(f"飛書 APP ID: {self.feishu_app_id[:10]}...")
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
                    global FEISHU_APP_ID, FEISHU_APP_SECRET
                    self.feishu_app_id = config['feishu_credentials'].get('app_id', FEISHU_APP_ID)
                    self.feishu_app_secret = config['feishu_credentials'].get('app_secret', FEISHU_APP_SECRET)
                    FEISHU_APP_ID = self.feishu_app_id
                    FEISHU_APP_SECRET = self.feishu_app_secret
                    feishu_uploader.set_credentials(self.feishu_app_id, self.feishu_app_secret)
                    logger.info(f"從 JSON 載入飛書憑證: {self.feishu_app_id[:10]}...")
                
                # 載入群組
                for group_id, group_data in config.get('groups', {}).items():
                    group = BossGroup.from_dict(group_id, group_data)
                    group.set_save_callback(self._schedule_save)
                    self.groups[group_id] = group
                
                logger.info(f"從 JSON 文件載入 {len(self.groups)} 個群組")
                loaded = True
            except Exception as e:
                logger.error(f"載入 JSON 配置失敗: {e}")
        
        # 2. 如果 JSON 載入失敗，使用硬編碼配置
        if not loaded:
            logger.info("使用硬編碼預設配置...")
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
                logger.info(f"  {group_id} -> {preset.get('display_name')} ({len(group.webhooks)} webhooks)")
            
            self._save_config_sync()
    
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
                "version": "4.5",
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
            
            # 使用臨時文件 + 原子替換，避免寫入中斷導致資料損壞
            temp_file = CONFIG_FILE + ".tmp"
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            os.replace(temp_file, CONFIG_FILE)
            
            logger.info(f"配置已保存到 {CONFIG_FILE}")
        except Exception as e:
            logger.error(f"保存配置失敗: {e}")
    
    # ---- 飛書憑證管理 ----
    
    def update_feishu_credentials(self, app_id: str, app_secret: str) -> tuple:
        """更新飛書應用憑證"""
        if not app_id or not app_secret:
            return False, "APP ID 和 APP Secret 不能為空"
        
        global FEISHU_APP_ID, FEISHU_APP_SECRET
        
        with self.lock:
            self.feishu_app_id = app_id.strip()
            self.feishu_app_secret = app_secret.strip()
        
        FEISHU_APP_ID = self.feishu_app_id
        FEISHU_APP_SECRET = self.feishu_app_secret
        feishu_uploader.set_credentials(self.feishu_app_id, self.feishu_app_secret)
        feishu_uploader.token_cache = {'token': None, 'expire_time': 0}
        
        self._schedule_save()
        logger.info(f"飛書憑證已更新: {app_id[:10]}...")
        return True, "飛書憑證已更新並保存"
    
    def get_feishu_credentials(self) -> dict:
        """獲取飛書憑證（部分遮蔽）"""
        return {
            "app_id": self.feishu_app_id,
            "app_id_masked": f"{self.feishu_app_id[:10]}..." if self.feishu_app_id and len(self.feishu_app_id) > 10 else self.feishu_app_id,
            "app_secret": self.feishu_app_secret,
            "app_secret_masked": f"{self.feishu_app_secret[:8]}..." if self.feishu_app_secret and len(self.feishu_app_secret) > 8 else "***",
            "is_configured": bool(self.feishu_app_id and self.feishu_app_secret)
        }
    
    # ---- 群組管理 ----
    
    def create_group(self, group_id: str, display_name: str = None) -> 'BossGroup':
        """建立新群組"""
        with self.lock:
            clean_id = re.sub(r'[^a-zA-Z0-9_]', '', group_id.lower()) or "default"
            
            if clean_id not in self.groups:
                group = BossGroup(clean_id, display_name)
                group.set_save_callback(self._schedule_save)
                self.groups[clean_id] = group
                logger.info(f"建立群組: {clean_id}")
                self._schedule_save()
            
            return self.groups[clean_id]
    
    def get_group(self, group_id: str):
        """獲取群組"""
        return self.groups.get(group_id.lower())
    
    def get_or_create_group(self, group_id: str):
        """獲取或自動建立群組"""
        return self.get_group(group_id) or self.create_group(group_id)
    
    def delete_group(self, group_id: str) -> bool:
        """刪除群組"""
        with self.lock:
            if group_id.lower() in self.groups:
                del self.groups[group_id.lower()]
                logger.info(f"刪除群組: {group_id}")
                self._schedule_save()
                return True
            return False
    
    def get_all_stats(self) -> dict:
        """獲取所有統計資訊"""
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
    """驗證密碼"""
    return password == ADMIN_PASSWORD


def authenticate():
    """回傳 401 認證要求"""
    return Response(
        '需要密碼才能訪問管理介面\n', 401,
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
# API 路由
# ================================================================================

@app.route('/')
@requires_auth
def index():
    """管理介面首頁"""
    return render_template_string(HTML_TEMPLATE)


@app.route('/test-feishu')
def test_feishu():
    results = {}
    try:
        r = requests.post(
            'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
            json={'app_id': FEISHU_APP_ID, 'app_secret': FEISHU_APP_SECRET},
            timeout=8
        )
        results['feishu_cn'] = {"status": r.status_code, "body": r.json()}
    except Exception as e:
        results['feishu_cn'] = f"FAIL - {str(e)}"
    
    try:
        r = requests.post(
            'https://open.larksuite.com/open-apis/auth/v3/tenant_access_token/internal',
            json={'app_id': FEISHU_APP_ID, 'app_secret': FEISHU_APP_SECRET},
            timeout=8
        )
        results['lark_com'] = {"status": r.status_code, "body": r.json()}
    except Exception as e:
        results['lark_com'] = f"FAIL - {str(e)}"
    
    return jsonify(results)
    
@app.route('/webhook/<group_id>', methods=['POST'])
def receive_webhook(group_id):
    """接收外部 Webhook 並中繼轉發"""
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
            
            # 處理附件（支援本地路徑和 URL）
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
                        except Exception:
                            pass
        else:
            content = request.form.get('content', '')
            if 'file' in request.files:
                image_data = request.files['file'].read()
        
        if not content and not image_data:
            return jsonify({"success": False, "message": "無內容"}), 400
        
        logger.info(f"[{group_id}] 收到: {content[:50]}...")
        success, message, details = group.relay_message(content, image_data, source_ip)
        
        return jsonify({
            "success": success,
            "message": message,
            "group_id": group_id,
            "mode": group.send_mode,
            "details": details
        })
    except Exception as e:
        logger.error(f"[{group_id}] 錯誤: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/webhook', methods=['POST'])
def receive_webhook_default():
    """預設群組 Webhook 端點"""
    return receive_webhook('default')


@app.route('/api/stats')
@requires_auth
def get_stats():
    """獲取所有統計資訊"""
    return jsonify(manager.get_all_stats())


@app.route('/api/feishu/credentials', methods=['GET'])
@requires_auth
def get_feishu_credentials():
    """獲取飛書憑證"""
    return jsonify(manager.get_feishu_credentials())


@app.route('/api/feishu/credentials', methods=['POST'])
@requires_auth
def update_feishu_credentials():
    """更新飛書憑證"""
    data = request.get_json()
    success, message = manager.update_feishu_credentials(
        data.get('app_id', '').strip(),
        data.get('app_secret', '').strip()
    )
    return jsonify({"success": success, "message": message})


@app.route('/api/group', methods=['POST'])
@requires_auth
def create_group():
    """建立新群組"""
    data = request.get_json()
    group_id = data.get('group_id', '').strip()
    if not group_id:
        return jsonify({"success": False, "message": "請提供群組 ID"})
    if manager.get_group(group_id):
        return jsonify({"success": False, "message": "此群組 ID 已存在"})
    manager.create_group(group_id, data.get('display_name'))
    return jsonify({"success": True, "message": "建立成功"})


@app.route('/api/group/<group_id>', methods=['DELETE'])
@requires_auth
def delete_group(group_id):
    """刪除群組"""
    return jsonify({"success": manager.delete_group(group_id)})


@app.route('/api/group/<group_id>/mode', methods=['POST'])
@requires_auth
def set_group_mode(group_id):
    """切換群組發送模式"""
    group = manager.get_group(group_id)
    if not group:
        return jsonify({"success": False, "message": "群組不存在"})
    success, message = group.set_send_mode(request.get_json().get('mode', ''))
    return jsonify({"success": success, "message": message})


@app.route('/api/group/<group_id>/webhook', methods=['POST'])
@requires_auth
def add_webhook_to_group(group_id):
    """添加 Webhook 到群組"""
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
    """從群組移除 Webhook"""
    group = manager.get_group(group_id)
    if not group:
        return jsonify({"success": False, "message": "群組不存在"})
    return jsonify({"success": group.remove_webhook(webhook_id)})


@app.route('/api/group/<group_id>/webhook/<webhook_id>', methods=['PATCH'])
@requires_auth
def update_webhook(group_id, webhook_id):
    """更新 Webhook 名稱"""
    group = manager.get_group(group_id)
    if not group:
        return jsonify({"success": False, "message": "群組不存在"})
    success, message = group.update_webhook(webhook_id, request.get_json().get('name'))
    return jsonify({"success": success, "message": message})


@app.route('/api/group/<group_id>/webhook/<webhook_id>/toggle', methods=['POST'])
@requires_auth
def toggle_webhook(group_id, webhook_id):
    """啟用/禁用 Webhook"""
    group = manager.get_group(group_id)
    if not group:
        return jsonify({"success": False, "message": "群組不存在"})
    success, message = group.toggle_webhook(webhook_id, request.get_json().get('enabled', True))
    return jsonify({"success": success, "message": message})


@app.route('/api/group/<group_id>/webhook/<webhook_id>/fixed', methods=['POST'])
@requires_auth
def toggle_webhook_fixed(group_id, webhook_id):
    """切換 Webhook 固定狀態"""
    group = manager.get_group(group_id)
    if not group:
        return jsonify({"success": False, "message": "群組不存在"})
    success, message = group.toggle_webhook_fixed(webhook_id, request.get_json().get('is_fixed', False))
    return jsonify({"success": success, "message": message})


@app.route('/api/group/<group_id>/webhook/<webhook_id>/schedule', methods=['POST'])
@requires_auth
def set_webhook_schedule(group_id, webhook_id):
    """
    設定 Webhook 的日期時段排程 (v4.5)
    
    請求格式：
    {
        "schedule_mode": "date_range" | "off",
        "schedules": [
            {"date": "2025-02-23", "start_time": "12:00", "end_time": "22:00"},
            {"date": "2025-02-24", "start_time": "00:00", "end_time": "12:00"}
        ]
    }
    """
    group = manager.get_group(group_id)
    if not group:
        return jsonify({"success": False, "message": "群組不存在"})
    
    webhook = next((wh for wh in group.webhooks if wh.id == webhook_id), None)
    if not webhook:
        return jsonify({"success": False, "message": "找不到此 Webhook"})
    
    data = request.get_json()
    webhook.schedule_mode = data.get('schedule_mode', 'off')
    
    # 驗證並儲存排程列表
    if 'schedules' in data:
        valid_schedules = []
        for s in data['schedules']:
            if s.get('date') and s.get('start_time') and s.get('end_time'):
                valid_schedules.append({
                    "date": s["date"],
                    "start_time": s["start_time"],
                    "end_time": s["end_time"]
                })
        webhook.schedules = valid_schedules
    
    manager.force_save()
    
    schedule_count = len(webhook.schedules)
    if webhook.schedule_mode != 'off':
        message = f"{webhook.name} 排程已更新 ({schedule_count} 筆)"
    else:
        message = f"{webhook.name} 排程已關閉"
    
    return jsonify({"success": True, "message": message})


@app.route('/api/group/<group_id>/webhook/<webhook_id>/test', methods=['POST'])
@requires_auth
def test_single_webhook(group_id, webhook_id):
    """測試單個 Webhook"""
    group = manager.get_group(group_id)
    if not group:
        return jsonify({"success": False, "message": "群組不存在"})
    
    webhook = next((wh for wh in group.webhooks if wh.id == webhook_id), None)
    if not webhook:
        return jsonify({"success": False, "message": "找不到此 Webhook"})
    
    data = request.get_json()
    content = data.get('content', f'[測試] {webhook.name}')
    
    # 根據類型呼叫對應的發送方法
    sender_map = {
        'discord': MessageSender.send_to_discord,
        'feishu': MessageSender.send_to_feishu,
        'wecom': MessageSender.send_to_wecom
    }
    sender = sender_map.get(webhook.webhook_type)
    success = sender(webhook.url, content) if sender else False
    
    if success:
        webhook.stats["sent"] += 1
    else:
        webhook.stats["failed"] += 1
    
    return jsonify({"success": success, "message": "發送成功" if success else "發送失敗"})


@app.route('/api/save', methods=['POST'])
@requires_auth
def force_save():
    """強制保存配置"""
    manager.force_save()
    return jsonify({"success": True, "message": "已保存"})


@app.route('/health')
def health():
    """健康檢查端點"""
    return jsonify({
        "status": "ok",
        "version": "4.5",
        "groups": len(manager.groups),
        "config_file": CONFIG_FILE
    })


# ================================================================================
# HTML 模板 - v4.5 日期時段排程版
# ================================================================================

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Webhook 中繼站 v4.5</title>
    <style>
        :root {
            --bg-primary: #0e1117;
            --bg-secondary: #161b22;
            --bg-tertiary: #1c2129;
            --bg-card: rgba(22, 27, 34, 0.8);
            --border: rgba(48, 54, 61, 0.8);
            --border-light: rgba(48, 54, 61, 0.4);
            --text-primary: #e6edf3;
            --text-secondary: #8b949e;
            --text-muted: #6e7681;
            --accent: #58a6ff;
            --success: #3fb950;
            --danger: #f85149;
            --warning: #d29922;
            --purple: #bc8cff;
            --pink: #f778ba;
        }
        
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft JhengHei', sans-serif;
            background: var(--bg-primary);
            min-height: 100vh;
            color: var(--text-primary);
            padding: 16px;
            line-height: 1.5;
        }
        
        .container { max-width: 1000px; margin: 0 auto; }
        h1 { text-align: center; margin-bottom: 4px; font-size: 1.5em; font-weight: 600; }
        .subtitle { text-align: center; color: var(--text-secondary); margin-bottom: 6px; font-size: 0.82em; }
        .config-info { text-align: center; font-size: 0.75em; color: var(--text-muted); margin-bottom: 20px; }
        
        .card { background: var(--bg-card); border-radius: 8px; padding: 16px; margin-bottom: 12px; border: 1px solid var(--border); }
        .card h2 { color: var(--text-primary); margin-bottom: 12px; font-size: 0.95em; font-weight: 600; }
        
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(90px, 1fr)); gap: 8px; }
        .stat-box { background: var(--bg-tertiary); border-radius: 6px; padding: 10px 8px; text-align: center; border: 1px solid var(--border-light); }
        .stat-box .value { font-size: 1.4em; font-weight: 700; color: var(--accent); }
        .stat-box .label { font-size: 0.7em; color: var(--text-muted); margin-top: 2px; }
        
        .group-card { background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 10px; overflow: hidden; }
        .group-header { background: var(--bg-tertiary); padding: 10px 14px; display: flex; justify-content: space-between; align-items: center; cursor: pointer; flex-wrap: wrap; gap: 8px; transition: background 0.15s; }
        .group-header:hover { background: rgba(56, 62, 71, 0.6); }
        .group-title { font-weight: 600; font-size: 0.95em; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
        .group-title .id { font-family: monospace; background: rgba(110, 118, 129, 0.2); padding: 1px 7px; border-radius: 4px; font-size: 0.82em; color: var(--text-secondary); }
        .group-stats-mini { display: flex; gap: 10px; font-size: 0.78em; color: var(--text-secondary); flex-wrap: wrap; }
        .group-body { padding: 14px; display: none; border-top: 1px solid var(--border-light); }
        .group-body.open { display: block; }
        
        .mode-selector { display: flex; gap: 8px; margin: 8px 0; flex-wrap: wrap; }
        .mode-btn { padding: 6px 14px; border-radius: 6px; border: 1px solid var(--border); background: transparent; color: var(--text-secondary); cursor: pointer; font-size: 0.82em; transition: all 0.15s; }
        .mode-btn:hover { border-color: var(--accent); color: var(--accent); }
        .mode-btn.active { background: rgba(88, 166, 255, 0.15); border-color: var(--accent); color: var(--accent); }
        .mode-btn.active-rr { background: rgba(188, 140, 255, 0.15); border-color: var(--purple); color: var(--purple); }
        
        .mode-info { background: rgba(88, 166, 255, 0.08); border: 1px solid rgba(88, 166, 255, 0.2); border-radius: 6px; padding: 8px 10px; font-size: 0.78em; margin: 8px 0; color: var(--text-secondary); }
        .mode-info.round_robin { background: rgba(188, 140, 255, 0.08); border-color: rgba(188, 140, 255, 0.2); }
        
        .endpoint-box { background: var(--bg-tertiary); border: 1px solid var(--border-light); border-radius: 6px; padding: 8px 10px; font-family: monospace; font-size: 0.8em; margin: 8px 0; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 6px; color: var(--success); }
        
        .webhook-item { background: var(--bg-tertiary); border-radius: 6px; padding: 10px 12px; margin-bottom: 6px; border: 1px solid var(--border-light); transition: all 0.15s; }
        .webhook-item.disabled { opacity: 0.45; }
        .webhook-item.next { border-left: 3px solid var(--success); }
        .webhook-item.fixed { border-left: 3px solid var(--purple); }
        .webhook-item.schedule-off { border-left: 3px solid var(--warning); }
        
        .webhook-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; flex-wrap: wrap; gap: 6px; }
        .webhook-name { font-weight: 600; font-size: 0.88em; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
        .webhook-url { font-family: monospace; font-size: 0.72em; color: var(--text-muted); word-break: break-all; margin-top: 3px; }
        .webhook-stats { font-size: 0.72em; color: var(--text-muted); margin-top: 3px; }
        .webhook-controls { display: flex; gap: 4px; align-items: center; flex-wrap: wrap; }
        
        .toggle-switch { position: relative; width: 40px; height: 22px; flex-shrink: 0; }
        .toggle-switch input { opacity: 0; width: 0; height: 0; }
        .toggle-slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background: rgba(110, 118, 129, 0.4); transition: 0.2s; border-radius: 22px; }
        .toggle-slider:before { position: absolute; content: ""; height: 16px; width: 16px; left: 3px; bottom: 3px; background: white; transition: 0.2s; border-radius: 50%; }
        .toggle-switch input:checked + .toggle-slider { background: var(--success); }
        .toggle-switch input:checked + .toggle-slider:before { transform: translateX(18px); }
        
        .btn { background: var(--accent); border: none; color: #fff; padding: 5px 10px; border-radius: 5px; cursor: pointer; font-size: 0.78em; transition: all 0.15s; font-weight: 500; white-space: nowrap; }
        .btn:hover { opacity: 0.85; }
        .btn-danger { background: var(--danger); }
        .btn-success { background: var(--success); }
        .btn-purple { background: var(--purple); }
        .btn-warning { background: var(--warning); color: #000; }
        .btn-outline { background: transparent; border: 1px solid var(--border); color: var(--text-secondary); }
        .btn-outline:hover { border-color: var(--accent); color: var(--accent); }
        .btn-sm { padding: 3px 7px; font-size: 0.75em; }
        
        input[type="text"], input[type="password"], input[type="time"], input[type="date"], select { padding: 6px 10px; border: 1px solid var(--border); border-radius: 5px; background: var(--bg-primary); color: var(--text-primary); font-size: 0.82em; }
        input::placeholder { color: var(--text-muted); }
        input:focus, select:focus { outline: none; border-color: var(--accent); }
        select option { background: var(--bg-secondary); }
        
        .flex-row { display: flex; gap: 6px; margin-bottom: 8px; flex-wrap: wrap; align-items: center; }
        .flex-row input { flex: 1; min-width: 140px; }
        
        .add-form { background: var(--bg-primary); border: 1px solid var(--border-light); border-radius: 6px; padding: 10px; margin: 8px 0; }
        .add-form .title { font-size: 0.82em; color: var(--text-secondary); margin-bottom: 8px; font-weight: 500; }
        
        .history-item { background: var(--bg-primary); border-radius: 4px; padding: 6px 8px; margin-bottom: 3px; font-size: 0.75em; border: 1px solid var(--border-light); }
        .history-item .time { color: var(--accent); font-family: monospace; font-size: 0.92em; }
        .history-item .mode-tag { background: rgba(110, 118, 129, 0.2); padding: 1px 5px; border-radius: 3px; font-size: 0.85em; }
        
        .badge { display: inline-block; padding: 1px 6px; border-radius: 10px; font-size: 0.68em; font-weight: 600; }
        .badge-discord { background: rgba(88, 101, 242, 0.2); color: #8b9bff; }
        .badge-feishu { background: rgba(88, 166, 255, 0.15); color: var(--accent); }
        .badge-wecom { background: rgba(7, 193, 96, 0.15); color: #3fb950; }
        .badge-next { background: rgba(63, 185, 80, 0.15); color: var(--success); }
        .badge-fixed { background: rgba(188, 140, 255, 0.15); color: var(--purple); }
        .badge-img { background: rgba(247, 120, 186, 0.15); color: var(--pink); }
        .badge-sync { background: rgba(88, 166, 255, 0.15); color: var(--accent); }
        .badge-rr { background: rgba(188, 140, 255, 0.15); color: var(--purple); }
        .badge-schedule { background: rgba(210, 153, 34, 0.15); color: var(--warning); }
        .badge-schedule-on { background: rgba(63, 185, 80, 0.15); color: var(--success); }
        
        .copy-btn { background: transparent; border: 1px solid var(--border); color: var(--text-secondary); padding: 2px 8px; border-radius: 4px; cursor: pointer; font-size: 0.75em; }
        .copy-btn:hover { border-color: var(--accent); color: var(--accent); }
        
        .section-title { font-size: 0.82em; color: var(--text-secondary); margin: 12px 0 8px; padding-bottom: 4px; border-bottom: 1px solid var(--border-light); font-weight: 500; }
        .no-data { color: var(--text-muted); font-size: 0.78em; padding: 12px; text-align: center; background: var(--bg-primary); border-radius: 6px; border: 1px dashed var(--border-light); }
        .save-indicator { position: fixed; bottom: 20px; right: 20px; background: var(--success); color: #000; padding: 8px 16px; border-radius: 6px; font-weight: 600; font-size: 0.85em; display: none; z-index: 1000; }
        .feishu-ok { color: var(--success); }
        .feishu-err { color: var(--danger); }
        
        /* v4.5 排程面板 */
        .schedule-panel { background: var(--bg-primary); border: 1px solid var(--border-light); border-radius: 6px; padding: 10px; margin-top: 8px; font-size: 0.82em; }
        .schedule-panel.active { border-color: rgba(63, 185, 80, 0.3); }
        .schedule-row { display: flex; gap: 6px; align-items: center; padding: 4px 0; flex-wrap: wrap; border-bottom: 1px solid var(--border-light); }
        .schedule-row:last-child { border-bottom: none; }
        .schedule-row .date { color: var(--accent); font-family: monospace; min-width: 70px; }
        .schedule-row .time { color: var(--text-secondary); font-family: monospace; }
        .schedule-row.expired { opacity: 0.4; }
        .schedule-row.today { background: rgba(63, 185, 80, 0.05); border-radius: 4px; padding: 4px 6px; }
        .schedule-add-row { display: flex; gap: 6px; align-items: center; margin-top: 6px; flex-wrap: wrap; }
        
        @media (max-width: 600px) {
            .stats-grid { grid-template-columns: repeat(2, 1fr); }
            .group-header, .webhook-header { flex-direction: column; align-items: flex-start; }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Webhook 中繼站</h1>
        <p class="subtitle">v4.5 | 運行: <span id="uptime">-</span></p>
        <p class="config-info">配置: <span id="configFile">-</span> · 時區: <span id="timezone">-</span> · <span id="currentTime">-</span></p>
        
        <div class="card">
            <h2>總覽統計</h2>
            <div class="stats-grid">
                <div class="stat-box"><div class="value" id="totalGroups">0</div><div class="label">群組</div></div>
                <div class="stat-box"><div class="value" id="totalReceived">0</div><div class="label">接收</div></div>
                <div class="stat-box"><div class="value" id="totalSent">0</div><div class="label">成功</div></div>
                <div class="stat-box"><div class="value" id="totalFailed">0</div><div class="label">失敗</div></div>
                <div class="stat-box"><div class="value" id="successRate">0%</div><div class="label">成功率</div></div>
            </div>
        </div>
        
        <div class="card">
            <h2>飛書應用憑證</h2>
            <div style="font-size:0.8em;margin-bottom:8px;color:var(--text-secondary)">修改後即時生效。狀態: <span id="feishuStatus">載入中...</span></div>
            <div class="flex-row">
                <input type="text" id="feishuAppId" placeholder="APP ID" style="flex:1;min-width:180px">
                <input type="password" id="feishuAppSecret" placeholder="APP Secret" style="flex:1;min-width:180px">
                <button class="btn btn-success" onclick="updateFeishuCredentials()">保存</button>
                <button class="btn btn-outline btn-sm" onclick="document.getElementById('feishuAppSecret').type=document.getElementById('feishuAppSecret').type==='password'?'text':'password'">顯示</button>
            </div>
        </div>
        
        <div class="card">
            <h2>建立新群組</h2>
            <div class="flex-row">
                <input type="text" id="newGroupId" placeholder="群組 ID" style="max-width:140px">
                <input type="text" id="newGroupName" placeholder="顯示名稱">
                <button class="btn btn-success" onclick="createGroup()">建立</button>
            </div>
        </div>
        
        <div class="card">
            <h2>BOSS 群組管理</h2>
            <div id="groupList"></div>
        </div>
        
        <div class="card">
            <h2>使用說明</h2>
            <div style="font-size:0.82em;line-height:1.7;color:var(--text-secondary)">
                <p><strong style="color:var(--text-primary)">v4.5 - 日期時段排程：</strong></p>
                <ul style="margin-left:18px;margin-bottom:8px">
                    <li>每個 Webhook 可設定多筆「指定日期 + 時段」排程</li>
                    <li>例如：A 在 2/23 12:00-22:00 和 2/24 00:00-12:00 開啟通知</li>
                    <li>不在排程內的通知自動跳過，過期排程標灰可手動清除</li>
                </ul>
                <p><strong style="color:var(--text-primary)">發送模式：</strong></p>
                <ul style="margin-left:18px">
                    <li><span class="badge badge-sync">同步</span> 同時發送到所有在排程內的 Webhook</li>
                    <li><span class="badge badge-rr">輪詢</span> 輪流發送，跳過排程外的</li>
                    <li><span class="badge badge-fixed">固定</span> 任何模式都會發送（仍受排程限制）</li>
                </ul>
            </div>
        </div>
    </div>
    <div class="save-indicator" id="saveIndicator">已保存</div>

    <script>
        const baseUrl = window.location.origin;
        let openGroups = new Set();
        let openSchedulePanels = new Set();
        let inputStates = {};
        let isUserInteracting = false;
        let lastInteractionTime = 0;
        
        document.addEventListener('DOMContentLoaded', function() {
            document.body.addEventListener('mousedown', () => { isUserInteracting = true; lastInteractionTime = Date.now(); });
            document.body.addEventListener('keydown', () => { isUserInteracting = true; lastInteractionTime = Date.now(); });
            document.body.addEventListener('focus', (e) => {
                if (e.target.matches('input, select, textarea')) { isUserInteracting = true; lastInteractionTime = Date.now(); }
            }, true);
            setInterval(() => { if (Date.now() - lastInteractionTime > 5000) isUserInteracting = false; }, 500);
            loadFeishuCredentials();
        });
        
        function showSave() {
            const el = document.getElementById('saveIndicator');
            el.style.display = 'block';
            setTimeout(() => el.style.display = 'none', 2000);
        }
        
        function saveInputStates() {
            inputStates = {};
            ['newGroupId', 'newGroupName'].forEach(id => {
                const el = document.getElementById(id);
                if (el) inputStates[id] = el.value;
            });
            document.querySelectorAll('[id^="wn-"], [id^="wu-"], [id^="sd-"], [id^="ss-"], [id^="se-"]').forEach(el => { inputStates[el.id] = el.value; });
            document.querySelectorAll('[id^="wt-"]').forEach(el => { inputStates[el.id] = el.value; });
            document.querySelectorAll('[id^="wf-"]').forEach(el => { inputStates[el.id] = el.checked; });
        }
        
        function restoreInputStates() {
            for (const [id, val] of Object.entries(inputStates)) {
                const el = document.getElementById(id);
                if (el) { el.type === 'checkbox' ? el.checked = val : el.value = val; }
            }
        }
        
        function savePanelStates() {
            openSchedulePanels.clear();
            document.querySelectorAll('[id^="sp-"]').forEach(box => {
                if (box.style.display !== 'none') openSchedulePanels.add(box.id.replace('sp-', ''));
            });
        }
        
        function restorePanelStates() {
            openSchedulePanels.forEach(id => {
                const box = document.getElementById('sp-' + id);
                if (box) box.style.display = 'block';
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
        
        async function loadData(forceRender = false) {
            try {
                const res = await fetch('/api/stats');
                const data = await res.json();
                if (isUserInteracting && !forceRender) { updateStatsOnly(data); return; }
                saveInputStates();
                savePanelStates();
                updateStatsOnly(data);
                renderGroups(data.groups);
                restoreInputStates();
                restorePanelStates();
            } catch (e) { console.error(e); }
        }
        
        async function loadFeishuCredentials() {
            try {
                const res = await fetch('/api/feishu/credentials');
                const data = await res.json();
                document.getElementById('feishuAppId').value = data.app_id || '';
                document.getElementById('feishuAppSecret').value = data.app_secret || '';
                document.getElementById('feishuStatus').innerHTML = data.is_configured
                    ? '<span class="feishu-ok">已配置 (' + data.app_id_masked + ')</span>'
                    : '<span class="feishu-err">未配置</span>';
            } catch (e) {}
        }
        
        async function updateFeishuCredentials() {
            const appId = document.getElementById('feishuAppId').value.trim();
            const appSecret = document.getElementById('feishuAppSecret').value.trim();
            if (!appId || !appSecret) return alert('請填寫完整');
            const res = await (await fetch('/api/feishu/credentials', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ app_id: appId, app_secret: appSecret })
            })).json();
            if (res.success) { showSave(); await loadFeishuCredentials(); alert(res.message); }
            else alert(res.message);
        }
        
        function getTodayStr() {
            const n = new Date();
            return n.getFullYear() + '-' + String(n.getMonth()+1).padStart(2,'0') + '-' + String(n.getDate()).padStart(2,'0');
        }
        
        function formatDateShort(d) {
            try { const dt = new Date(d + 'T00:00:00'); return (dt.getMonth()+1) + '/' + dt.getDate(); }
            catch(e) { return d; }
        }
        
        function isNextWebhook(group, webhookId) {
            const enabled = group.webhooks.filter(w => w.enabled && !w.is_fixed);
            if (!enabled.length) return false;
            const idx = group.current_index % enabled.length;
            return enabled[idx] && enabled[idx].id === webhookId;
        }
        
        function toggleGroup(groupId) {
            if (openGroups.has(groupId)) openGroups.delete(groupId);
            else openGroups.add(groupId);
            document.getElementById('group-' + groupId)?.classList.toggle('open');
        }
        
        function toggleSchedulePanel(webhookId) {
            const box = document.getElementById('sp-' + webhookId);
            if (box.style.display === 'none') { box.style.display = 'block'; openSchedulePanels.add(webhookId); }
            else { box.style.display = 'none'; openSchedulePanels.delete(webhookId); }
        }
        
        function copyText(text) { navigator.clipboard.writeText(text); alert('已複製'); }

        // ====== 渲染群組列表 ======
        function renderGroups(groups) {
            const container = document.getElementById('groupList');
            if (!groups || !groups.length) {
                container.innerHTML = '<div class="no-data">尚未建立任何群組</div>';
                return;
            }
            const today = getTodayStr();
            
            container.innerHTML = groups.map(g => `
                <div class="group-card">
                    <div class="group-header" onclick="toggleGroup('${g.group_id}')">
                        <div class="group-title">
                            <span>${g.display_name}</span>
                            <span class="id">${g.group_id}</span>
                            <span class="badge ${g.send_mode === 'sync' ? 'badge-sync' : 'badge-rr'}">${g.send_mode_name}</span>
                            ${g.webhooks_fixed > 0 ? '<span class="badge badge-fixed">固定 ' + g.webhooks_fixed + '</span>' : ''}
                        </div>
                        <div class="group-stats-mini">
                            <span>接收 ${g.received}</span>
                            <span>成功 ${g.total_sent}</span>
                            <span>失敗 ${g.total_failed}</span>
                            <span>啟用 ${g.webhooks_enabled}/${g.webhooks_total}</span>
                        </div>
                    </div>
                    <div class="group-body ${openGroups.has(g.group_id) ? 'open' : ''}" id="group-${g.group_id}">
                        <div class="section-title">接收端點</div>
                        <div class="endpoint-box">
                            <span>${baseUrl}/webhook/${g.group_id}</span>
                            <button class="copy-btn" onclick="copyText('${baseUrl}/webhook/${g.group_id}')">複製</button>
                        </div>
                        
                        <div class="section-title">發送模式</div>
                        <div class="mode-selector">
                            <button class="mode-btn ${g.send_mode === 'sync' ? 'active' : ''}" onclick="setMode('${g.group_id}', 'sync')">同步模式</button>
                            <button class="mode-btn ${g.send_mode === 'round_robin' ? 'active-rr' : ''}" onclick="setMode('${g.group_id}', 'round_robin')">輪詢模式</button>
                        </div>
                        <div class="mode-info ${g.send_mode}">
                            ${g.send_mode === 'sync' 
                                ? '同步：同時發送到所有排程內的 Webhook' 
                                : '輪詢：輪流發送，跳過排程外的'}
                        </div>
                        
                        <div class="section-title">Webhook 列表 (${g.webhooks_enabled}/${g.webhooks_total})</div>
                        <div class="add-form">
                            <div class="title">添加新 Webhook</div>
                            <div class="flex-row">
                                <input type="text" id="wn-${g.group_id}" placeholder="名稱" style="max-width:110px">
                                <select id="wt-${g.group_id}" style="max-width:95px">
                                    <option value="discord">Discord</option>
                                    <option value="feishu">飛書</option>
                                    <option value="wecom">企業微信</option>
                                </select>
                                <input type="text" id="wu-${g.group_id}" placeholder="Webhook URL">
                                <label style="display:flex;align-items:center;gap:3px;font-size:0.82em;color:var(--text-secondary)">
                                    <input type="checkbox" id="wf-${g.group_id}"><span>固定</span>
                                </label>
                                <button class="btn btn-success btn-sm" onclick="addWebhook('${g.group_id}')">添加</button>
                            </div>
                        </div>
                        
                        ${g.webhooks && g.webhooks.length ? g.webhooks.map((w, i) => {
                            const isNext = g.send_mode === 'round_robin' && w.enabled && !w.is_fixed && isNextWebhook(g, w.id);
                            const scheduleOff = w.schedule_mode !== 'off' && !w.is_in_schedule;
                            return `
                            <div class="webhook-item ${!w.enabled ? 'disabled' : ''} ${isNext ? 'next' : ''} ${w.is_fixed ? 'fixed' : ''} ${scheduleOff ? 'schedule-off' : ''}">
                                <div class="webhook-header">
                                    <div class="webhook-name">
                                        <span class="badge ${w.webhook_type === 'discord' ? 'badge-discord' : w.webhook_type === 'feishu' ? 'badge-feishu' : 'badge-wecom'}">
                                            ${w.webhook_type === 'discord' ? 'Discord' : w.webhook_type === 'feishu' ? '飛書' : '企微'}
                                        </span>
                                        <span>${w.name}</span>
                                        ${w.is_fixed ? '<span class="badge badge-fixed">固定</span>' : ''}
                                        ${isNext ? '<span class="badge badge-next">下一個</span>' : ''}
                                        ${w.schedule_mode !== 'off' ? (w.is_in_schedule 
                                            ? '<span class="badge badge-schedule-on">排程中</span>' 
                                            : '<span class="badge badge-schedule">排程外</span>') : ''}
                                    </div>
                                    <div class="webhook-controls">
                                        <label class="toggle-switch">
                                            <input type="checkbox" ${w.enabled ? 'checked' : ''} onchange="toggleWebhook('${g.group_id}', '${w.id}', this.checked)">
                                            <span class="toggle-slider"></span>
                                        </label>
                                        <button class="btn ${w.is_fixed ? 'btn-purple' : 'btn-outline'} btn-sm" onclick="toggleFixed('${g.group_id}', '${w.id}', ${!w.is_fixed})">固定</button>
                                        <button class="btn btn-warning btn-sm" onclick="toggleSchedulePanel('${w.id}')">排程</button>
                                        <button class="btn btn-outline btn-sm" onclick="renameWebhook('${g.group_id}', '${w.id}', '${w.name.replace(/'/g, "\\'")}')">改名</button>
                                        <button class="btn btn-outline btn-sm" onclick="testWebhook('${g.group_id}', '${w.id}')">測試</button>
                                        <button class="btn btn-danger btn-sm" onclick="removeWebhook('${g.group_id}', '${w.id}')">刪除</button>
                                    </div>
                                </div>
                                <div class="webhook-url">${w.url_preview}</div>
                                <div class="webhook-stats">成功 ${w.sent} | 失敗 ${w.failed}${w.schedule_info ? ' | ' + w.schedule_info : ''}</div>
                                
                                <!-- v4.5 排程面板 -->
                                <div class="schedule-panel ${w.schedule_mode !== 'off' ? 'active' : ''}" id="sp-${w.id}" style="display:none">
                                    <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap">
                                        <label class="toggle-switch">
                                            <input type="checkbox" id="sm-${w.id}" ${w.schedule_mode !== 'off' ? 'checked' : ''} onchange="toggleScheduleMode('${g.group_id}', '${w.id}', this.checked)">
                                            <span class="toggle-slider"></span>
                                        </label>
                                        <span>啟用日期排程</span>
                                        ${w.schedules && w.schedules.length ? '<span style="color:var(--text-muted);font-size:0.9em">(' + w.schedules.length + ' 筆)</span>' : ''}
                                    </div>
                                    <div id="sl-${w.id}">
                                        ${(w.schedules || []).map((s, si) => {
                                            const isExpired = s.date < today;
                                            const isToday = s.date === today;
                                            return '<div class="schedule-row ' + (isExpired ? 'expired' : '') + (isToday ? ' today' : '') + '">' +
                                                '<span class="date">' + formatDateShort(s.date) + '</span>' +
                                                '<span class="time">' + s.start_time + ' - ' + s.end_time + '</span>' +
                                                (isToday && w.is_in_schedule ? '<span class="badge badge-schedule-on" style="font-size:0.7em">生效中</span>' : '') +
                                                (isExpired ? '<span style="font-size:0.7em;color:var(--text-muted)">已過期</span>' : '') +
                                                '<button class="btn btn-danger btn-sm" onclick="removeScheduleItem(\\'' + g.group_id + '\\',\\'' + w.id + '\\',' + si + ')">刪除</button>' +
                                                '</div>';
                                        }).join('')}
                                    </div>
                                    <div class="schedule-add-row">
                                        <input type="date" id="sd-${w.id}" value="${today}" style="max-width:130px;padding:3px">
                                        <input type="time" id="ss-${w.id}" value="00:00" style="max-width:90px;padding:3px">
                                        <span style="color:var(--text-muted)">-</span>
                                        <input type="time" id="se-${w.id}" value="23:59" style="max-width:90px;padding:3px">
                                        <button class="btn btn-success btn-sm" onclick="addScheduleItem('${g.group_id}', '${w.id}')">添加</button>
                                    </div>
                                    <div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">
                                        <button class="btn btn-outline btn-sm" onclick="clearExpiredSchedules('${g.group_id}', '${w.id}')">清除過期</button>
                                    </div>
                                </div>
                            </div>`;
                        }).join('') : '<div class="no-data">尚未添加任何 Webhook</div>'}
                        
                        <div class="section-title">最近記錄</div>
                        ${g.history && g.history.length ? g.history.slice(0, 8).map(h => `
                            <div class="history-item">
                                <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:4px">
                                    <span>
                                        <span class="time">${h.time}</span>
                                        <span class="mode-tag">${h.mode}</span>
                                        ${h.has_image ? '<span class="badge badge-img">圖</span>' : ''}
                                    </span>
                                    <span style="color:var(--text-secondary)">${h.status}</span>
                                </div>
                                <div style="color:var(--text-muted);margin-top:2px">${h.content}</div>
                            </div>
                        `).join('') : '<div class="no-data">暫無記錄</div>'}
                        
                        <div style="margin-top:12px;display:flex;gap:6px;justify-content:flex-end;flex-wrap:wrap">
                            <button class="btn btn-outline btn-sm" onclick="testGroup('${g.group_id}')">測試群組</button>
                            <button class="btn btn-danger btn-sm" onclick="deleteGroup('${g.group_id}')">刪除群組</button>
                        </div>
                    </div>
                </div>
            `).join('');
        }

        // ====== 排程操作 ======
        
        async function getWebhookData(groupId, webhookId) {
            const res = await (await fetch('/api/stats')).json();
            for (const g of res.groups) {
                if (g.group_id === groupId) {
                    for (const w of g.webhooks) {
                        if (w.id === webhookId) return w;
                    }
                }
            }
            return null;
        }
        
        async function toggleScheduleMode(groupId, webhookId, enabled) {
            const w = await getWebhookData(groupId, webhookId);
            if (!w) return;
            const res = await (await fetch('/api/group/' + groupId + '/webhook/' + webhookId + '/schedule', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ schedule_mode: enabled ? 'date_range' : 'off', schedules: w.schedules || [] })
            })).json();
            if (res.success) { showSave(); await loadData(true); }
            else alert(res.message);
        }

        async function addScheduleItem(groupId, webhookId) {
            const dateVal = document.getElementById('sd-' + webhookId).value;
            const startVal = document.getElementById('ss-' + webhookId).value;
            const endVal = document.getElementById('se-' + webhookId).value;
            if (!dateVal || !startVal || !endVal) return alert('請填寫完整');
            
            const w = await getWebhookData(groupId, webhookId);
            if (!w) return;
            
            let schs = [...(w.schedules || [])];
            if (schs.some(s => s.date === dateVal && s.start_time === startVal && s.end_time === endVal)) return alert('此排程已存在');
            schs.push({ date: dateVal, start_time: startVal, end_time: endVal });
            schs.sort((a, b) => (a.date + a.start_time).localeCompare(b.date + b.start_time));
            
            const modeChecked = document.getElementById('sm-' + webhookId).checked;
            const res = await (await fetch('/api/group/' + groupId + '/webhook/' + webhookId + '/schedule', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ schedule_mode: modeChecked ? 'date_range' : 'off', schedules: schs })
            })).json();
            if (res.success) { showSave(); await loadData(true); } else alert(res.message);
        }
        
        async function removeScheduleItem(groupId, webhookId, index) {
            const w = await getWebhookData(groupId, webhookId);
            if (!w) return;
            let schs = [...(w.schedules || [])];
            schs.splice(index, 1);
            await fetch('/api/group/' + groupId + '/webhook/' + webhookId + '/schedule', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ schedule_mode: w.schedule_mode, schedules: schs })
            });
            showSave(); await loadData(true);
        }
        
        async function clearExpiredSchedules(groupId, webhookId) {
            const w = await getWebhookData(groupId, webhookId);
            if (!w) return;
            const today = getTodayStr();
            let schs = [...(w.schedules || [])];
            const filtered = schs.filter(s => s.date >= today);
            if (filtered.length === schs.length) return alert('沒有過期排程');
            await fetch('/api/group/' + groupId + '/webhook/' + webhookId + '/schedule', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ schedule_mode: w.schedule_mode, schedules: filtered })
            });
            showSave(); await loadData(true);
            alert('已清除 ' + (schs.length - filtered.length) + ' 筆過期排程');
        }
        
        // ====== CRUD 操作 ======
        
        async function createGroup() {
            const id = document.getElementById('newGroupId').value.trim();
            const name = document.getElementById('newGroupName').value.trim();
            if (!id) return alert('請輸入群組 ID');
            const res = await (await fetch('/api/group', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ group_id: id, display_name: name || null })
            })).json();
            if (res.success) {
                document.getElementById('newGroupId').value = '';
                document.getElementById('newGroupName').value = '';
                openGroups.add(id.toLowerCase());
                showSave(); await loadData(true);
            } else alert(res.message);
        }
        
        async function deleteGroup(groupId) {
            if (!confirm('確定刪除群組 [' + groupId + ']？')) return;
            await fetch('/api/group/' + groupId, { method: 'DELETE' });
            openGroups.delete(groupId);
            showSave(); await loadData(true);
        }
        
        async function setMode(groupId, mode) {
            const res = await (await fetch('/api/group/' + groupId + '/mode', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ mode })
            })).json();
            if (res.success) { showSave(); await loadData(true); } else alert(res.message);
        }
        
        async function addWebhook(groupId) {
            const name = document.getElementById('wn-' + groupId).value.trim();
            const type = document.getElementById('wt-' + groupId).value;
            const url = document.getElementById('wu-' + groupId).value.trim();
            const fixed = document.getElementById('wf-' + groupId).checked;
            if (!url) return alert('請輸入 Webhook URL');
            const res = await (await fetch('/api/group/' + groupId + '/webhook', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ url, name: name || null, webhook_type: type, is_fixed: fixed })
            })).json();
            if (res.success) {
                document.getElementById('wn-' + groupId).value = '';
                document.getElementById('wu-' + groupId).value = '';
                document.getElementById('wt-' + groupId).value = 'discord';
                document.getElementById('wf-' + groupId).checked = false;
                showSave(); await loadData(true);
            } else alert(res.message);
        }
        
        async function removeWebhook(groupId, webhookId) {
            if (!confirm('確定移除？')) return;
            await fetch('/api/group/' + groupId + '/webhook/' + webhookId, { method: 'DELETE' });
            openSchedulePanels.delete(webhookId);
            showSave(); await loadData(true);
        }
        
        async function toggleWebhook(groupId, webhookId, enabled) {
            await fetch('/api/group/' + groupId + '/webhook/' + webhookId + '/toggle', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ enabled })
            });
            showSave(); await loadData(true);
        }
        
        async function toggleFixed(groupId, webhookId, isFixed) {
            await fetch('/api/group/' + groupId + '/webhook/' + webhookId + '/fixed', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ is_fixed: isFixed })
            });
            showSave(); await loadData(true);
        }
        
        async function renameWebhook(groupId, webhookId, currentName) {
            const newName = prompt('請輸入新名稱:', currentName);
            if (!newName || newName === currentName) return;
            await fetch('/api/group/' + groupId + '/webhook/' + webhookId, {
                method: 'PATCH', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ name: newName })
            });
            showSave(); await loadData(true);
        }
        
        async function testWebhook(groupId, webhookId) {
            const res = await (await fetch('/api/group/' + groupId + '/webhook/' + webhookId + '/test', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ content: '[測試] ' + new Date().toLocaleTimeString() })
            })).json();
            alert(res.success ? '測試成功' : res.message);
            await loadData(true);
        }
        
        async function testGroup(groupId) {
            const content = prompt('測試訊息:', '[測試] ' + groupId.toUpperCase());
            if (!content) return;
            const res = await (await fetch('/webhook/' + groupId, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ content })
            })).json();
            alert(res.message);
            await loadData(true);
        }
        
        // ====== 初始化 ======
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
    print("=" * 50)
    print("  Webhook 中繼站 v4.5")
    print("=" * 50)
    print(f"  本地訪問: http://localhost:{PORT}")
    print(f"  配置文件: {CONFIG_FILE}")
    print(f"  時區: UTC{'+' if TIMEZONE_OFFSET >= 0 else ''}{TIMEZONE_OFFSET}")
    print(f"  密碼保護: {'啟用' if ADMIN_PASSWORD else '停用'}")
    print("=" * 50)
    
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
