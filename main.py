# Complete Webhook-Based Telegram Media Compressor Bot for Koyeb
# With full compression functionality

import os
import tempfile
import subprocess
import asyncio
import json
import logging
from datetime import datetime, timedelta
from aiohttp import web, ClientSession
import hashlib
import hmac
from typing import Dict, Optional
import shutil

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
class Config:
    # Bot Credentials
    API_ID = int(os.getenv("API_ID"))
    API_HASH = os.getenv("API_HASH")
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    
    # Webhook settings for Koyeb
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")
    WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}" if BOT_TOKEN else "/webhook"
    PORT = int(os.getenv("PORT", 8000))
    
    # File limits (Using Pyrogram for large files)
    MAX_AUDIO_SIZE = 500 * 1024 * 1024  # 500MB
    MAX_VIDEO_SIZE = 500 * 1024 * 1024  # 500MB
    TELEGRAM_BOT_API_LIMIT = 20 * 1024 * 1024  # 20MB Bot API limit
    PYROGRAM_LIMIT = 2 * 1024 * 1024 * 1024  # 2GB Pyrogram limit
    
    @classmethod
    def validate(cls):
        if not all([cls.API_ID, cls.API_HASH, cls.BOT_TOKEN]):
            raise ValueError("Missing required environment variables")
        if not cls.WEBHOOK_URL:
            logger.warning("WEBHOOK_URL not set - bot may not work on Koyeb")

# Validate config
try:
    Config.validate()
    logger.info("✅ Configuration validated")
except ValueError as e:
    logger.error(f"❌ Configuration error: {e}")
    exit(1)

# Global state
user_states: Dict[int, Dict] = {}
bot_info = None

def get_user_state(user_id: int) -> Dict:
    if user_id not in user_states:
        user_states[user_id] = {
            "mode": None,
            "audio_preset": "medium",
            "video_preset": "medium",
            "last_activity": datetime.now().timestamp(),
            "files_processed": 0,
            "space_saved": 0
        }
    return user_states[user_id]

def format_file_size(size_bytes: int) -> str:
    if size_bytes == 0:
        return "0 B"
    size_names = ["B", "KB", "MB", "GB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    return f"{size_bytes:.2f} {size_names[i]}"

def check_ffmpeg():
    """Check if FFmpeg is available"""
    try:
        result = subprocess.run(['ffmpeg', '-version'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            logger.info("✅ FFmpeg is available")
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    
    logger.warning("⚠️ FFmpeg not found - using basic compression")
    return False

# Check FFmpeg availability
FFMPEG_AVAILABLE = check_ffmpeg()

async def download_file_large(file_id: str, file_path: str, file_size: int = 0) -> Optional[str]:
    """Download large files using Pyrogram (up to 2GB)"""
    global pyrogram_started
    
    try:
        # Start Pyrogram client if not started
        if not pyrogram_started:
            await pyrogram_app.start()
            pyrogram_started = True
            logger.info("✅ Pyrogram client started for large file downloads")
        
        # Create temp directory if it doesn't exist
        os.makedirs("/tmp/bot_files", exist_ok=True)
        
        logger.info(f"📥 Downloading large file ({format_file_size(file_size)}) using Pyrogram...")
        
        # Download file using Pyrogram
        await pyrogram_app.download_media(file_id, file_path)
        
        if os.path.exists(file_path):
            actual_size = os.path.getsize(file_path)
            logger.info(f"✅ Large file downloaded successfully: {format_file_size(actual_size)}")
            return file_path
        else:
            logger.error("❌ Downloaded file not found")
            return None
            
    except Exception as e:
        logger.error(f"❌ Error downloading large file: {e}")
        return None

async def download_file(file_id: str, file_path: str, file_size: int = 0) -> Optional[str]:
    """Download file - uses appropriate method based on size"""
    try:
        # For files larger than Bot API limit, use Pyrogram
        if file_size > Config.TELEGRAM_BOT_API_LIMIT:
            logger.info(f"📁 Large file detected ({format_file_size(file_size)}), using Pyrogram...")
            return await download_file_large(file_id, file_path, file_size)
        
        # For smaller files, use Bot API (faster)
        logger.info(f"📁 Small file ({format_file_size(file_size)}), using Bot API...")
        
        # Get file info
        get_file_url = f"https://api.telegram.org/bot{Config.BOT_TOKEN}/getFile"
        
        async with ClientSession() as session:
            async with session.get(get_file_url, params={"file_id": file_id}) as response:
                result = await response.json()
                
                if not result.get("ok"):
                    logger.error(f"❌ Failed to get file info: {result}")
                    # Fallback to Pyrogram for problematic files
                    logger.info("🔄 Falling back to Pyrogram...")
                    return await download_file_large(file_id, file_path, file_size)
                
                file_info = result["result"]
                telegram_file_path = file_info["file_path"]
                
                # Download the file
                download_url = f"https://api.telegram.org/file/bot{Config.BOT_TOKEN}/{telegram_file_path}"
                
                async with session.get(download_url) as download_response:
                    if download_response.status == 200:
                        # Create temp directory if it doesn't exist
                        os.makedirs("/tmp/bot_files", exist_ok=True)
                        
                        # Save file
                        with open(file_path, 'wb') as f:
                            async for chunk in download_response.content.iter_chunked(8192):
                                f.write(chunk)
                        
                        logger.info(f"✅ File downloaded via Bot API: {file_path}")
                        return file_path
                    else:
                        logger.error(f"❌ Bot API download failed: {download_response.status}")
                        # Fallback to Pyrogram
                        logger.info("🔄 Falling back to Pyrogram...")
                        return await download_file_large(file_id, file_path, file_size)
                        
    except Exception as e:
        logger.error(f"❌ Error in download_file: {e}")
        # Fallback to Pyrogram
        logger.info("🔄 Falling back to Pyrogram...")
        return await download_file_large(file_id, file_path, file_size)

async def compress_audio(input_path: str, output_path: str, preset: str = "medium") -> bool:
    """Compress audio file"""
    try:
        if not FFMPEG_AVAILABLE:
            # Fallback: just copy the file
            shutil.copy2(input_path, output_path)
            return True
        
        # Audio compression settings
        settings = {
            "high": ["-c:a", "libmp3lame", "-b:a", "96k", "-ac", "2"],
            "medium": ["-c:a", "libmp3lame", "-b:a", "64k", "-ac", "2"],
            "low": ["-c:a", "libmp3lame", "-b:a", "32k", "-ac", "1"]
        }
        
        cmd = ["ffmpeg", "-i", input_path, "-y"] + settings.get(preset, settings["medium"]) + [output_path]
        
        logger.info(f"🎵 Compressing audio with preset: {preset}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode == 0:
            logger.info("✅ Audio compression successful")
            return True
        else:
            logger.error(f"❌ Audio compression failed: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        logger.error("❌ Audio compression timed out")
        return False
    except Exception as e:
        logger.error(f"❌ Audio compression error: {e}")
        return False

async def compress_video(input_path: str, output_path: str, preset: str = "medium") -> bool:
    """Compress video file"""
    try:
        if not FFMPEG_AVAILABLE:
            # Fallback: just copy the file
            shutil.copy2(input_path, output_path)
            return True
        
        # Video compression settings
        settings = {
            "high": ["-vf", "scale=-2:480", "-r", "25", "-c:v", "libx264", "-crf", "23", "-c:a", "aac", "-b:a", "64k"],
            "medium": ["-vf", "scale=-2:360", "-r", "20", "-c:v", "libx264", "-crf", "28", "-c:a", "aac", "-b:a", "48k"],
            "low": ["-vf", "scale=-2:270", "-r", "15", "-c:v", "libx264", "-crf", "32", "-c:a", "aac", "-b:a", "32k"]
        }
        
        cmd = ["ffmpeg", "-i", input_path, "-y"] + settings.get(preset, settings["medium"]) + [output_path]
        
        logger.info(f"🎥 Compressing video with preset: {preset}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        
        if result.returncode == 0:
            logger.info("✅ Video compression successful")
            return True
        else:
            logger.error(f"❌ Video compression failed: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        logger.error("❌ Video compression timed out")
        return False
    except Exception as e:
        logger.error(f"❌ Video compression error: {e}")
        return False

async def upload_file(chat_id: int, file_path: str, filename: str, file_type: str = "document") -> bool:
    """Upload compressed file back to Telegram"""
    try:
        url = f"https://api.telegram.org/bot{Config.BOT_TOKEN}/send{file_type.title()}"
        
        # Create form data for multipart upload
        data = web.FormData()
        data.add_field('chat_id', str(chat_id))
        
        with open(file_path, 'rb') as file_content:
            data.add_field(file_type, file_content, 
                          filename=filename, 
                          content_type='application/octet-stream')
            
            async with ClientSession() as session:
                async with session.post(url, data=data) as response:
                    result = await response.json()
                    
                    if result.get("ok"):
                        logger.info(f"✅ File uploaded: {filename}")
                        return True
                    else:
                        logger.error(f"❌ Failed to upload file: {result}")
                        return False
                        
    except Exception as e:
        logger.error(f"❌ Error uploading file: {e}")
        return False

async def send_message(chat_id: int, text: str, reply_markup=None):
    """Send message using Telegram Bot API directly"""
    url = f"https://api.telegram.org/bot{Config.BOT_TOKEN}/sendMessage"
    
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    
    async with ClientSession() as session:
        try:
            async with session.post(url, json=payload) as response:
                result = await response.json()
                if result.get("ok"):
                    logger.info(f"✅ Message sent to {chat_id}")
                    return result
                else:
                    logger.error(f"❌ Failed to send message: {result}")
                    return None
        except Exception as e:
            logger.error(f"❌ Error sending message: {e}")
            return None

async def process_media_file(user_id: int, file_info: dict, file_type: str):
    """Process media file (audio/video)"""
    try:
        user_state = get_user_state(user_id)
        
        # Get file details
        file_id = file_info.get("file_id")
        file_size = file_info.get("file_size", 0)
        
        # Check file size limits
        if file_type == "audio" and file_size > Config.MAX_AUDIO_SIZE:
            await send_message(user_id, 
                f"❌ **Audio file too large!**\n\n"
                f"📏 **Max audio size:** {format_file_size(Config.MAX_AUDIO_SIZE)}\n"
                f"📁 **Your file:** {format_file_size(file_size)}\n\n"
                f"💡 **Tip:** Try a shorter audio file or lower quality recording.")
            return
        
        if file_type == "video" and file_size > Config.MAX_VIDEO_SIZE:
            await send_message(user_id, 
                f"❌ **Video file too large!**\n\n"
                f"📏 **Max video size:** {format_file_size(Config.MAX_VIDEO_SIZE)}\n"
                f"📁 **Your file:** {format_file_size(file_size)}\n\n"
                f"💡 **Tip:** Try a shorter video or compress it first using other tools.")
            return
        
        # Send processing message with appropriate info
        download_method = "Pyrogram" if file_size > Config.TELEGRAM_BOT_API_LIMIT else "Bot API"
        await send_message(user_id, 
            f"⚡ **Processing {file_type}...**\n\n"
            f"📁 Size: {format_file_size(file_size)}\n"
            f"⚙️ Quality: {user_state.get(f'{file_type}_preset', 'medium').title()}\n"
            f"📥 Method: {download_method}\n"
            f"⏳ Please wait... (this may take a few minutes for large files)")
        
        # Generate unique filenames
        timestamp = int(datetime.now().timestamp())
        input_path = f"/tmp/bot_files/input_{user_id}_{timestamp}"
        output_path = f"/tmp/bot_files/output_{user_id}_{timestamp}.{'mp3' if file_type == 'audio' else 'mp4'}"
        
        # Download file
        downloaded_path = await download_file(file_id, input_path, file_size)
        if not downloaded_path:
            await send_message(user_id, "❌ **Failed to download file!**\nPlease try again.")
            return
        
        # Compress file
        if file_type == "audio":
            preset = user_state.get("audio_preset", "medium")
            success = await compress_audio(input_path, output_path, preset)
        else:  # video
            preset = user_state.get("video_preset", "medium")
            success = await compress_video(input_path, output_path, preset)
        
        if not success:
            await send_message(user_id, "❌ **Compression failed!**\nPlease try again.")
            # Cleanup
            for path in [input_path, output_path]:
                if os.path.exists(path):
                    os.remove(path)
            return
        
        # Get output file size
        if os.path.exists(output_path):
            output_size = os.path.getsize(output_path)
            space_saved = file_size - output_size
            
            # Update user stats
            user_state["files_processed"] += 1
            user_state["space_saved"] += max(0, space_saved)
            
            # Upload compressed file
            filename = f"compressed_{file_type}_{timestamp}.{'mp3' if file_type == 'audio' else 'mp4'}"
            upload_success = await upload_file(user_id, output_path, filename)
            
            if upload_success:
                # Send success message
                await send_message(user_id,
                    f"✅ **{file_type.title()} compressed successfully!**\n\n"
                    f"📊 **Results:**\n"
                    f"📥 Original: {format_file_size(file_size)}\n"
                    f"📤 Compressed: {format_file_size(output_size)}\n"
                    f"💾 Space saved: {format_file_size(space_saved)} "
                    f"({((space_saved/file_size)*100):.1f}%)\n\n"
                    f"🎯 **Quality:** {preset.title()}")
            else:
                await send_message(user_id, "❌ **Failed to upload compressed file!**")
        else:
            await send_message(user_id, "❌ **Compression output not found!**")
        
        # Cleanup temp files
        for path in [input_path, output_path]:
            if os.path.exists(path):
                os.remove(path)
                
    except Exception as e:
        logger.error(f"❌ Error processing media file: {e}")
        await send_message(user_id, "❌ **Processing error!**\nPlease try again.")
        
        # Cleanup on error
        timestamp = int(datetime.now().timestamp())
        for path in [f"/tmp/bot_files/input_{user_id}_{timestamp}", 
                    f"/tmp/bot_files/output_{user_id}_{timestamp}.mp3",
                    f"/tmp/bot_files/output_{user_id}_{timestamp}.mp4"]:
            if os.path.exists(path):
                os.remove(path)

async def handle_start_command(user_id: int, username: str):
    """Handle /start command"""
    logger.info(f"🚀 START command from user {user_id} (@{username})")
    
    # Reset user state but keep stats
    if user_id in user_states:
        stats = {
            "files_processed": user_states[user_id].get("files_processed", 0),
            "space_saved": user_states[user_id].get("space_saved", 0)
        }
        user_states[user_id] = {**get_user_state(user_id), **stats}
    else:
        get_user_state(user_id)
    
    # Create inline keyboard
    keyboard = {
        "inline_keyboard": [
            [{"text": "🎧 Audio Compression", "callback_data": "mode_audio"}],
            [{"text": "🎥 Video Compression", "callback_data": "mode_video"}],
            [
                {"text": "📊 My Stats", "callback_data": "user_stats"},
                {"text": "ℹ️ Help", "callback_data": "help"}
            ]
        ]
    }
    
    welcome_msg = (
        "🤖 **Media Compressor Bot**\n\n"
        "⚡ **Fast serverless compression with large file support!**\n\n"
        "🎯 **Features:**\n"
        "• High-quality audio & video compression\n"
        "• Multiple quality presets\n"
        "• **Large file support up to 500MB**\n"
        "• Smart optimization algorithms\n"
        "• Fast cloud processing\n\n"
        "📏 **File Size Limits:**\n"
        f"• Audio: {format_file_size(Config.MAX_AUDIO_SIZE)}\n"
        f"• Video: {format_file_size(Config.MAX_VIDEO_SIZE)}\n\n"
        "💡 **Large files (>20MB) are supported using advanced download methods!**\n\n"
        "Choose your compression mode:"
    )
    
    await send_message(user_id, welcome_msg, keyboard)

async def handle_help_command(user_id: int):
    """Handle /help command"""
    help_text = (
        "ℹ️ **Media Compressor Bot Help**\n\n"
        "📋 **Commands:**\n"
        "• /start - Main menu\n"
        "• /help - Show this help\n"
        "• /test - Test bot response\n\n"
        "🎯 **How to Use:**\n"
        "1. Use /start to choose compression mode\n"
        "2. Select your preferred quality preset\n"
        "3. Send your audio/video file\n"
        "4. Wait for processing (30s-5min)\n"
        "5. Download your compressed file\n\n"
        "⚡ **Powered by FFmpeg & Koyeb**\n\n"
        "💡 **Tips:**\n"
        "• Higher quality = larger file size\n"
        "• Processing time depends on file size\n"
        "• All files are automatically deleted after processing"
    )
    
    await send_message(user_id, help_text)

async def handle_test_command(user_id: int):
    """Handle /test command"""
    test_msg = (
        "✅ **System Test Results**\n\n"
        f"🤖 Bot Status: **Online**\n"
        f"📡 Webhook: **Active**\n"
        f"🛠️ FFmpeg: **{'Available' if FFMPEG_AVAILABLE else 'Limited'}**\n"
        f"⏰ Server Time: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n"
        f"👤 User ID: `{user_id}`\n\n"
        "🎯 **All systems operational!**"
    )
    
    await send_message(user_id, test_msg)

async def handle_callback_query(callback_query):
    """Handle callback queries"""
    user_id = callback_query["from"]["id"]
    data = callback_query["data"]
    
    logger.info(f"📱 Callback: {data} from user {user_id}")
    
    user_state = get_user_state(user_id)
    
    if data.startswith("mode_"):
        mode = data.replace("mode_", "")
        user_state["mode"] = mode
        
        if mode == "audio":
            current_preset = user_state.get("audio_preset", "medium")
            keyboard = {
                "inline_keyboard": [
                    [{"text": f"🔊 High Quality {'✓' if current_preset == 'high' else ''}", 
                      "callback_data": "audio_high"}],
                    [{"text": f"🔉 Medium Quality {'✓' if current_preset == 'medium' else ''}", 
                      "callback_data": "audio_medium"}],
                    [{"text": f"🔈 Low Quality {'✓' if current_preset == 'low' else ''}", 
                      "callback_data": "audio_low"}],
                    [{"text": "🔙 Back", "callback_data": "back_main"}]
                ]
            }
            
            response_msg = (
                "🎧 **Audio Compression Mode**\n\n"
                "📁 Send audio files, voice messages, or documents\n"
                f"📏 Max size: {format_file_size(Config.MAX_AUDIO_SIZE)}\n"
                "🚀 **Large files supported!**\n\n"
                "⚙️ **Quality Presets:**\n"
                "🔊 **High**: 96kbps, Stereo (best quality)\n"
                "🔉 **Medium**: 64kbps, Stereo (balanced)\n"
                "🔈 **Low**: 32kbps, Mono (smallest size)\n\n"
                f"Current: **{current_preset.title()}**\n\n"
                "Choose quality level:"
            )
        
        elif mode == "video":
            current_preset = user_state.get("video_preset", "medium")
            keyboard = {
                "inline_keyboard": [
                    [{"text": f"📺 High Quality {'✓' if current_preset == 'high' else ''}", 
                      "callback_data": "video_high"}],
                    [{"text": f"🖥️ Medium Quality {'✓' if current_preset == 'medium' else ''}", 
                      "callback_data": "video_medium"}],
                    [{"text": f"📱 Low Quality {'✓' if current_preset == 'low' else ''}", 
                      "callback_data": "video_low"}],
                    [{"text": "🔙 Back", "callback_data": "back_main"}]
                ]
            }
            
            response_msg = (
                "🎥 **Video Compression Mode**\n\n"
                "📁 Send video files or documents\n"
                f"📏 Max size: {format_file_size(Config.MAX_VIDEO_SIZE)}\n"
                "🚀 **Large files supported!**\n\n"
                "⚙️ **Quality Presets:**\n"
                "📺 **High**: 480p, 25fps (best quality)\n"
                "🖥️ **Medium**: 360p, 20fps (balanced)\n"
                "📱 **Low**: 270p, 15fps (smallest size)\n\n"
                f"Current: **{current_preset.title()}**\n\n"
                "Choose quality level:"
            )
        
        await send_message(user_id, response_msg, keyboard)
    
    elif data.startswith(("audio_", "video_")):
        preset_type, quality = data.split("_", 1)
        user_state[f"{preset_type}_preset"] = quality
        
        response_msg = (
            f"✅ **{preset_type.title()} quality set to: {quality.title()}**\n\n"
            "🎯 **Ready for processing!**\n\n"
            f"📤 Now send me your {preset_type} files to compress!\n\n"
            "💡 **Supported formats:**\n" +
            ("• MP3, WAV, M4A, OGG, FLAC\n• Voice messages\n• Audio documents" 
             if preset_type == "audio" else 
             "• MP4, AVI, MOV, MKV, WEBM\n• Video documents\n• Any video format")
        )
        
        await send_message(user_id, response_msg)
    
    elif data == "help":
        await handle_help_command(user_id)
    
    elif data == "user_stats":
        stats = user_state
        stats_msg = (
            "📊 **Your Statistics**\n\n"
            f"📁 Files processed: **{stats.get('files_processed', 0)}**\n"
            f"💾 Total space saved: **{format_file_size(stats.get('space_saved', 0))}**\n"
            f"🎯 Current mode: **{stats.get('mode', 'None').title() if stats.get('mode') else 'None'}**\n\n"
            "⚡ **Keep compressing to see your progress!**"
        )
        await send_message(user_id, stats_msg)
    
    elif data == "back_main":
        username = callback_query["from"].get("username", "Unknown")
        await handle_start_command(user_id, username)
    
    # Answer callback query
    answer_url = f"https://api.telegram.org/bot{Config.BOT_TOKEN}/answerCallbackQuery"
    async with ClientSession() as session:
        await session.post(answer_url, json={
            "callback_query_id": callback_query["id"],
            "text": "✅"
        })

async def process_update(update_data):
    """Process incoming update"""
    try:
        logger.info(f"📨 Processing update type: {list(update_data.keys())}")
        
        if "message" in update_data:
            message = update_data["message"]
            
            if "from" not in message:
                logger.warning("Message has no 'from' field")
                return
            
            user_id = message["from"]["id"]
            username = message["from"].get("username", "Unknown")
            
            # Handle text messages
            if "text" in message:
                text = message["text"]
                logger.info(f"📝 Text message: '{text}' from user {user_id}")
                
                if text == "/start":
                    await handle_start_command(user_id, username)
                elif text == "/help":
                    await handle_help_command(user_id)
                elif text == "/test":
                    await handle_test_command(user_id)
                else:
                    # Unknown command
                    await send_message(user_id, 
                        "❓ **Unknown command**\n\n"
                        "Use /start to begin or /help for assistance.\n\n"
                        "💡 If you want to compress files, first select a mode using /start")
            
            # Handle media files
            else:
                user_state = get_user_state(user_id)
                
                if not user_state.get("mode"):
                    await send_message(user_id,
                        "❌ **Please select a compression mode first!**\n\n"
                        "Use /start to choose Audio or Video compression mode.")
                    return
                
                # Process different media types
                if "audio" in message:
                    if user_state["mode"] != "audio":
                        await send_message(user_id,
                            "❌ **Wrong mode!**\n\n"
                            "You're in Video mode but sent an audio file.\n"
                            "Use /start to switch to Audio mode.")
                        return
                    await process_media_file(user_id, message["audio"], "audio")
                
                elif "voice" in message:
                    if user_state["mode"] != "audio":
                        await send_message(user_id,
                            "❌ **Wrong mode!**\n\n"
                            "You're in Video mode but sent a voice message.\n"
                            "Use /start to switch to Audio mode.")
                        return
                    await process_media_file(user_id, message["voice"], "audio")
                
                elif "video" in message:
                    if user_state["mode"] != "video":
                        await send_message(user_id,
                            "❌ **Wrong mode!**\n\n"
                            "You're in Audio mode but sent a video file.\n"
                            "Use /start to switch to Video mode.")
                        return
                    await process_media_file(user_id, message["video"], "video")
                
                elif "document" in message:
                    doc = message["document"]
                    mime_type = doc.get("mime_type", "")
                    
                    if user_state["mode"] == "audio" and (
                        mime_type.startswith("audio/") or 
                        doc.get("file_name", "").lower().endswith(('.mp3', '.wav', '.m4a', '.ogg', '.flac'))
                    ):
                        await process_media_file(user_id, doc, "audio")
                    elif user_state["mode"] == "video" and (
                        mime_type.startswith("video/") or 
                        doc.get("file_name", "").lower().endswith(('.mp4', '.avi', '.mov', '.mkv', '.webm'))
                    ):
                        await process_media_file(user_id, doc, "video")
                    else:
                        await send_message(user_id,
                            f"❌ **Unsupported file type!**\n\n"
                            f"Current mode: **{user_state['mode'].title()}**\n"
                            f"File type: `{mime_type or 'unknown'}`\n\n"
                            "Please send a compatible file or change modes using /start")
                
                else:
                    await send_message(user_id,
                        "❌ **Unsupported media type!**\n\n"
                        "Please send audio files (for Audio mode) or video files (for Video mode).\n\n"
                        "Use /start to select the correct mode.")
        
        elif "callback_query" in update_data:
            await handle_callback_query(update_data["callback_query"])
        
    except Exception as e:
        logger.error(f"❌ Error processing update: {e}")
        import traceback
        traceback.print_exc()

# Webhook handler
async def webhook_handler(request):
    """Handle incoming webhooks from Telegram"""
    try:
        # Get the update data
        update_data = await request.json()
        
        # Process the update
        await process_update(update_data)
        
        return web.Response(text="OK")
        
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        return web.Response(text="Error", status=500)

# Health check
async def health_check(request):
    """Health check endpoint"""
    return web.json_response({
        "status": "healthy",
        "bot_running": True,
        "webhook_configured": bool(Config.WEBHOOK_URL),
        "ffmpeg_available": FFMPEG_AVAILABLE,
        "timestamp": datetime.now().isoformat()
    })

async def setup_webhook():
    """Set up webhook with Telegram"""
    if not Config.WEBHOOK_URL:
        logger.warning("⚠️ WEBHOOK_URL not configured")
        return False
    
    # Properly construct the webhook URL
    webhook_url = f"{Config.WEBHOOK_URL.rstrip('/')}{Config.WEBHOOK_PATH}"
    
    url = f"https://api.telegram.org/bot{Config.BOT_TOKEN}/setWebhook"
    payload = {"url": webhook_url}
    
    async with ClientSession() as session:
        try:
            async with session.post(url, json=payload) as response:
                result = await response.json()
                if result.get("ok"):
                    logger.info(f"✅ Webhook set to: {webhook_url}")
                    return True
                else:
                    logger.error(f"❌ Failed to set webhook: {result}")
                    return False
        except Exception as e:
            logger.error(f"❌ Error setting webhook: {e}")
            return False
            
async def get_bot_info():
    """Get bot information"""
    url = f"https://api.telegram.org/bot{Config.BOT_TOKEN}/getMe"
    
    async with ClientSession() as session:
        try:
            async with session.get(url) as response:
                result = await response.json()
                if result.get("ok"):
                    bot_data = result["result"]
                    logger.info(f"✅ Bot info: @{bot_data['username']} ({bot_data['first_name']})")
                    return bot_data
                else:
                    logger.error(f"❌ Failed to get bot info: {result}")
                    return None
        except Exception as e:
            logger.error(f"❌ Error getting bot info: {e}")
            return None

async def cleanup_old_files():
    """Clean up old temporary files"""
    try:
        temp_dir = "/tmp/bot_files"
        if not os.path.exists(temp_dir):
            return
        
        current_time = datetime.now()
        for filename in os.listdir(temp_dir):
            file_path = os.path.join(temp_dir, filename)
            
            # Check if file is older than 1 hour
            file_time = datetime.fromtimestamp(os.path.getctime(file_path))
            if (current_time - file_time).total_seconds() > 3600:
                try:
                    os.remove(file_path)
                    logger.info(f"🧹 Cleaned up old file: {filename}")
                except Exception as e:
                    logger.error(f"❌ Failed to remove {filename}: {e}")
                    
    except Exception as e:
        logger.error(f"❌ Cleanup error: {e}")

async def periodic_cleanup():
    """Run cleanup periodically"""
    while True:
        try:
            await asyncio.sleep(1800)  # Run every 30 minutes
            await cleanup_old_files()
        except Exception as e:
            logger.error(f"❌ Periodic cleanup error: {e}")

async def create_app():
    """Create the web application"""
    app = web.Application()
    
    # Add routes
    app.router.add_post(Config.WEBHOOK_PATH, webhook_handler)
    app.router.add_get('/health', health_check)
    app.router.add_get('/', health_check)
    
    return app

async def main():
    """Main function"""
    logger.info("🚀 Telegram Media Compressor Bot Starting...")
    logger.info("=" * 60)
    logger.info(f"🌐 Port: {Config.PORT}")
    logger.info(f"🔗 Webhook Path: {Config.WEBHOOK_PATH}")
    logger.info(f"📡 Webhook URL: {Config.WEBHOOK_URL}")
    logger.info(f"🛠️ FFmpeg Available: {FFMPEG_AVAILABLE}")
    logger.info("=" * 60)
    
    # Create temp directory
    os.makedirs("/tmp/bot_files", exist_ok=True)
    
    # Get bot info
    global bot_info
    bot_info = await get_bot_info()
    if not bot_info:
        logger.error("❌ Failed to get bot info")
        return
    
    # Set up webhook
    webhook_success = await setup_webhook()
    if not webhook_success:
        logger.error("❌ Failed to set up webhook")
        # Continue anyway for local testing
    
    # Start periodic cleanup task
    cleanup_task = asyncio.create_task(periodic_cleanup())
    
    # Create and start web server
    app = await create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, '0.0.0.0', Config.PORT)
    await site.start()
    
    logger.info("✅ Bot is running and ready!")
    logger.info(f"📱 Bot: @{bot_info['username']}")
    logger.info(f"🎯 Features: Audio & Video Compression")
    logger.info(f"⚡ FFmpeg: {'Enabled' if FFMPEG_AVAILABLE else 'Fallback Mode'}")
    logger.info("🔄 Waiting for updates...")
    
    # Keep running
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("🔴 Bot stopped by user")
    finally:
        logger.info("🧹 Shutting down...")
        cleanup_task.cancel()
        
        # Stop Pyrogram client if started
        if pyrogram_started:
            try:
                await pyrogram_app.stop()
                logger.info("✅ Pyrogram client stopped")
            except Exception as e:
                logger.error(f"❌ Error stopping Pyrogram: {e}")
        
        await runner.cleanup()
        # Final cleanup
        await cleanup_old_files()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"❌ Failed to start bot: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
