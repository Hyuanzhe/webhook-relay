FROM python:3.11-slim

WORKDIR /app

# 複製所有文件
COPY . .

# 安裝依賴
RUN pip install --no-cache-dir -r requirements.txt

# 暴露端口
EXPOSE 5000

# 啟動應用
CMD ["python", "webhook_relay_cloud.py"]
```

4. **提交（Commit changes）**

---

## 📋 或者修改 runtime.txt

如果不想用 Dockerfile，可以：

### 編輯 `runtime.txt`

把內容改成：
```
python-3.11.9
