import google.generativeai as genai
import os

# 1. Điền API Key của bạn vào đây
os.environ["GOOGLE_API_KEY"] = "AIzaSyBgJPLxwH8TQ13QFv1nZBduBt6qqRIjJu0"
genai.configure(api_key=os.environ["GOOGLE_API_KEY"])

print("--- DANH SÁCH MODEL MIỄN PHÍ CỦA BẠN ---")
try:
    for m in genai.list_models():
        # Chỉ lấy model tạo văn bản (generateContent)
        if 'generateContent' in m.supported_generation_methods:
            print(f"Tên model: {m.name}")
except Exception as e:
    print(f"Lỗi: {e}")