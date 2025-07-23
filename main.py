# Enhanced Telegram Media Compressor Bot for Koyeb
# Optimized for serverless deployment - FIXED VERSION

import os
import tempfile
import subprocess
import asyncio
import glob
import time
import shutil
import json
import logging
from datetime import datetime, timedelta
from pyrogram import Client 
from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from pyrogram.errors import FloodWait, RPCError
from pathlib import Path
from typing import Dict, List, Optional

# Configure logging for better debugging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration with environment variables for Koyeb
class Config:
    # Bot Credentials - Use environment variables for security
    API_ID = os.getenv("API_ID")
    API_HASH = os.getenv("API_HASH")
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")

    # Validate required environment variables
    @classmethod
    def validate(cls):
        required_vars = ["API_ID", "API_HASH", "BOT_TOKEN"]
        missing = [var for var in required_vars if not getattr(cls, var)]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
        
        # Convert API_ID to int if it exists
        if cls.API_ID:
            try:
                cls.API_ID = int(cls.API_ID)
            except ValueError:
                raise ValueError("API_ID must be a valid integer")

    # File size limits (adjusted for Koyeb)
    MAX_AUDIO_SIZE = int(os.getenv("MAX_AUDIO_SIZE"))  # 500MB
    MAX_VIDEO_SIZE = int(os.getenv("MAX_VIDEO_SIZE"))  # 900MB
    MIN_FILE_SIZE = 1024  # 1KB minimum

    # Rate limiting
    MAX_FILES_PER_HOUR = int(os.getenv("MAX_FILES_PER_HOUR"))
    MAX_FILES_PER_DAY = int(os.getenv("MAX_FILES_PER_DAY"))

    # Processing limits for Koyeb
    MAX_CONCURRENT_PROCESSES = int(os.getenv("MAX_CONCURRENT_PROCESSES"))
    PROCESS_TIMEOUT = int(os.getenv("PROCESS_TIMEOUT"))  # 20 minutes

    # Storage - Use /tmp for Koyeb
    TEMP_DIR = "/tmp"
    STATS_FILE = "/tmp/bot_stats.json"

    # Webhook settings for Koyeb (if needed)
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")
    PORT = int(os.getenv("PORT"))

# Audio compression presets
AUDIO_PRESETS = {
    "ultra_high": {"bitrate": "128k", "format": "mp3", "channels": 2},
    "high": {"bitrate": "96k", "format": "mp3", "channels": 2},
    "medium": {"bitrate": "64k", "format": "mp3", "channels": 2},
    "low": {"bitrate": "32k", "format": "mp3", "channels": 1},
    "ultra_low": {"bitrate": "16k", "format": "mp3", "channels": 1}
}

# Video compression presets (optimized for Koyeb)
VIDEO_PRESETS = {
    "ultra_high": {
        "scale": "1280:720", "fps": 25, "bitrate": "1000k", "crf": 23,
        "preset": "fast", "audio_bitrate": "96k"
    },
    "high": {
        "scale": "854:480", "fps": 25, "bitrate": "700k", "crf": 25,
        "preset": "fast", "audio_bitrate": "80k"
    },
    "medium": {
        "scale": "640:360", "fps": 20, "bitrate": "500k", "crf": 28,
        "preset": "faster", "audio_bitrate": "64k"
    },
    "low": {
        "scale": "480:270", "fps": 15, "bitrate": "300k", "crf": 32,
        "preset": "faster", "audio_bitrate": "48k"
    },
    "ultra_low": {
        "scale": "320:240", "fps": 15, "bitrate": "150k", "crf": 35,
        "preset": "ultrafast", "audio_bitrate": "32k"
    }
}

# Video codec settings
VIDEO_CODEC_SETTINGS = {
    "h264": {"codec": "libx264", "profile": "baseline", "level": "3.1"},
    "h265": {"codec": "libx265", "profile": "main", "level": "3.1"}
}

class BotStats:
    """Statistics tracking with file persistence"""
    def __init__(self):
        self.stats = self.load_stats()

    def load_stats(self) -> Dict:
        try:
            if os.path.exists(Config.STATS_FILE):
                with open(Config.STATS_FILE, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error loading stats: {e}")

        return {
            "total_files_processed": 0,
            "total_bytes_saved": 0,
            "audio_files": 0,
            "video_files": 0,
            "users": {},
            "daily_stats": {},
            "errors": 0,
            "start_time": time.time()
        }

    def save_stats(self):
        try:
            with open(Config.STATS_FILE, 'w') as f:
                json.dump(self.stats, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving stats: {e}")

    def update_processing_stats(self, user_id: int, file_type: str,
                              original_size: int, compressed_size: int):
        today = datetime.now().strftime("%Y-%m-%d")

        # Update totals
        self.stats["total_files_processed"] += 1
        self.stats["total_bytes_saved"] += max(0, original_size - compressed_size)
        self.stats[f"{file_type}_files"] = self.stats.get(f"{file_type}_files", 0) + 1

        # Update user stats
        if str(user_id) not in self.stats["users"]:
            self.stats["users"][str(user_id)] = {"files": 0, "bytes_saved": 0}

        self.stats["users"][str(user_id)]["files"] += 1
        self.stats["users"][str(user_id)]["bytes_saved"] += max(0, original_size - compressed_size)

        # Update daily stats
        if today not in self.stats["daily_stats"]:
            self.stats["daily_stats"][today] = {"files": 0, "users": set()}

        self.stats["daily_stats"][today]["files"] += 1
        if isinstance(self.stats["daily_stats"][today]["users"], set):
            self.stats["daily_stats"][today]["users"].add(user_id)
            self.stats["daily_stats"][today]["users"] = list(self.stats["daily_stats"][today]["users"])
        elif user_id not in self.stats["daily_stats"][today]["users"]:
            self.stats["daily_stats"][today]["users"].append(user_id)

        self.save_stats()

    def get_user_stats(self, user_id: int) -> Dict:
        return self.stats["users"].get(str(user_id), {"files": 0, "bytes_saved": 0})

class RateLimiter:
    """Rate limiting with memory storage"""
    def __init__(self):
        self.user_activity: Dict[int, List[float]] = {}

    def update_activity(self, user_id: int):
        current_time = time.time()
        if user_id not in self.user_activity:
            self.user_activity[user_id] = []

        # Remove old activity (older than 24 hours)
        self.user_activity[user_id] = [
            t for t in self.user_activity[user_id]
            if current_time - t < 86400  # 24 hours
        ]

        self.user_activity[user_id].append(current_time)

    def check_limits(self, user_id: int) -> Dict[str, bool]:
        if user_id not in self.user_activity:
            return {"hourly": False, "daily": False}

        current_time = time.time()
        recent_activity = self.user_activity[user_id]

        # Check hourly limit
        hourly_count = len([t for t in recent_activity if current_time - t < 3600])
        hourly_exceeded = hourly_count >= Config.MAX_FILES_PER_HOUR

        # Check daily limit
        daily_count = len([t for t in recent_activity if current_time - t < 86400])
        daily_exceeded = daily_count >= Config.MAX_FILES_PER_DAY

        return {"hourly": hourly_exceeded, "daily": daily_exceeded}

class ProcessingQueue:
    """Simple queue system for Koyeb"""
    def __init__(self, max_concurrent: int = Config.MAX_CONCURRENT_PROCESSES):
        self.max_concurrent = max_concurrent
        self.active_processes: Dict[int, asyncio.Task] = {}
        self.queue: List[tuple] = []

    async def add_to_queue(self, user_id: int, process_func, *args, **kwargs):
        if len(self.active_processes) < self.max_concurrent:
            task = asyncio.create_task(process_func(*args, **kwargs))
            self.active_processes[user_id] = task
            try:
                await task
            finally:
                self.active_processes.pop(user_id, None)
                await self.process_queue()
        else:
            self.queue.append((user_id, process_func, args, kwargs))
            return "queued"

    async def process_queue(self):
        if self.queue and len(self.active_processes) < self.max_concurrent:
            user_id, process_func, args, kwargs = self.queue.pop(0)
            task = asyncio.create_task(process_func(*args, **kwargs))
            self.active_processes[user_id] = task
            try:
                await task
            finally:
                self.active_processes.pop(user_id, None)
                await self.process_queue()

def format_file_size(size_bytes: int) -> str:
    """Convert bytes to human readable format"""
    if size_bytes == 0:
        return "0 B"
    size_names = ["B", "KB", "MB", "GB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    return f"{size_bytes:.2f} {size_names[i]}"

def get_optimal_settings(file_size_mb: float, media_type: str) -> str:
    """Determine optimal settings based on file size"""
    if media_type == "audio":
        if file_size_mb <= 5:
            return "ultra_high"
        elif file_size_mb <= 15:
            return "high"
        elif file_size_mb <= 30:
            return "medium"
        elif file_size_mb <= 60:
            return "low"
        else:
            return "ultra_low"
    else:  # video
        if file_size_mb <= 25:
            return "ultra_high"
        elif file_size_mb <= 75:
            return "high"
        elif file_size_mb <= 150:
            return "medium"
        elif file_size_mb <= 300:
            return "low"
        else:
            return "ultra_low"

def check_ffmpeg_installation() -> bool:
    """Check if FFmpeg is available"""
    try:
        result = subprocess.run(['ffmpeg', '-version'],
                              capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            version_line = result.stdout.split('\n')[0]
            logger.info(f"FFmpeg found: {version_line}")
            return True
    except Exception as e:
        logger.error(f"FFmpeg check failed: {e}")

    logger.warning("FFmpeg not found or not accessible")
    return False

async def compress_audio_koyeb(input_path: str, output_path: str, preset: str) -> bool:
    """Compress audio using FFmpeg optimized for Koyeb"""
    try:
        audio_settings = AUDIO_PRESETS[preset]
        
        cmd = [
            'ffmpeg', '-i', input_path, '-y',
            '-c:a', 'libmp3lame',
            '-b:a', audio_settings['bitrate'],
            '-ac', str(audio_settings['channels']),
            '-ar', '44100',
            '-threads', '2',  # Limit threads for Koyeb
            '-map_metadata', '0',
            output_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=Config.PROCESS_TIMEOUT)
        return result.returncode == 0

    except Exception as e:
        logger.error(f"Audio compression error: {e}")
        return False

async def compress_video_koyeb(input_path: str, output_path: str, preset: str, codec: str = "h264") -> tuple:
    """Compress video optimized for Koyeb"""
    try:
        video_settings = VIDEO_PRESETS[preset]
        codec_settings = VIDEO_CODEC_SETTINGS[codec]

        # Basic FFmpeg command optimized for Koyeb
        cmd = [
            'ffmpeg', '-i', input_path, '-y',
            '-c:v', codec_settings['codec'],
            '-crf', str(video_settings['crf']),
            '-preset', video_settings['preset'],
            '-b:v', video_settings['bitrate'],
            '-maxrate', video_settings['bitrate'],
            '-bufsize', str(int(video_settings['bitrate'].replace('k', '')) * 2) + 'k',
            '-vf', f"scale={video_settings['scale']},fps={video_settings['fps']}",
            '-c:a', 'aac',
            '-b:a', video_settings['audio_bitrate'],
            '-movflags', '+faststart',
            '-threads', '2',  # Limit threads for Koyeb
            '-map_metadata', '0',
            output_path
        ]

        if codec_settings['profile']:
            cmd.extend(['-profile:v', codec_settings['profile']])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=Config.PROCESS_TIMEOUT)
        success = result.returncode == 0

        # Basic video info (simplified for Koyeb)
        video_info = {}
        if success:
            try:
                probe_cmd = [
                    'ffprobe', '-v', 'quiet', '-print_format', 'json',
                    '-show_format', '-show_streams', input_path
                ]
                probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
                if probe_result.returncode == 0:
                    probe_data = json.loads(probe_result.stdout)
                    for stream in probe_data.get('streams', []):
                        if stream.get('codec_type') == 'video':
                            video_info = {
                                'duration': int(float(stream.get('duration', 0))),
                                'width': stream.get('width', 0),
                                'height': stream.get('height', 0)
                            }
                            break
            except:
                pass

        return success, video_info, None

    except Exception as e:
        logger.error(f"Video compression error: {e}")
        return False, {}, None

# Validate configuration first
try:
    Config.validate()
    logger.info("✅ Configuration validated successfully")
except ValueError as e:
    logger.error(f"❌ Configuration error: {e}")
    exit(1)

# Initialize global components
ffmpeg_available = check_ffmpeg_installation()
stats_manager = BotStats()
rate_limiter = RateLimiter()
processing_queue = ProcessingQueue()

# Create app with error handling
try:
    app = Client(
        "koyeb_media_bot", 
        api_id=Config.API_ID, 
        api_hash=Config.API_HASH, 
        bot_token=Config.BOT_TOKEN
    )
    logger.info("✅ Pyrogram client created successfully")
except Exception as e:
    logger.error(f"❌ Failed to create Pyrogram client: {e}")
    exit(1)

# Global state management
user_states: Dict[int, Dict] = {}

def get_user_state(user_id: int) -> Dict:
    """Get or create user state"""
    if user_id not in user_states:
        user_states[user_id] = {
            "mode": None,
            "audio_preset": "medium",
            "video_preset": "medium",
            "video_codec": "h264",
            "last_activity": time.time()
        }
    return user_states[user_id]

@app.on_message(filters.command("start") & ~filters.me & ~filters.bot)
async def start(client, message):
    try:
        user_id = message.from_user.id
        username = message.from_user.username or "Unknown"
        logger.info(f"Start command received from user {user_id} (@{username})")

        # Reset user state
        user_states.pop(user_id, None)

        user_stats = stats_manager.get_user_stats(user_id)

        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎧 Audio Compression", callback_data="mode_audio")],
            [InlineKeyboardButton("🎥 Video Compression", callback_data="mode_video")],
            [
                InlineKeyboardButton("📊 My Stats", callback_data="user_stats"),
                InlineKeyboardButton("ℹ️ Help", callback_data="help")
            ]
        ])

        welcome_msg = (
            "🤖 **Media Compressor Bot (Koyeb)**\n\n"
            "⚡ **Fast serverless compression!**\n\n"
            "🎯 **Features:**\n"
            "• High-quality audio & video compression\n"
            "• 5 quality presets available\n"
            "• Smart auto-optimization\n"
            "• Fast serverless processing\n\n"
            "📊 **Your Stats:**\n"
            f"• Files processed: {user_stats['files']}\n"
            f"• Space saved: {format_file_size(user_stats['bytes_saved'])}\n\n"
            "📏 **Limits:**\n"
            f"• Audio: {format_file_size(Config.MAX_AUDIO_SIZE)}\n"
            f"• Video: {format_file_size(Config.MAX_VIDEO_SIZE)}\n\n"
            "Choose your compression mode:"
        )

        await message.reply_text(welcome_msg, reply_markup=markup)
        logger.info(f"✅ Start message sent successfully to user {user_id}")

    except Exception as e:
        logger.error(f"❌ Error in start command: {e}")
        try:
            await message.reply_text("❌ An error occurred. Please try again later.")
        except:
            pass

@app.on_callback_query()
async def callback_handler(client, callback_query: CallbackQuery):
    try:
        user_id = callback_query.from_user.id
        username = callback_query.from_user.username or "Unknown"
        data = callback_query.data

        logger.info(f"Callback received: '{data}' from user {user_id} (@{username})")

        user_state = get_user_state(user_id)
        user_state["last_activity"] = time.time()

        if data.startswith("mode_"):
            mode = data.replace("mode_", "")
            user_state["mode"] = mode

            if mode == "audio":
                markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔊 Ultra High", callback_data="audio_ultra_high")],
                    [InlineKeyboardButton("📢 High Quality", callback_data="audio_high")],
                    [InlineKeyboardButton("🔉 Medium Quality ✓", callback_data="audio_medium")],
                    [InlineKeyboardButton("🔈 Low Quality", callback_data="audio_low")],
                    [InlineKeyboardButton("📱 Ultra Low", callback_data="audio_ultra_low")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
                ])

                response_msg = (
                    "🎧 **Audio Compression Mode**\n\n"
                    "📁 Send audio files or voice messages\n"
                    f"📏 Max size: {format_file_size(Config.MAX_AUDIO_SIZE)}\n\n"
                    "⚙️ **Quality Presets:**\n"
                    "🔊 **Ultra High**: 128kbps, Stereo\n"
                    "📢 **High**: 96kbps, Stereo\n"
                    "🔉 **Medium**: 64kbps, Stereo (Default)\n"
                    "🔈 **Low**: 32kbps, Mono\n"
                    "📱 **Ultra Low**: 16kbps, Mono\n\n"
                    "Choose quality level:"
                )

            elif mode == "video":
                if not ffmpeg_available:
                    await callback_query.answer("❌ Video compression unavailable!")
                    return

                markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎬 Ultra High", callback_data="video_ultra_high")],
                    [InlineKeyboardButton("📺 High Quality", callback_data="video_high")],
                    [InlineKeyboardButton("🖥️ Medium Quality ✓", callback_data="video_medium")],
                    [InlineKeyboardButton("📱 Low Quality", callback_data="video_low")],
                    [InlineKeyboardButton("⚡ Ultra Low", callback_data="video_ultra_low")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
                ])

                response_msg = (
                    "🎥 **Video Compression Mode**\n\n"
                    "📁 Send video files to compress\n"
                    f"📏 Max size: {format_file_size(Config.MAX_VIDEO_SIZE)}\n\n"
                    "⚙️ **Quality Presets:**\n"
                    "🎬 **Ultra High**: 720p, 25fps, 1000k\n"
                    "📺 **High**: 480p, 25fps, 700k\n"
                    "🖥️ **Medium**: 360p, 20fps, 500k\n"
                    "📱 **Low**: 270p, 15fps, 300k\n"
                    "⚡ **Ultra Low**: 240p, 15fps, 150k\n\n"
                    "Choose quality level:"
                )

            await callback_query.message.reply_text(response_msg, reply_markup=markup)

        elif data.startswith(("audio_", "video_")):
            preset_type, quality = data.split("_", 1)
            user_state[f"{preset_type}_preset"] = quality

            quality_names = {
                "ultra_high": "🎬 Ultra High",
                "high": "📺 High",
                "medium": "🖥️ Medium",
                "low": "📱 Low",
                "ultra_low": "⚡ Ultra Low"
            }

            quality_name = quality_names.get(quality, quality.title())

            if preset_type == "audio":
                preset_info = AUDIO_PRESETS[quality]
                response_msg = (
                    f"✅ **Audio quality set to: {quality_name}**\n\n"
                    f"🎵 Bitrate: {preset_info['bitrate']}\n"
                    f"📻 Channels: {preset_info['channels']}\n"
                    f"📄 Format: {preset_info['format'].upper()}\n\n"
                    "⚡ **Ready for processing!**\n"
                    "Now send me audio files to compress!"
                )
            else:
                preset_info = VIDEO_PRESETS[quality]
                response_msg = (
                    f"✅ **Video quality set to: {quality_name}**\n\n"
                    f"📐 Resolution: {preset_info['scale']}\n"
                    f"🎬 FPS: {preset_info['fps']}\n"
                    f"📊 Video Bitrate: {preset_info['bitrate']}\n"
                    f"🔊 Audio Bitrate: {preset_info['audio_bitrate']}\n\n"
                    "⚡ **Ready for processing!**\n"
                    "Now send me video files to compress!"
                )

            await callback_query.message.reply_text(response_msg)

        elif data == "user_stats":
            user_stats = stats_manager.get_user_stats(user_id)
            response_msg = (
                "📊 **Your Statistics**\n\n"
                f"📁 Files processed: {user_stats['files']}\n"
                f"💾 Space saved: {format_file_size(user_stats['bytes_saved'])}\n\n"
                "📈 **Current Session:**\n"
                f"⏱️ Queue position: {len(processing_queue.queue)}\n"
                f"🔄 Active processes: {len(processing_queue.active_processes)}\n\n"
                "⚡ **Powered by Koyeb**"
            )

            await callback_query.message.reply_text(response_msg)

        elif data == "help":
            help_msg = (
                "ℹ️ **How to Use This Bot**\n\n"
                "1️⃣ Choose compression mode (Audio/Video)\n"
                "2️⃣ Select quality preset\n"
                "3️⃣ Send your media file\n"
                "4️⃣ Wait for processing & download result\n\n"
                "⚡ **Koyeb Features:**\n"
                "• Fast serverless processing\n"
                "• Reliable compression\n"
                "• Multiple quality options\n"
                "• Smart optimization\n\n"
                "⚠️ **Rate Limits:**\n"
                f"• {Config.MAX_FILES_PER_HOUR} files per hour\n"
                f"• {Config.MAX_FILES_PER_DAY} files per day\n\n"
                "💡 **Tip**: Use Medium quality for best balance!"
            )

            await callback_query.message.reply_text(help_msg)

        elif data == "back_main":
            await start(client, callback_query.message)

        await callback_query.answer("✅")

    except Exception as e:
        logger.error(f"❌ Error in callback handler: {e}")
        try:
            await callback_query.answer("❌ An error occurred")
        except:
            pass

@app.on_message(filters.audio | filters.voice | filters.video | filters.document)
async def handle_media(client, message):
    try:
        user_id = message.from_user.id
        username = message.from_user.username or "Unknown"

        # Check rate limits
        limits = rate_limiter.check_limits(user_id)
        if limits["daily"]:
            await message.reply_text("❌ **Daily limit exceeded!** Please try again tomorrow.")
            return
        if limits["hourly"]:
            await message.reply_text("❌ **Hourly limit exceeded!** Please try again in an hour.")
            return

        user_state = get_user_state(user_id)

        if not user_state["mode"]:
            await message.reply_text(
                "❌ **Please select a compression mode first!**\n"
                "Use /start to choose Audio or Video compression."
            )
            return

        # Determine media type and get file info
        media_file = None
        if message.audio or message.voice:
            media_file = message.audio or message.voice
            media_type = "audio"
            max_size = Config.MAX_AUDIO_SIZE
        elif message.video:
            media_file = message.video
            media_type = "video"
            max_size = Config.MAX_VIDEO_SIZE
        elif message.document:
            # Check if document is a media file
            if message.document.mime_type:
                if message.document.mime_type.startswith(('audio/', 'video/')):
                    media_file = message.document
                    media_type = "audio" if message.document.mime_type.startswith('audio/') else "video"
                    max_size = Config.MAX_AUDIO_SIZE if media_type == "audio" else Config.MAX_VIDEO_SIZE

        if not media_file:
            await message.reply_text("❌ **Unsupported file type!** Please send audio or video files only.")
            return

        if media_file.file_size > max_size:
            await message.reply_text(
                f"❌ **File too large!**\n"
                f"Max size for {media_type}: {format_file_size(max_size)}\n"
                f"Your file: {format_file_size(media_file.file_size)}"
            )
            return

        if media_file.file_size < Config.MIN_FILE_SIZE:
            await message.reply_text("❌ **File too small!** Minimum size is 1KB.")
            return

        # Update rate limiter
        rate_limiter.update_activity(user_id)

        # Add to processing queue
        await processing_queue.add_to_queue(
            user_id,
            process_media_file_koyeb,
            client, message, media_file, media_type, user_state
        )

    except Exception as e:
        logger.error(f"❌ Error in handle_media: {e}")
        try:
            await message.reply_text("❌ An error occurred while processing your request. Please try again.")
        except:
            pass

async def process_media_file_koyeb(client, message, media_file, media_type, user_state):
    """Process media file optimized for Koyeb"""
    user_id = message.from_user.id

    # Send processing message
    processing_msg = await message.reply_text(
        f"⚡ **Processing {media_type} on Koyeb...**\n\n"
        f"📁 File: {media_file.file_name or 'Unknown'}\n"
        f"📊 Size: {format_file_size(media_file.file_size)}\n"
        f"⚙️ Quality: {user_state[f'{media_type}_preset'].title()}\n"
        f"🔄 Status: Downloading..."
    )

    temp_dir = tempfile.mkdtemp(dir=Config.TEMP_DIR)

    try:
        # Download file
        file_extension = 'tmp'
        if media_file.file_name:
            file_extension = media_file.file_name.split('.')[-1]
        elif media_type == 'audio':
            file_extension = 'mp3'
        elif media_type == 'video':
            file_extension = 'mp4'

        input_file = os.path.join(temp_dir, f"input.{file_extension}")
        await client.download_media(media_file, input_file)

        # Update status
        await processing_msg.edit_text(
            f"⚡ **Processing {media_type} on Koyeb...**\n\n"
            f"📁 File: {media_file.file_name or 'Unknown'}\n"
            f"📊 Size: {format_file_size(media_file.file_size)}\n"
            f"⚙️ Quality: {user_state[f'{media_type}_preset'].title()}\n"
            f"🔄 Status: Compressing..."
        )

        # Get optimal settings if auto mode
        file_size_mb = media_file.file_size / (1024 * 1024)
        preset = user_state[f"{media_type}_preset"]
        if preset == "auto":
            preset = get_optimal_settings(file_size_mb, media_type)
            logger.info(f"Auto-selected {preset} preset for {file_size_mb:.2f}MB {media_type} file")

        # Compress file
        output_extension = "mp3" if media_type == "audio" else "mp4"
        output_file = os.path.join(temp_dir, f"compressed.{output_extension}")

        start_time = time.time()
        success = False
        video_info = {}

        if media_type == "audio":
            success = await compress_audio_koyeb(input_file, output_file, preset)
        else:
            codec = user_state.get("video_codec", "h264")
            success, video_info, _ = await compress_video_koyeb(input_file, output_file, preset, codec)

        processing_time = time.time() - start_time

        if not success or not os.path.exists(output_file):
            raise Exception("Compression failed")

        # Get file sizes
        original_size = os.path.getsize(input_file)
        compressed_size = os.path.getsize(output_file)
        compression_ratio = ((original_size - compressed_size) / original_size) * 100 if original_size > 0 else 0

        # Update statistics
        stats_manager.update_processing_stats(user_id, media_type, original_size, compressed_size)

        # Update status
        await processing_msg.edit_text(
            f"✅ **Compression completed!**\n\n"
            f"📁 Original: {format_file_size(original_size)}\n"
            f"📦 Compressed: {format_file_size(compressed_size)}\n"
            f"💾 Saved: {format_file_size(original_size - compressed_size)} ({compression_ratio:.1f}%)\n"
            f"⏱️ Time: {processing_time:.1f}s\n"
            f"⚡ Uploading..."
        )

        # Prepare caption
        caption = (
            f"🎯 **Compressed {media_type.title()}**\n\n"
            f"📊 **Statistics:**\n"
            f"• Original: {format_file_size(original_size)}\n"
            f"• Compressed: {format_file_size(compressed_size)}\n"
            f"• Space saved: {format_file_size(original_size - compressed_size)} ({compression_ratio:.1f}%)\n"
            f"• Quality: {preset.title()}\n"
            f"• Processing time: {processing_time:.1f}s\n\n"
            f"⚡ Powered by Koyeb"
        )

        # Send compressed file
        if media_type == "audio":
            await message.reply_audio(
                audio=output_file,
                caption=caption,
                title=f"Compressed - {media_file.file_name or 'Audio'}",
                performer="Koyeb Compressor Bot",
                duration=getattr(media_file, 'duration', None)
            )
        else:
            # Enhanced video sending
            send_kwargs = {
                'video': output_file,
                'caption': caption,
                'supports_streaming': True
            }

            # Add duration if available
            if video_info.get('duration'):
                send_kwargs['duration'] = video_info['duration']
            elif hasattr(media_file, 'duration') and media_file.duration:
                send_kwargs['duration'] = media_file.duration

            # Add dimensions if available
            if video_info.get('width') and video_info.get('height'):
                send_kwargs['width'] = video_info['width']
                send_kwargs['height'] = video_info['height']
            elif hasattr(media_file, 'width') and hasattr(media_file, 'height'):
                send_kwargs['width'] = media_file.width
                send_kwargs['height'] = media_file.height

            await message.reply_video(**send_kwargs)

        # Delete processing message
        try:
            await processing_msg.delete()
        except:
            pass

        logger.info(f"Successfully processed {media_type} for user {user_id}: {compression_ratio:.1f}% compression in {processing_time:.1f}s")

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error processing {media_type} for user {user_id}: {error_msg}")

        stats_manager.stats["errors"] += 1
        stats_manager.save_stats()

        try:
            await processing_msg.edit_text(
                f"❌ **Processing failed!**\n\n"
                f"Error: {error_msg}\n\n"
                f"💡 **Suggestions:**\n"
                f"• Try a lower quality preset\n"
                f"• Ensure file isn't corrupted\n"
                f"• Check file format compatibility\n\n"
                f"Contact support if issue persists."
            )
        except:
            await message.reply_text(
                f"❌ **Processing failed!**\n\n"
                f"Please try again with a different file or lower quality settings."
            )

    finally:
        # Cleanup
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except:
            pass

@app.on_message(filters.command("stats") & filters.user(int(Config.ADMIN_USER_ID)) if Config.ADMIN_USER_ID else filters.command("stats") & filters.private)
async def admin_stats(client, message):
    """Admin-only global statistics"""
    try:
        stats = stats_manager.stats
        uptime = time.time() - stats["start_time"]
        uptime_str = str(timedelta(seconds=int(uptime)))

        # Calculate today's stats
        today = datetime.now().strftime("%Y-%m-%d")
        today_stats = stats["daily_stats"].get(today, {"files": 0, "users": []})

        stats_msg = (
            f"📊 **Bot Statistics (Admin)**\n\n"
            f"⏰ **Uptime**: {uptime_str}\n"
            f"📁 **Total Files**: {stats['total_files_processed']}\n"
            f"🎧 **Audio Files**: {stats['audio_files']}\n"
            f"🎥 **Video Files**: {stats['video_files']}\n"
            f"💾 **Total Space Saved**: {format_file_size(stats['total_bytes_saved'])}\n"
            f"👥 **Total Users**: {len(stats['users'])}\n"
            f"❌ **Errors**: {stats['errors']}\n\n"
            f"📅 **Today's Activity**:\n"
            f"• Files processed: {today_stats['files']}\n"
            f"• Active users: {len(today_stats['users'])}\n\n"
            f"🔄 **Current Queue**: {len(processing_queue.queue)}\n"
            f"⚡ **Active Processes**: {len(processing_queue.active_processes)}\n\n"
            f"⚡ **Koyeb Status**: Active"
        )

        await message.reply_text(stats_msg)
    except Exception as e:
        logger.error(f"❌ Error in admin_stats: {e}")
        await message.reply_text("❌ Error retrieving statistics")

@app.on_message(filters.command("cleanup") & filters.user(int(Config.ADMIN_USER_ID)) if Config.ADMIN_USER_ID else filters.command("cleanup") & filters.private)
async def admin_cleanup(client, message):
    """Admin-only cleanup command"""
    try:
        # Clean old user states
        cleanup_old_states()

        # Clean temp directory
        try:
            temp_files = len([f for f in os.listdir(Config.TEMP_DIR) if f.startswith(('input.', 'compressed.', 'temp_'))])
            for file in os.listdir(Config.TEMP_DIR):
                if file.startswith(('input.', 'compressed.', 'temp_')):
                    try:
                        os.remove(os.path.join(Config.TEMP_DIR, file))
                    except:
                        pass
        except:
            temp_files = 0

        await message.reply_text(
            f"🧹 **Cleanup completed!**\n\n"
            f"• Cleaned {len(user_states)} user states\n"
            f"• Cleaned temp files: {temp_files}\n"
            f"• Queue cleared: {len(processing_queue.queue)} items\n\n"
            f"✅ System optimized!"
        )
    except Exception as e:
        logger.error(f"❌ Error in admin_cleanup: {e}")
        await message.reply_text("❌ Error during cleanup")

@app.on_message(filters.command("help"))
async def help_command(client, message):
    """Enhanced help command"""
    try:
        help_text = (
            "ℹ️ **Media Compressor Bot Help**\n\n"
            "⚡ **Powered by Koyeb for fast processing!**\n\n"
            "📋 **Commands:**\n"
            "• /start - Main menu and mode selection\n"
            "• /help - Show this help message\n\n"
            "🎯 **How to Use:**\n"
            "1. Use /start to choose compression mode\n"
            "2. Select quality preset (Ultra High to Ultra Low)\n"
            "3. Send your audio/video file\n"
            "4. Wait for processing\n"
            "5. Download compressed result\n\n"
            "⚡ **Koyeb Benefits:**\n"
            "• Fast serverless processing\n"
            "• Reliable compression\n"
            "• Multiple quality options\n"
            "• Smart optimization\n\n"
            "📊 **Supported Formats:**\n"
            "• Audio: MP3, AAC, OGG, FLAC, WAV\n"
            "• Video: MP4, AVI, MKV, MOV, WMV\n\n"
            "🔧 **Quality Presets:**\n"
            "• Ultra High: Best quality, larger size\n"
            "• High: Excellent quality, good size\n"
            "• Medium: Balanced quality/size\n"
            "• Low: Smaller size, good quality\n"
            "• Ultra Low: Smallest size, basic quality\n\n"
            "❓ **Need help?** Contact @kinkum1"
        )

        await message.reply_text(help_text)
    except Exception as e:
        logger.error(f"❌ Error in help_command: {e}")

def cleanup_old_states():
    """Clean up old user states"""
    current_time = time.time()
    expired_users = []
    for user_id, state in user_states.items():
        if current_time - state["last_activity"] > 3600:  # 1 hour timeout
            expired_users.append(user_id)

    for user_id in expired_users:
        user_states.pop(user_id, None)

    if expired_users:
        logger.info(f"Cleaned up {len(expired_users)} expired user states")

# Health check endpoint for Koyeb
from aiohttp import web
import aiohttp

async def health_check(request):
    """Health check endpoint for Koyeb"""
    return web.json_response({
        "status": "healthy",
        "bot_running": True,
        "ffmpeg_available": ffmpeg_available,
        "active_processes": len(processing_queue.active_processes)
    })

async def create_web_server():
    """Create web server for health checks"""
    try:
        app_web = web.Application()
        app_web.router.add_get('/health', health_check)
        app_web.router.add_get('/', health_check)
        
        runner = web.AppRunner(app_web)
        await runner.setup()
        
        site = web.TCPSite(runner, '0.0.0.0', Config.PORT)
        await site.start()
        logger.info(f"✅ Web server started on port {Config.PORT}")
    except Exception as e:
        logger.error(f"❌ Failed to start web server: {e}")

async def main():
    """Main function optimized for Koyeb deployment"""
    logger.info("🚀 Media Compressor Bot for Koyeb Starting...")
    logger.info("=" * 60)
    logger.info(f"📊 Max Audio Size: {format_file_size(Config.MAX_AUDIO_SIZE)}")
    logger.info(f"📊 Max Video Size: {format_file_size(Config.MAX_VIDEO_SIZE)}")
    logger.info(f"⚡ Max Concurrent Processes: {Config.MAX_CONCURRENT_PROCESSES}")
    logger.info(f"🔧 FFmpeg Available: {'✅' if ffmpeg_available else '❌'}")
    logger.info(f"🌐 Port: {Config.PORT}")
    logger.info("=" * 60)

    if not ffmpeg_available:
        logger.warning("⚠️ Warning: FFmpeg not available. Video compression will be disabled.")

    try:
        # Start web server for health checks
        await create_web_server()
        
        # Start bot
        logger.info("🟢 Starting Telegram bot...")
        await app.start()
        logger.info("✅ Bot started successfully!")
        logger.info("📱 Bot is ready to receive messages!")

        # Test bot connection
        try:
            me = await app.get_me()
            logger.info(f"✅ Bot authenticated as: @{me.username} ({me.first_name})")
        except Exception as e:
            logger.error(f"❌ Bot authentication failed: {e}")
            raise

        # Keep the bot running
        logger.info("🔄 Bot is running on Koyeb...")
        await asyncio.Event().wait()  # Run forever

    except Exception as e:
        logger.error(f"🔴 Bot error: {e}")
        stats_manager.stats["errors"] += 1
        stats_manager.save_stats()
        raise
    finally:
        logger.info("🧹 Shutting down...")
        try:
            await app.stop()
        except:
            pass
        stats_manager.save_stats()
        logger.info("✅ Shutdown completed!")

if __name__ == "__main__":
    # Install required packages if not already installed
    import subprocess
    import sys
    
    def install_package(package):
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])
            logger.info(f"✅ Installed {package}")
        except Exception as e:
            logger.error(f"❌ Failed to install {package}: {e}")
    
    required_packages = [
        "pyrogram",
        "tgcrypto", 
        "aiohttp",
        "pillow"
    ]
    
    logger.info("🔧 Checking required packages...")
    for package in required_packages:
        try:
            __import__(package.replace("-", "_"))
            logger.info(f"✅ {package} already installed")
        except ImportError:
            logger.info(f"📦 Installing {package}...")
            install_package(package)
    
    # Run the bot
    try:
        logger.info("🚀 Starting bot...")
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🔴 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Failed to start bot: {e}")
        sys.exit(1)
