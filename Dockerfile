FROM python:3.10-slim

# نصب ffmpeg و ffprobe
RUN apt-get update && apt-get install -y ffmpeg

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# نام فایل پایتون شما
CMD ["python", "video_note_bot.py"]
