import os
import json
import time
import subprocess
import tempfile
import zipfile

import requests

# ---------------------------------------------------------------------------
# تنظیمات
# ---------------------------------------------------------------------------
TOKEN = os.getenv("BOT_TOKEN", "70250813:gn0oH_mnDLO1L28KTcPBX9kiMp3SnGnVXLE")
BASE_URL = "https://api.splus.ir/bot" + TOKEN
FILE_BASE_URL = "https://api.splus.ir/file/bot" + TOKEN

MAX_DOWNLOAD_MB = 20  # محدودیت سروش‌پلاس برای دانلود فایل
MAX_DURATION = 60     # حداکثر مدت مجاز برای ویدیو مسیج (ثانیه)
OUTPUT_SIZE = 480      # ابعاد مربع خروجی ویدیو مسیج (px) — افزایش از ۳۸۴ برای وضوح بیشتر
GIF_MAX_WIDTH = 540    # حداکثر عرض خروجی گیف (px)
GIF_MAX_DURATION = 30  # حداکثر مدت گیف (ثانیه) — گیف‌های طولانی حجم خیلی بالا می‌رن
STICKER_SIZE = 512     # سایز استاندارد استیکر وب‌پی (ضلع بزرگ‌تر باید ۵۱۲ باشه)

BTN_VIDEO_NOTE = '🎥 ویدیو مسیج'
BTN_GIF = '🎞 گیف'
BTN_VOICE = '🎙 ویس'
BTN_ENHANCE = '✨ افزایش کیفیت'
BTN_COMPRESS = '🗜 فشرده‌سازی'
BTN_STICKER = '🧩 استیکر'
BTN_PDF = '📄 تبدیل به PDF'

PDF_MAX_FRAMES = 20   # حداکثر تعداد صفحه‌ی PDF وقتی ویدیو به PDF تبدیل می‌شه
PDF_MAX_DIM = 1600    # حداکثر ابعاد هر صفحه (px) برای کنترل حجم PDF

# اگه می‌خوای هنگام /start یه استیکر خوش‌آمد بفرستی، این‌جا file_id همون استیکر
# (یا لینک HTTP مستقیمش) رو بذار. برای گرفتن file_id: یه استیکر برای بات بفرست
# و توی لاگ کنسول (که با پرینت زیر اضافه شده) file_id رو ببین.
WELCOME_STICKER = ''

# مسیر مدل آفلاین Vosk برای فارسی (برای تبدیل ویس به متن).
# روی Railway به شل سرور دسترسی نداری، پس مدل موقع اجرای بات به‌صورت خودکار
# دانلود و اکسترکت می‌شه (تابع ensure_vosk_model پایین‌تر) — کافیه این لینک زیپ
# مدل رو درست نگه داری. برای مدل دقیق‌تر (و سنگین‌تر، ۱.۶ گیگ) لینک رو با
# vosk-model-fa-0.42.zip عوض کن.
VOSK_MODEL_URL = 'https://alphacephei.com/vosk/models/vosk-model-small-fa-0.42.zip'
VOSK_MODEL_PATH = os.getenv('VOSK_MODEL_PATH', './vosk-model-fa')

# ویدیوها/عکس‌هایی که منتظر انتخاب کاربرن: chat_id -> dict
pending_videos = {}
pending_photos = {}

SESSION = requests.Session()
_adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=0)
SESSION.mount('https://', _adapter)
SESSION.mount('http://', _adapter)


# ---------------------------------------------------------------------------
# توابع کمکی API
# ---------------------------------------------------------------------------

def get_updates(offset=None):
    params = {'timeout': 25}
    if offset:
        params['offset'] = offset
    try:
        res = SESSION.get(BASE_URL + '/getUpdates', params=params, timeout=30)
        data = res.json()
        if not data.get('ok'):
            print('⚠️ خطای getUpdates:', data)
        return data
    except Exception as e:
        print('⚠️ استثنا در getUpdates:', e)
        return {'ok': False, 'result': []}


def send_message(chat_id, text, reply_markup=None):
    # طبق مستندات متن پیام حداکثر ۴۰۹۶ کاراکتره؛ متن‌های بلندتر (مثل متن ویس) تکه‌تکه می‌شن
    chunks = [text[i:i + MAX_MESSAGE_LEN] for i in range(0, len(text), MAX_MESSAGE_LEN)] or ['']
    for idx, chunk in enumerate(chunks):
        payload = {'chat_id': chat_id, 'text': chunk}
        if reply_markup and idx == len(chunks) - 1:
            payload['reply_markup'] = json.dumps(reply_markup)
        try:
            res = SESSION.post(BASE_URL + '/sendMessage', json=payload, timeout=15)
            data = res.json()
            if not data.get('ok'):
                print('⚠️ خطای sendMessage:', data)
        except Exception as e:
            print('⚠️ خطا در sendMessage:', e)


def ask_video_conversion_type(chat_id):
    keyboard = {
        'keyboard': [
            [{'text': BTN_VIDEO_NOTE}, {'text': BTN_GIF}],
            [{'text': BTN_VOICE}, {'text': BTN_ENHANCE}],
            [{'text': BTN_COMPRESS}, {'text': BTN_PDF}]
        ],
        'resize_keyboard': True,
        'one_time_keyboard': True
    }
    send_message(chat_id, '🎬 این ویدیو رو به چه شکلی تبدیل کنم؟', reply_markup=keyboard)


def ask_photo_conversion_type(chat_id):
    keyboard = {
        'keyboard': [
            [{'text': BTN_STICKER}, {'text': BTN_ENHANCE}],
            [{'text': BTN_COMPRESS}, {'text': BTN_PDF}]
        ],
        'resize_keyboard': True,
        'one_time_keyboard': True
    }
    send_message(chat_id, '🖼 این عکس رو چیکار کنم؟', reply_markup=keyboard)


def remove_keyboard(chat_id, text):
    send_message(chat_id, text, reply_markup={'remove_keyboard': True})


def get_file_path(file_id):
    """گرفتن file_path از API برای ساخت لینک دانلود."""
    try:
        res = SESSION.get(BASE_URL + '/getFile', params={'file_id': file_id}, timeout=15)
        data = res.json()
        if data.get('ok'):
            return data['result']['file_path']
        print('⚠️ خطای getFile:', data)
        return None
    except Exception as e:
        print('⚠️ استثنا در getFile:', e)
        return None


def download_file(file_path, dest_path, expected_size=None, max_retries=3):
    """
    این تابع عمداً از SESSION مشترک استفاده نمی‌کنه و هر بار یه اتصال تازه باز می‌کنه.
    وقتی HTTP 200 برمی‌گرده ولی بدنه خالیه (۰ بایت)، معمولاً یعنی یه اتصال
    keep-alive قدیمی/نیمه‌بسته از pool دوباره استفاده شده — با یه اتصال کاملاً
    تازه (و چند تلاش مجدد) این مشکل معمولاً حل می‌شه.
    """
    url = FILE_BASE_URL + '/' + file_path
    print('⬇️ دانلود از:', url)

    for attempt in range(1, max_retries + 1):
        try:
            with requests.get(url, stream=True, timeout=60, headers={'Connection': 'close'}) as r:
                print(f'   تلاش {attempt} | وضعیت HTTP: {r.status_code} | Content-Type: {r.headers.get("Content-Type")} | Content-Length: {r.headers.get("Content-Length")}')
                r.raise_for_status()
                with open(dest_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            f.write(chunk)

            actual_size = os.path.getsize(dest_path)
            print(f'   حجم دانلود شده: {actual_size} بایت' + (f' (انتظار: {expected_size} بایت)' if expected_size else ''))

            if actual_size == 0:
                print(f'⚠️ فایل دانلودشده خالیه (تلاش {attempt}/{max_retries}).')
                time.sleep(1.5)
                continue

            if expected_size and abs(actual_size - expected_size) > 1024:
                print(f'⚠️ حجم دانلودشده با حجم مورد انتظار همخوانی نداره (تلاش {attempt}/{max_retries}).')
                time.sleep(1.5)
                continue

            return True

        except Exception as e:
            print(f'⚠️ خطا در دانلود فایل (تلاش {attempt}/{max_retries}):', e)
            time.sleep(1.5)

    return False


MAX_UPLOAD_MB = 50        # سقف آپلود برای ویدیو/ویس/سند/انیمیشن طبق مستندات
MAX_PHOTO_UPLOAD_MB = 10  # سقف آپلود عکس (sendPhoto) طبق مستندات
MAX_MESSAGE_LEN = 4096    # سقف طول متن پیام طبق مستندات


def _upload(chat_id, method, field, path, filename, mime, data=None, timeout=90, max_mb=None):
    """
    آپلود multipart برای همه‌ی متدهای ارسال فایل.
    طبق مستندات: فایل باید به‌صورت multipart/form-data آپلود بشه و حجمش از سقف مجاز
    (۱۰ مگ برای عکس، ۵۰ مگ برای بقیه) بیشتر نباشه. اسم فایل و MIME هم همیشه
    صریح فرستاده می‌شن (بدون پسوند، سرور نوع فایل رو درست تشخیص نمی‌ده).
    """
    if max_mb is None:
        max_mb = MAX_UPLOAD_MB
    size = os.path.getsize(path)
    if size > max_mb * 1024 * 1024:
        print(f'⚠️ {method}: حجم فایل خروجی ({size // (1024 * 1024)}MB) از سقف {max_mb}MB بیشتره.')
        send_message(chat_id, f'⚠️ حجم فایل خروجی بیشتر از {max_mb} مگابایت شد و قابل ارسال نیست.')
        return {'ok': False, 'too_big': True}

    payload = {'chat_id': chat_id}
    if data:
        payload.update({k: v for k, v in data.items() if v is not None})
    try:
        with open(path, 'rb') as f:
            files = {field: (filename, f, mime)}
            res = SESSION.post(BASE_URL + '/' + method, data=payload, files=files, timeout=timeout)
        result = res.json()
        if not result.get('ok'):
            print(f'⚠️ خطای {method}:', result)
        return result
    except Exception as e:
        print(f'⚠️ استثنا در {method}:', e)
        return {'ok': False}


def send_video_note(chat_id, video_path, duration=None):
    # طبق مستندات: ویدیوی MPEG4 مربعی تا ۱ دقیقه؛ length = قطر دایره؛ duration = ثانیه
    data = {'length': OUTPUT_SIZE}
    if duration:
        data['duration'] = int(round(duration))
    return _upload(chat_id, 'sendVideoNote', 'video_note', video_path,
                   'video_note.mp4', 'video/mp4', data=data, timeout=90)


def send_sticker(chat_id, sticker_path):
    """ارسال استیکر WEBP به‌صورت multipart (طبق مستندات sendSticker)."""
    return _upload(chat_id, 'sendSticker', 'sticker', sticker_path,
                   'sticker.webp', 'image/webp', timeout=60)


def send_sticker_ref(chat_id, sticker_ref):
    """ارسال استیکر با file_id یا لینک HTTP (بدون آپلود فایل) — برای استیکر خوش‌آمدِ ثابت."""
    try:
        res = SESSION.post(BASE_URL + '/sendSticker', data={'chat_id': chat_id, 'sticker': sticker_ref}, timeout=15)
        result = res.json()
        if not result.get('ok'):
            print('⚠️ خطای sendSticker (ref):', result)
        return result
    except Exception as e:
        print('⚠️ استثنا در sendSticker (ref):', e)
        return {'ok': False}


def send_document(chat_id, doc_path, filename=None, mime='application/octet-stream'):
    """ارسال فایل به‌صورت سند (تا ۵۰ مگابایت)."""
    return _upload(chat_id, 'sendDocument', 'document', doc_path,
                   filename or os.path.basename(doc_path), mime, timeout=120)


def send_photo(chat_id, photo_path):
    """
    عکس حداکثر ۱۰ مگابایت، مجموع عرض+ارتفاع حداکثر ۱۰۰۰۰ و نسبت ابعاد حداکثر ۲۰ باید باشه.
    اگه sendPhoto رد شد (مثلاً نسبت ابعاد زیاد یا حجم بالا)، به‌عنوان سند ارسال می‌شه
    تا کاربر دست‌خالی نمونه.
    """
    ext = os.path.splitext(photo_path)[1].lower()
    mime = 'image/png' if ext == '.png' else 'image/jpeg'
    size = os.path.getsize(photo_path)
    if size <= MAX_PHOTO_UPLOAD_MB * 1024 * 1024:
        result = _upload(chat_id, 'sendPhoto', 'photo', photo_path,
                         os.path.basename(photo_path), mime, timeout=60,
                         max_mb=MAX_PHOTO_UPLOAD_MB)
        if result.get('ok'):
            return result
    print('ℹ️ ارسال به‌صورت عکس ممکن نشد، تلاش برای ارسال به‌صورت سند...')
    return send_document(chat_id, photo_path, os.path.basename(photo_path), mime)


def send_animation(chat_id, path, width=None, height=None, duration=None):
    # طبق مستندات: GIF یا ویدیوی H.264/MPEG-4 AVC بدون صدا، تا ۵۰ مگابایت
    filename = os.path.basename(path)
    mime_type = 'image/gif' if filename.lower().endswith('.gif') else 'video/mp4'
    data = {
        'width': int(width) if width else None,
        'height': int(height) if height else None,
        'duration': int(round(duration)) if duration else None,
    }
    return _upload(chat_id, 'sendAnimation', 'animation', path, filename, mime_type, data=data, timeout=120)


def send_voice(chat_id, voice_path, duration=None):
    """
    طبق مستندات: برای نمایش به‌صورت پیام صوتی باید OGG با کدک OPUS (یا MP3/M4A) باشه.
    """
    data = {'duration': int(round(duration)) if duration else None}
    return _upload(chat_id, 'sendVoice', 'voice', voice_path, 'voice.ogg', 'audio/ogg', data=data, timeout=90)


def send_video(chat_id, video_path, width=None, height=None, duration=None):
    """ارسال ویدیوی معمولی MPEG4 (نه ویدیو مسیجِ گرد) — تا ۵۰ مگابایت."""
    data = {
        'width': int(width) if width else None,
        'height': int(height) if height else None,
        'duration': int(round(duration)) if duration else None,
        'supports_streaming': 'true',
    }
    return _upload(chat_id, 'sendVideo', 'video', video_path, 'video.mp4', 'video/mp4', data=data, timeout=180)


# ---------------------------------------------------------------------------
# پردازش ویدیو با ffmpeg
# ---------------------------------------------------------------------------

def get_video_duration(path):
    try:
        out = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', path],
            capture_output=True, text=True, timeout=20
        )
        return float(out.stdout.strip())
    except Exception:
        return None


def get_video_dimensions(path):
    try:
        out = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
             '-show_entries', 'stream=width,height', '-of', 'csv=p=0', path],
            capture_output=True, text=True, timeout=20
        )
        w, h = out.stdout.strip().split(',')
        return int(w), int(h)
    except Exception:
        return None, None


def convert_to_video_note(input_path, output_path):
    """
    کراپ به مربع (از وسط) + ریسایز به OUTPUT_SIZE + محدود کردن مدت به MAX_DURATION
    + انکود به mp4/h264 که برای video_note لازمه.
    """
    duration = get_video_duration(input_path)
    trim_args = []
    if duration and duration > MAX_DURATION:
        trim_args = ['-t', str(MAX_DURATION)]

    vf = f"crop='min(iw,ih)':'min(iw,ih)',scale={OUTPUT_SIZE}:{OUTPUT_SIZE}"

    cmd = [
        'ffmpeg', '-y', '-i', input_path,
        *trim_args,
        '-vf', vf,
        '-c:v', 'libx264', '-profile:v', 'baseline', '-level', '3.1',
        '-preset', 'medium', '-crf', '18',
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '128k',
        '-movflags', '+faststart',
        output_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print('⚠️ خطای ffmpeg:', result.stderr[-2000:])
            return False
        return True
    except Exception as e:
        print('⚠️ استثنا در ffmpeg:', e)
        return False


def convert_to_gif(input_path, output_path):
    """
    برخلاف mp4 بی‌صدا، خروجی این تابع یه فایل .gif واقعیه — چون کلاینت
    سروش‌پلاس فقط وقتی فایل واقعاً از نوع gif باشه، لیبل «گیف» رو نشون می‌ده.
    از روش دوپاس (palette) استفاده می‌شه تا کیفیت رنگ گیف قابل قبول بمونه.
    """
    duration = get_video_duration(input_path)
    trim_args = []
    if duration and duration > GIF_MAX_DURATION:
        trim_args = ['-t', str(GIF_MAX_DURATION)]

    tmp_dir = os.path.dirname(output_path)
    palette_path = os.path.join(tmp_dir, 'palette.png')

    fps = 15
    scale = f"'min({GIF_MAX_WIDTH},iw)':-2:flags=lanczos"

    # پاس اول: ساخت پالت رنگی بهینه
    palette_cmd = [
        'ffmpeg', '-y', '-i', input_path,
        *trim_args,
        '-vf', f'fps={fps},scale={scale},palettegen=stats_mode=diff',
        palette_path
    ]
    # پاس دوم: ساخت گیف نهایی با استفاده از پالت
    gif_cmd = [
        'ffmpeg', '-y', '-i', input_path, '-i', palette_path,
        *trim_args,
        '-filter_complex', f'fps={fps},scale={scale}[x];[x][1:v]paletteuse=dither=sierra2_4a',
        '-loop', '0',
        output_path
    ]
    try:
        r1 = subprocess.run(palette_cmd, capture_output=True, text=True, timeout=120)
        if r1.returncode != 0:
            print('⚠️ خطای ffmpeg (ساخت پالت گیف):', r1.stderr[-2000:])
            return False

        r2 = subprocess.run(gif_cmd, capture_output=True, text=True, timeout=120)
        if r2.returncode != 0:
            print('⚠️ خطای ffmpeg (ساخت گیف):', r2.stderr[-2000:])
            return False

        return True
    except Exception as e:
        print('⚠️ استثنا در ffmpeg (گیف):', e)
        return False


def convert_video_to_voice(input_path, output_path):
    """
    استخراج صدای ویدیو و تبدیل به ogg/opus (فرمت استاندارد پیام صوتی).
    مونو و بیت‌ریت پایین چون برای صدای صحبت کافیه و حجم رو خیلی کم می‌کنه.
    """
    cmd = [
        'ffmpeg', '-y', '-i', input_path,
        '-vn',
        '-acodec', 'libopus', '-b:a', '64k', '-ar', '48000', '-ac', '1',
        output_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print('⚠️ خطای ffmpeg (ویس):', result.stderr[-2000:])
            return False
        return True
    except Exception as e:
        print('⚠️ استثنا در ffmpeg (ویس):', e)
        return False


ENHANCE_MAX_DIM = 1280  # حداکثر ابعاد خروجی بعد از بزرگ‌نمایی (px) — برای جلوگیری از حجم/زمان بیش‌ازحد


def convert_enhance_quality(input_path, output_path):
    """
    افزایش کیفیت ویدیوهای کم‌کیفیت (مثل ویدیوهای فشرده‌شده‌ی چندبار فوروارد شده):
    ۱. hqdn3d: کاهش نویز و آرتیفکت‌های فشرده‌سازی (بلاک/موسکیتو نویز)
    ۲. scale (لنکزوس): بزرگ‌نمایی هوشمند اگه رزولوشن ورودی پایینه (حداکثر تا ENHANCE_MAX_DIM)
    ۳. unsharp: شارپ کردن لبه‌ها بعد از دنویز و بزرگ‌نمایی، تا تصویر واضح‌تر به‌نظر برسه
    خروجی با crf پایین (کیفیت بالا) و صدا با بیت‌ریت بالاتر انکود می‌شه.
    """
    vf = (
        "hqdn3d=1.5:1.5:6:6,"
        f"scale='min({ENHANCE_MAX_DIM},max(iw,iw*1.5))':'min({ENHANCE_MAX_DIM},max(ih,ih*1.5))':"
        "force_original_aspect_ratio=decrease:flags=lanczos,"
        "unsharp=5:5:0.8:5:5:0.0"
    )
    cmd = [
        'ffmpeg', '-y', '-i', input_path,
        '-vf', vf,
        '-c:v', 'libx264', '-preset', 'medium', '-crf', '17',
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '160k',
        '-movflags', '+faststart',
        output_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            print('⚠️ خطای ffmpeg (افزایش کیفیت):', result.stderr[-2000:])
            return False
        return True
    except Exception as e:
        print('⚠️ استثنا در ffmpeg (افزایش کیفیت):', e)
        return False


def compress_video(input_path, output_path):
    """
    کاهش حجم ویدیو با افت کیفیت قابل‌قبول:
    - محدود کردن عرض به حداکثر ۷۲۰px (اگه ورودی بزرگ‌تره)
    - crf بالاتر (فشرده‌سازی بیشتر) با preset متعادل
    - بیت‌ریت صدا پایین‌تر
    """
    vf = "scale='min(720,iw)':-2"
    cmd = [
        'ffmpeg', '-y', '-i', input_path,
        '-vf', vf,
        '-c:v', 'libx264', '-preset', 'medium', '-crf', '30',
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '96k',
        '-movflags', '+faststart',
        output_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            print('⚠️ خطای ffmpeg (فشرده‌سازی ویدیو):', result.stderr[-2000:])
            return False
        return True
    except Exception as e:
        print('⚠️ استثنا در ffmpeg (فشرده‌سازی ویدیو):', e)
        return False


def convert_image_to_sticker(input_path, output_path):
    """
    تبدیل عکس به webp با ابعاد استاندارد استیکر: ضلع بزرگ‌تر همیشه دقیقاً
    STICKER_SIZE می‌شه (چه عکس ورودی بزرگ‌تر باشه چه کوچیک‌تر) و نسبت تصویر
    حفظ می‌شه — چون خیلی از کلاینت‌ها استیکر ایستا رو رد می‌کنن اگه هیچ ضلعی
    دقیقاً ۵۱۲ نباشه.
    """
    vf = (
        f"scale='if(gte(iw,ih),{STICKER_SIZE},trunc({STICKER_SIZE}*iw/ih/2)*2)':"
        f"'if(gte(iw,ih),trunc({STICKER_SIZE}*ih/iw/2)*2,{STICKER_SIZE})',"
        "unsharp=5:5:0.5:5:5:0.0"
    )
    cmd = [
        'ffmpeg', '-y', '-i', input_path,
        '-vf', vf,
        '-vcodec', 'libwebp',
        '-lossless', '0', '-compression_level', '6', '-q:v', '95',
        '-pix_fmt', 'yuva420p',
        output_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            print('⚠️ خطای ffmpeg (استیکر):', result.stderr[-2000:])
            return False
        return True
    except Exception as e:
        print('⚠️ استثنا در ffmpeg (استیکر):', e)
        return False


def _images_to_pdf(image_paths, output_path):
    """ساخت PDF چندصفحه‌ای از لیست عکس‌ها (با Pillow). هر عکس یه صفحه."""
    from PIL import Image, ImageOps

    pages = []
    for path in image_paths:
        img = Image.open(path)
        img = ImageOps.exif_transpose(img)
        if img.mode in ('RGBA', 'LA', 'P'):
            # پس‌زمینه‌ی سفید برای عکس‌های شفاف (PNG و...)
            rgba = img.convert('RGBA')
            bg = Image.new('RGB', rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[-1])
            img = bg
        else:
            img = img.convert('RGB')
        if max(img.size) > PDF_MAX_DIM:
            img.thumbnail((PDF_MAX_DIM, PDF_MAX_DIM), Image.LANCZOS)
        pages.append(img)

    if not pages:
        return False
    pages[0].save(output_path, 'PDF', resolution=150.0, save_all=True, append_images=pages[1:])
    return True


def convert_image_to_pdf(input_path, output_path):
    """تبدیل یه عکس به PDF تک‌صفحه‌ای."""
    try:
        return _images_to_pdf([input_path], output_path)
    except Exception as e:
        print('⚠️ خطا در تبدیل عکس به PDF (پکیج Pillow نصبه؟):', e)
        return False


def convert_video_to_pdf(input_path, output_path, work_dir):
    """
    چند فریم یکنواخت از ویدیو (حداکثر PDF_MAX_FRAMES تا) استخراج می‌کنه و
    هر فریم رو یه صفحه‌ی PDF می‌کنه. اگه ویدیو کوتاه باشه تقریباً هر ثانیه یه فریم.
    """
    duration = get_video_duration(input_path)
    if duration and duration > 0:
        n_frames = max(1, min(PDF_MAX_FRAMES, int(duration)))
        fps = n_frames / duration
    else:
        n_frames = PDF_MAX_FRAMES
        fps = 0.5

    frames_pattern = os.path.join(work_dir, 'frame_%03d.jpg')
    cmd = [
        'ffmpeg', '-y', '-i', input_path,
        '-vf', f"fps={fps:.5f},scale='min({PDF_MAX_DIM},iw)':-2",
        '-frames:v', str(n_frames),
        '-q:v', '3',
        frames_pattern
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            print('⚠️ خطای ffmpeg (استخراج فریم):', result.stderr[-2000:])
            return False
        frames = sorted(
            os.path.join(work_dir, f) for f in os.listdir(work_dir)
            if f.startswith('frame_') and f.endswith('.jpg')
        )
        if not frames:
            return False
        return _images_to_pdf(frames, output_path)
    except Exception as e:
        print('⚠️ خطا در تبدیل ویدیو به PDF:', e)
        return False


ENHANCE_IMG_MAX_DIM = 2048  # حداکثر ابعاد خروجی بعد از بزرگ‌نمایی (px)


def enhance_image_quality(input_path, output_path):
    """
    افزایش کیفیت عکس: کاهش نویز/آرتیفکت فشرده‌سازی + بزرگ‌نمایی هوشمند (لنکزوس)
    اگه عکس ورودی رزولوشن پایینی داره + شارپ کردن نهایی.
    خروجی JPEG با کیفیت بالا (q:v پایین یعنی کیفیت بالاتر).
    """
    vf = (
        "hqdn3d=1.5:1.5:6:6,"
        f"scale='min({ENHANCE_IMG_MAX_DIM},max(iw,iw*1.5))':'min({ENHANCE_IMG_MAX_DIM},max(ih,ih*1.5))':"
        "force_original_aspect_ratio=decrease:flags=lanczos,"
        "unsharp=5:5:0.8:5:5:0.0"
    )
    cmd = [
        'ffmpeg', '-y', '-i', input_path,
        '-vf', vf,
        '-q:v', '2',
        output_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            print('⚠️ خطای ffmpeg (افزایش کیفیت عکس):', result.stderr[-2000:])
            return False
        return True
    except Exception as e:
        print('⚠️ استثنا در ffmpeg (افزایش کیفیت عکس):', e)
        return False


def compress_image(input_path, output_path):
    """کاهش حجم عکس: محدود کردن ابعاد به حداکثر ۱۲۸۰px + کیفیت JPEG متوسط."""
    vf = "scale='min(1280,iw)':-2"
    cmd = [
        'ffmpeg', '-y', '-i', input_path,
        '-vf', vf,
        '-q:v', '7',
        output_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            print('⚠️ خطای ffmpeg (فشرده‌سازی عکس):', result.stderr[-2000:])
            return False
        return True
    except Exception as e:
        print('⚠️ استثنا در ffmpeg (فشرده‌سازی عکس):', e)
        return False


# ---------------------------------------------------------------------------
# تبدیل ویس به متن (آفلاین، با Vosk)
# ---------------------------------------------------------------------------
_vosk_model = None
_vosk_load_failed = False


def ensure_vosk_model():
    """
    اگه پوشه‌ی مدل وجود نداشته باشه (مثلاً روی Railway که به شل دسترسی نداری
    و هر دیپلوی جدید فایل‌سیستم رو از صفر می‌سازه)، زیپ مدل رو دانلود و
    اکسترکت می‌کنه. این کار فقط بار اولی که ویس بیاد انجام می‌شه.
    """
    if os.path.isdir(VOSK_MODEL_PATH):
        return True
    try:
        print('⬇️ دانلود مدل Vosk فارسی (اولین اجرا، ممکنه کمی طول بکشه)...')
        zip_path = VOSK_MODEL_PATH.rstrip('/\\') + '.zip'
        with requests.get(VOSK_MODEL_URL, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(zip_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        f.write(chunk)

        extract_dir = os.path.dirname(VOSK_MODEL_PATH) or '.'
        with zipfile.ZipFile(zip_path, 'r') as z:
            top_folder = z.namelist()[0].split('/')[0]
            z.extractall(extract_dir)

        os.remove(zip_path)
        extracted_path = os.path.join(extract_dir, top_folder)
        if extracted_path != VOSK_MODEL_PATH:
            os.rename(extracted_path, VOSK_MODEL_PATH)

        print('✅ مدل Vosk با موفقیت آماده شد.')
        return True
    except Exception as e:
        print('⚠️ دانلود/اکسترکت مدل Vosk ناموفق بود:', e)
        return False


def get_vosk_model():
    """
    مدل رو فقط یه‌بار لود می‌کنه (لود مدل چند ثانیه طول می‌کشه، برای هر پیام
    نباید دوباره انجام بشه). نیاز به نصب پکیج vosk داره (تو requirements.txt بذار).
    """
    global _vosk_model, _vosk_load_failed
    if _vosk_model is not None or _vosk_load_failed:
        return _vosk_model
    try:
        from vosk import Model
        if not ensure_vosk_model():
            _vosk_load_failed = True
            return None
        _vosk_model = Model(VOSK_MODEL_PATH)
        return _vosk_model
    except Exception as e:
        print('⚠️ خطا در لود مدل Vosk (پکیج vosk نصبه؟):', e)
        _vosk_load_failed = True
        return None


def convert_to_wav(input_path, output_path):
    """تبدیل هر فرمت صوتی به wav تک‌کاناله ۱۶kHz — فرمتی که Vosk نیاز داره."""
    cmd = ['ffmpeg', '-y', '-i', input_path, '-ar', '16000', '-ac', '1', '-f', 'wav', output_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return result.returncode == 0
    except Exception as e:
        print('⚠️ استثنا در ffmpeg (تبدیل به wav):', e)
        return False


def transcribe_audio(wav_path):
    import wave
    from vosk import KaldiRecognizer

    model = get_vosk_model()
    if not model:
        return None

    wf = wave.open(wav_path, 'rb')
    try:
        rec = KaldiRecognizer(model, wf.getframerate())
        rec.SetWords(False)

        full_text = []
        while True:
            data = wf.readframes(4000)
            if len(data) == 0:
                break
            if rec.AcceptWaveform(data):
                part = json.loads(rec.Result()).get('text', '')
                if part:
                    full_text.append(part)

        final = json.loads(rec.FinalResult()).get('text', '')
        if final:
            full_text.append(final)

        return ' '.join(full_text).strip()
    finally:
        wf.close()


def process_incoming_voice(chat_id, voice):
    file_size = voice.get('file_size') or 0
    if file_size and file_size > MAX_DOWNLOAD_MB * 1024 * 1024:
        send_message(chat_id, f'⚠️ حجم ویس بیشتر از {MAX_DOWNLOAD_MB} مگابایت است و قابل پردازش نیست.')
        return

    if get_vosk_model() is None:
        send_message(chat_id, '⚠️ سرویس تبدیل ویس به متن روی سرور تنظیم نشده (مدل Vosk پیدا نشد).')
        return

    send_message(chat_id, '⏳ در حال تبدیل ویس به متن...')

    file_path = get_file_path(voice['file_id'])
    if not file_path:
        send_message(chat_id, '⚠️ خطا در دریافت فایل ویس.')
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        input_path = os.path.join(tmp_dir, 'input.ogg')
        if not download_file(file_path, input_path, expected_size=file_size):
            send_message(chat_id, '⚠️ دانلود ویس ناموفق بود.')
            return

        wav_path = os.path.join(tmp_dir, 'audio.wav')
        if not convert_to_wav(input_path, wav_path):
            send_message(chat_id, '⚠️ تبدیل فرمت صوتی ناموفق بود.')
            return

        text = transcribe_audio(wav_path)
        if not text:
            send_message(chat_id, '🤷‍♂️ چیزی از این ویس تشخیص داده نشد. شاید صداش واضح نیست.')
            return

        send_message(chat_id, f'📝 متن ویس:\n\n{text}')


# ---------------------------------------------------------------------------
# حلقه اصلی
# ---------------------------------------------------------------------------

def handle_incoming_video(chat_id, video):
    file_size = video.get('file_size') or 0
    if file_size and file_size > MAX_DOWNLOAD_MB * 1024 * 1024:
        send_message(chat_id, f'⚠️ حجم ویدیو بیشتر از {MAX_DOWNLOAD_MB} مگابایت است و قابل پردازش نیست.')
        return

    pending_videos[chat_id] = video
    pending_photos.pop(chat_id, None)
    ask_video_conversion_type(chat_id)


def process_pending_video(chat_id, mode):
    video = pending_videos.pop(chat_id, None)
    if not video:
        remove_keyboard(chat_id, '⚠️ ویدیویی برای تبدیل پیدا نشد. یه ویدیوی جدید بفرست.')
        return

    labels = {'video_note': 'ویدیو مسیج', 'gif': 'گیف', 'voice': 'ویس', 'enhance': 'کیفیت بالاتر', 'compress': 'حجم کمتر', 'pdf': 'PDF'}
    remove_keyboard(chat_id, f'⏳ در حال تبدیل ویدیو به {labels[mode]}...')

    file_size = video.get('file_size') or 0
    file_path = get_file_path(video['file_id'])
    if not file_path:
        send_message(chat_id, '⚠️ خطا در دریافت فایل ویدیو.')
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        input_path = os.path.join(tmp_dir, 'input.mp4')

        if not download_file(file_path, input_path, expected_size=file_size):
            send_message(chat_id, '⚠️ دانلود ویدیو ناموفق بود.')
            return

        if mode == 'video_note':
            output_path = os.path.join(tmp_dir, 'output.mp4')
            if not convert_to_video_note(input_path, output_path):
                send_message(chat_id, '⚠️ تبدیل ویدیو ناموفق بود.')
                return
            duration = get_video_duration(output_path)
            result = send_video_note(chat_id, output_path, duration)
            if not result.get('ok') and not result.get('too_big'):
                send_message(chat_id, '⚠️ ارسال ویدیو مسیج ناموفق بود.')

        elif mode == 'gif':
            output_path = os.path.join(tmp_dir, 'output.gif')
            if not convert_to_gif(input_path, output_path):
                send_message(chat_id, '⚠️ تبدیل ویدیو به گیف ناموفق بود.')
                return
            width, height = get_video_dimensions(output_path)
            gif_duration = get_video_duration(output_path)
            result = send_animation(chat_id, output_path, width, height, gif_duration)
            if not result.get('ok') and not result.get('too_big'):
                send_message(chat_id, '⚠️ ارسال گیف ناموفق بود.')

        elif mode == 'voice':
            output_path = os.path.join(tmp_dir, 'output.ogg')
            if not convert_video_to_voice(input_path, output_path):
                send_message(chat_id, '⚠️ تبدیل ویدیو به ویس ناموفق بود. (احتمالاً ویدیو صدا نداره)')
                return
            duration = get_video_duration(output_path)
            result = send_voice(chat_id, output_path, duration)
            if not result.get('ok') and not result.get('too_big'):
                send_message(chat_id, '⚠️ ارسال ویس ناموفق بود.')

        elif mode == 'enhance':
            output_path = os.path.join(tmp_dir, 'output_enhanced.mp4')
            if not convert_enhance_quality(input_path, output_path):
                send_message(chat_id, '⚠️ افزایش کیفیت ویدیو ناموفق بود.')
                return
            duration = get_video_duration(output_path)
            width, height = get_video_dimensions(output_path)
            result = send_video(chat_id, output_path, width, height, duration)
            if not result.get('ok') and not result.get('too_big'):
                send_message(chat_id, '⚠️ ارسال ویدیوی بهبودیافته ناموفق بود.')

        elif mode == 'pdf':
            output_path = os.path.join(tmp_dir, 'output.pdf')
            if not convert_video_to_pdf(input_path, output_path, tmp_dir):
                send_message(chat_id, '⚠️ تبدیل ویدیو به PDF ناموفق بود.')
                return
            result = send_document(chat_id, output_path, 'video.pdf', 'application/pdf')
            if not result.get('ok') and not result.get('too_big'):
                send_message(chat_id, '⚠️ ارسال PDF ناموفق بود.')

        else:  # compress
            output_path = os.path.join(tmp_dir, 'output_compressed.mp4')
            if not compress_video(input_path, output_path):
                send_message(chat_id, '⚠️ فشرده‌سازی ویدیو ناموفق بود.')
                return
            duration = get_video_duration(output_path)
            width, height = get_video_dimensions(output_path)
            result = send_video(chat_id, output_path, width, height, duration)
            if not result.get('ok') and not result.get('too_big'):
                send_message(chat_id, '⚠️ ارسال ویدیوی فشرده‌شده ناموفق بود.')
            elif result.get('ok'):
                before = video.get('file_size') or 0
                after = os.path.getsize(output_path)
                if before:
                    if after < before * 0.95:
                        send_message(chat_id, f'📉 حجم از {before // 1024} کیلوبایت به {after // 1024} کیلوبایت رسید.')
                    else:
                        send_message(chat_id, 'ℹ️ این فایل از قبل کم‌حجم بود و فشرده‌سازی حجمش رو کمتر نکرد.')


def handle_incoming_photo(chat_id, photo_sizes):
    """photo_sizes آرایه‌ای از PhotoSize هست؛ بزرگ‌ترینش رو انتخاب می‌کنیم."""
    photo = max(photo_sizes, key=lambda p: p.get('file_size') or (p.get('width', 0) * p.get('height', 0)))

    file_size = photo.get('file_size') or 0
    if file_size and file_size > MAX_DOWNLOAD_MB * 1024 * 1024:
        send_message(chat_id, f'⚠️ حجم عکس بیشتر از {MAX_DOWNLOAD_MB} مگابایت است و قابل پردازش نیست.')
        return

    pending_photos[chat_id] = photo
    pending_videos.pop(chat_id, None)
    ask_photo_conversion_type(chat_id)


def process_pending_photo(chat_id, mode):
    photo = pending_photos.pop(chat_id, None)
    if not photo:
        remove_keyboard(chat_id, '⚠️ عکسی برای تبدیل پیدا نشد. یه عکس جدید بفرست.')
        return

    labels = {'sticker': 'استیکر', 'enhance': 'کیفیت بالاتر', 'compress': 'حجم کمتر', 'pdf': 'PDF'}
    remove_keyboard(chat_id, f'⏳ در حال تبدیل عکس به {labels[mode]}...')

    file_size = photo.get('file_size') or 0
    file_path = get_file_path(photo['file_id'])
    if not file_path:
        send_message(chat_id, '⚠️ خطا در دریافت فایل عکس.')
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        ext = os.path.splitext(file_path)[1] or '.jpg'
        input_path = os.path.join(tmp_dir, 'input' + ext)

        if not download_file(file_path, input_path, expected_size=file_size):
            send_message(chat_id, '⚠️ دانلود عکس ناموفق بود.')
            return

        if mode == 'sticker':
            output_path = os.path.join(tmp_dir, 'output.webp')
            if not convert_image_to_sticker(input_path, output_path):
                send_message(chat_id, '⚠️ تبدیل عکس به استیکر ناموفق بود.')
                return
            result = send_sticker(chat_id, output_path)
            if not result.get('ok') and not result.get('too_big'):
                send_message(chat_id, '⚠️ ارسال استیکر ناموفق بود.')

        elif mode == 'enhance':
            output_path = os.path.join(tmp_dir, 'output_enhanced.jpg')
            if not enhance_image_quality(input_path, output_path):
                send_message(chat_id, '⚠️ افزایش کیفیت عکس ناموفق بود.')
                return
            result = send_photo(chat_id, output_path)
            if not result.get('ok') and not result.get('too_big'):
                send_message(chat_id, '⚠️ ارسال عکس بهبودیافته ناموفق بود.')

        elif mode == 'pdf':
            output_path = os.path.join(tmp_dir, 'output.pdf')
            if not convert_image_to_pdf(input_path, output_path):
                send_message(chat_id, '⚠️ تبدیل عکس به PDF ناموفق بود.')
                return
            result = send_document(chat_id, output_path, 'photo.pdf', 'application/pdf')
            if not result.get('ok') and not result.get('too_big'):
                send_message(chat_id, '⚠️ ارسال PDF ناموفق بود.')

        else:  # compress
            output_path = os.path.join(tmp_dir, 'output_compressed.jpg')
            if not compress_image(input_path, output_path):
                send_message(chat_id, '⚠️ فشرده‌سازی عکس ناموفق بود.')
                return
            result = send_photo(chat_id, output_path)
            if not result.get('ok') and not result.get('too_big'):
                send_message(chat_id, '⚠️ ارسال عکس فشرده‌شده ناموفق بود.')
            elif result.get('ok'):
                before = photo.get('file_size') or 0
                after = os.path.getsize(output_path)
                if before:
                    if after < before * 0.95:
                        send_message(chat_id, f'📉 حجم از {before // 1024} کیلوبایت به {after // 1024} کیلوبایت رسید.')
                    else:
                        send_message(chat_id, 'ℹ️ این فایل از قبل کم‌حجم بود و فشرده‌سازی حجمش رو کمتر نکرد.')


def main():
    print('✅ بات تبدیل ویدیو به ویدیو مسیج اجرا شد.')
    last_update_id = 0

    while True:
        try:
            updates = get_updates(last_update_id + 1)

            if updates.get('ok') and updates.get('result'):
                for update in updates['result']:
                    last_update_id = update['update_id']

                    message = update.get('message')
                    if not message:
                        continue

                    chat_id = message['chat']['id']

                    doc = message.get('document') or {}
                    doc_mime = (doc.get('mime_type') or '').lower()
                    if 'video' in message:
                        handle_incoming_video(chat_id, message['video'])
                    elif 'video_note' in message:
                        handle_incoming_video(chat_id, message['video_note'])
                    elif 'animation' in message:
                        handle_incoming_video(chat_id, message['animation'])
                    elif doc and doc_mime.startswith('video/'):
                        handle_incoming_video(chat_id, doc)
                    elif doc and doc_mime.startswith('image/'):
                        handle_incoming_photo(chat_id, [doc])
                    elif 'photo' in message:
                        handle_incoming_photo(chat_id, message['photo'])
                    elif 'voice' in message:
                        process_incoming_voice(chat_id, message['voice'])
                    elif 'sticker' in message:
                        # کمک برای گرفتن file_id استیکر خودت جهت تنظیم WELCOME_STICKER
                        print('ℹ️ file_id استیکر دریافتی:', message['sticker'].get('file_id'))
                    elif 'text' in message and message['text'] == BTN_VIDEO_NOTE:
                        process_pending_video(chat_id, 'video_note')
                    elif 'text' in message and message['text'] == BTN_GIF:
                        process_pending_video(chat_id, 'gif')
                    elif 'text' in message and message['text'] == BTN_VOICE:
                        process_pending_video(chat_id, 'voice')
                    elif 'text' in message and message['text'] == BTN_STICKER:
                        process_pending_photo(chat_id, 'sticker')
                    elif 'text' in message and message['text'] == BTN_ENHANCE:
                        # این دکمه بین منوی ویدیو و عکس مشترکه؛ ببینیم کدوم در انتظاره
                        if chat_id in pending_photos:
                            process_pending_photo(chat_id, 'enhance')
                        else:
                            process_pending_video(chat_id, 'enhance')
                    elif 'text' in message and message['text'] == BTN_COMPRESS:
                        if chat_id in pending_photos:
                            process_pending_photo(chat_id, 'compress')
                        else:
                            process_pending_video(chat_id, 'compress')
                    elif 'text' in message and message['text'] == BTN_PDF:
                        if chat_id in pending_photos:
                            process_pending_photo(chat_id, 'pdf')
                        else:
                            process_pending_video(chat_id, 'pdf')
                    elif 'text' in message and message['text'] in ('/start', 'شروع'):
                        if WELCOME_STICKER:
                            send_sticker_ref(chat_id, WELCOME_STICKER)
                        send_message(
                            chat_id,
                            '👋 سلام! به بات چندکاره خوش اومدی.\n\n'
                            'کافیه یکی از این‌ها رو برام بفرستی:\n\n'
                            '🎥 یه ویدیو → می‌تونی تبدیلش کنی به:\n'
                            '   • ویدیو مسیج (پیام گرد)\n'
                            '   • گیف متحرک\n'
                            '   • ویس (فقط صداش)\n'
                            '   • نسخه‌ی با کیفیت‌تر\n'
                            '   • نسخه‌ی فشرده (حجم کمتر)\n'
                            '   • PDF (چند فریم از ویدیو)\n\n'
                            '🖼 یه عکس → می‌تونی تبدیلش کنی به:\n'
                            '   • استیکر\n'
                            '   • نسخه‌ی با کیفیت‌تر\n'
                            '   • نسخه‌ی فشرده (حجم کمتر)\n'
                            '   • PDF\n\n'
                            '🎙 یه ویس → متنش رو برات می‌نویسم.\n\n'
                            f'📦 حداکثر حجم قابل قبول برای هر فایل: {MAX_DOWNLOAD_MB} مگابایت.'
                        )
            else:
                time.sleep(1)

        except Exception as loop_error:
            print('⚠️ خطای غیرمنتظره در حلقه اصلی:', loop_error)
            time.sleep(2)


if __name__ == '__main__':
    main()
